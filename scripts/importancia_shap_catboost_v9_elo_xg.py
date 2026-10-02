#!/usr/bin/env python3
"""Importância de variáveis, SHAP e interações do `catboost_v9` + Elo global por xG.

Pergunta: com quais variáveis o CatBoost mais se apoia (e combina) quando recebe o Elo por
xG (`elo_xg_home`/`elo_xg_away`/`elo_xg_diff`, ver `dados_historicos.FEATURES_NUMERICAS_ELO_XG`)?
Usa os MESMOS 3 folds walk-forward de `comparar_catboost_v9_elo_xg.py` (teste 2023/2024/2025),
o treinador de produção `modelos_ml.treinar_catboost` e parada antecipada só na validação.

Para cada fold, medidas calculadas SÓ no conjunto de teste do fold (nada do teste entra no treino):
  - `PredictionValuesChange` (PVC): quanto a previsão muda quando a variável muda (não usa dados).
  - `LossFunctionChange` (LFC): quanto o log-loss piora sem a variável (usa o teste).
  - SHAP: contribuição média absoluta de cada variável para cada classe (casa/empate/fora),
    em log-odds; a soma nas 3 classes dá a importância agregada.
  - Interações SHAP: efeito que só existe quando duas variáveis se combinam, numa amostra
    aleatória do teste (a matriz é pesada: jogos × 3 × V × V).
Entre folds: correlação de Spearman do ranking e sobreposição do top-N, para dizer se a
leitura é estável ou ruído de um fold só.

LEITURA: `elo_xg_diff = elo_xg_home - elo_xg_away`, então a importância se reparte entre as
três; olhe também o GRUPO. Importância mostra no que o modelo se apoia, não que isso gere
vantagem sobre o mercado (ver CONTEXTO_PROJETO.md: o modelo segue atrás da Pinnacle).

NÃO grava no banco, NÃO registra modelo e NÃO mexe na produção. Saída: console + JSON/CSV.

Uso:
    python scripts/importancia_shap_catboost_v9_elo_xg.py --saida-dir saida_shap
    python scripts/importancia_shap_catboost_v9_elo_xg.py --dataset dataset_com_elo_xg.pkl   # pula a montagem (~40 min)
Variáveis de ambiente (só sem --dataset): SUPABASE_URL, SUPABASE_KEY.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dados_historicos as dh
import modelos_ml as ml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("importancia_shap_catboost_v9_elo_xg")

FOLDS = [
    {"nome": "fold_1", "treino_max_ano": 2021, "val_ano": 2022, "test_ano": 2023},
    {"nome": "fold_2", "treino_max_ano": 2022, "val_ano": 2023, "test_ano": 2024},
    {"nome": "fold_3", "treino_max_ano": 2023, "val_ano": 2024, "test_ano": 2025},
]
MODELO = "catboost_v9"
COLUNA_ALVO = "resultado"
CLASSES = ("casa", "empate", "fora")  # ordem das classes 0/1/2 do alvo
TAMANHO_AMOSTRA_INTERACAO = 400


# --------------------------------------------------------------------------- funções puras
def montar_pool(modelo_df: pd.DataFrame, features: list[str]):
    """Pool do CatBoost com a coluna de liga tratada como categórica (igual ao treino)."""
    from catboost import Pool

    prep = ml.preparar_liga_para_catboost(modelo_df)
    cats = [features.index(c) for c in ml.CAT_FEATURES if c in features]
    return Pool(prep[features], prep[COLUNA_ALVO], cat_features=cats), prep


def tabela_importancia(modelo, pool, features: list[str]) -> tuple[pd.DataFrame, np.ndarray]:
    """Uma linha por variável: PVC, LFC, SHAP médio |.| por classe, SHAP total e % do total.
    Devolve também o array SHAP bruto (jogos, classes, variáveis) sem a coluna do valor-base."""
    sv = np.asarray(modelo.get_feature_importance(pool, type="ShapValues"))
    sv = sv[:, :, :-1]  # a última coluna é o valor esperado (ponto de partida), não uma variável
    por_classe = np.abs(sv).mean(axis=0)  # (classes, variáveis)
    df = pd.DataFrame({
        "variavel": features,
        "pvc": modelo.get_feature_importance(type="PredictionValuesChange"),
        "lfc": modelo.get_feature_importance(pool, type="LossFunctionChange"),
        **{f"shap_{c}": por_classe[k] for k, c in enumerate(CLASSES)},
    })
    df["shap_total"] = por_classe.sum(axis=0)
    df["shap_pct"] = df["shap_total"] / df["shap_total"].sum()
    return df.sort_values("shap_total", ascending=False).reset_index(drop=True), sv


def direcao(sv: np.ndarray, X: pd.DataFrame, features: list[str], variaveis: list[str]) -> list[dict]:
    """Correlação entre o valor da variável e o seu SHAP em cada classe (sinal = direção do efeito)."""
    saida = []
    for v in variaveis:
        x = X[v].to_numpy(dtype=float)
        ok = ~np.isnan(x)
        j = features.index(v)
        saida.append({"variavel": v, **{
            f"corr_{c}": float(np.corrcoef(x[ok], sv[ok, k, j])[0, 1]) if ok.sum() > 2 and np.ptp(sv[ok, k, j]) > 0 else float("nan")
            for k, c in enumerate(CLASSES)}})
    return saida


def matriz_interacao(modelo, pool_amostra, n_variaveis: int) -> tuple[np.ndarray, np.ndarray]:
    """(efeito principal por variável, matriz de interação |.| média fora da diagonal), ambos somados nas classes."""
    si = np.asarray(modelo.get_feature_importance(pool_amostra, type="ShapInteractionValues"))
    si = si[:, :, :n_variaveis, :n_variaveis]
    absmed = np.abs(si).mean(axis=0).sum(axis=0)  # (V, V)
    principal = np.diag(absmed).copy()
    inter = absmed.copy()
    np.fill_diagonal(inter, 0.0)
    return principal, inter


def melhores_parceiros(inter: np.ndarray, features: list[str], variavel: str, n: int = 5) -> list[tuple[str, float]]:
    linha = pd.Series(inter[features.index(variavel)], index=features).sort_values(ascending=False)
    return [(k, float(v)) for k, v in linha.iloc[:n].items()]


def pares_mais_fortes(inter: np.ndarray, features: list[str], n: int = 10) -> list[tuple[str, float]]:
    a, b = np.triu_indices(len(features), 1)
    s = pd.Series(inter[a, b], index=[f"{features[i]} x {features[j]}" for i, j in zip(a, b)]).sort_values(ascending=False)
    return [(k, float(v)) for k, v in s.iloc[:n].items()]


def estabilidade_entre_folds(tabelas: dict[str, pd.DataFrame], top_n: int = 15) -> dict:
    """Spearman do SHAP total entre pares de folds e quantas das top-N variáveis se repetem."""
    from scipy.stats import spearmanr

    nomes = list(tabelas)
    series = {n: tabelas[n].set_index("variavel")["shap_total"] for n in nomes}
    saida = {"spearman": {}, "top_n": top_n, "sobreposicao_top_n": {}}
    for i in range(len(nomes)):
        for j in range(i + 1, len(nomes)):
            a, b = series[nomes[i]], series[nomes[j]].reindex(series[nomes[i]].index)
            saida["spearman"][f"{nomes[i]}~{nomes[j]}"] = float(spearmanr(a.to_numpy(), b.to_numpy())[0])
            ta, tb = set(a.nlargest(top_n).index), set(series[nomes[j]].nlargest(top_n).index)
            saida["sobreposicao_top_n"][f"{nomes[i]}~{nomes[j]}"] = len(ta & tb)
    return saida


# --------------------------------------------------------------------------- execução
def carregar_dataset(caminho: str | None) -> pd.DataFrame:
    if caminho:
        return pd.read_pickle(caminho).reset_index(drop=True)
    from supabase import create_client

    url, chave = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")
    if not url or not chave:
        sys.exit("Defina SUPABASE_URL e SUPABASE_KEY (ou passe --dataset).")
    logger.info("Montando o dataset empilhado (~40 min)...")
    return dh.montar_dataset_ml_empilhado(create_client(url, chave), anos_por_liga=7).reset_index(drop=True)


def rodar_fold(ds: pd.DataFrame, anos: pd.Series, fold: dict, features: list[str], semente: int) -> dict:
    train, val, test = ds[anos <= fold["treino_max_ano"]], ds[anos == fold["val_ano"]], ds[anos == fold["test_ano"]]
    logger.info("%s | treino=%d val=%d teste=%d | %d variáveis", fold["nome"], len(train), len(val), len(test), len(features))
    # test_df=val: a parada antecipada NÃO enxerga o teste
    modelo, _, _ = ml.treinar_catboost(ml.PARAMS_DEFAULT[MODELO], train, coluna_alvo=COLUNA_ALVO, features=features, val_df=val, test_df=val)
    pool, prep = montar_pool(test, features)
    tabela, sv = tabela_importancia(modelo, pool, features)
    amostra = np.sort(np.random.default_rng(semente).choice(len(prep), min(TAMANHO_AMOSTRA_INTERACAO, len(prep)), replace=False))
    pool_i, _ = montar_pool(test.iloc[amostra], features)
    principal, inter = matriz_interacao(modelo, pool_i, len(features))
    return {"fold": fold["nome"], "n_teste": int(len(test)), "iteracao_otima": int(modelo.get_best_iteration()),
            "tabela": tabela, "sv": sv, "X": prep[features].reset_index(drop=True), "principal": principal, "inter": inter}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", help="pickle do dataset já montado, com as colunas elo_xg_*")
    ap.add_argument("--saida-dir", default="saida_shap")
    ap.add_argument("--semente", type=int, default=0, help="semente da amostra de jogos para as interações")
    args = ap.parse_args()

    ds = carregar_dataset(args.dataset)
    faltando = [c for c in dh.FEATURES_NUMERICAS_ELO_XG if c not in ds.columns]
    if faltando:
        sys.exit(f"Colunas ausentes do dataset: {faltando}")
    base = [f for f in ml.FEATURES_POR_MODELO[MODELO] if f in ds.columns]
    features = base + [f for f in dh.FEATURES_NUMERICAS_ELO_XG if f not in base]
    anos = pd.to_datetime(ds["match_date"]).dt.year
    elo = list(dh.FEATURES_NUMERICAS_ELO_XG)

    resultados = [rodar_fold(ds, anos, f, features, args.semente) for f in FOLDS]
    os.makedirs(args.saida_dir, exist_ok=True)
    resumo = {"features": features, "folds": []}
    for r in resultados:
        t = r["tabela"].set_index("variavel")
        pos = t["shap_total"].rank(ascending=False).astype(int)
        print(f"\n=== {r['fold']} (teste {r['n_teste']} jogos, iteração ótima {r['iteracao_otima']}) ===")
        print(f"{'#':>3} {'variável':<46}{'SHAP':>8}{'% total':>9}{'PVC %':>8}{'LFC':>10}   casa/empate/fora")
        for i, (v, lin) in enumerate(r["tabela"].head(10).set_index("variavel").iterrows(), 1):
            print(f"{i:>3} {v:<46}{lin.shap_total:8.4f}{lin.shap_pct:9.1%}{lin.pvc:8.2f}{lin.lfc:+10.5f}   {lin.shap_casa:.3f}/{lin.shap_empate:.3f}/{lin.shap_fora:.3f}")
        print("Elo por xG: " + " | ".join(f"{v} pos {pos[v]} ({t.loc[v, 'shap_pct']:.1%})" for v in elo)
              + f" | grupo {t.loc[elo, 'shap_pct'].sum():.1%}")
        parceiros = {v: melhores_parceiros(r["inter"], features, v, 4) for v in elo}
        for v in elo:
            print(f"  interações de {v}: " + " | ".join(f"{k} {x:.4f}" for k, x in parceiros[v]))
        principal = float(r["principal"][features.index("elo_xg_diff")])
        print(f"  elo_xg_diff: efeito principal {principal:.4f} vs soma das interações {r['inter'][features.index('elo_xg_diff')].sum():.4f}")
        r["tabela"].to_csv(os.path.join(args.saida_dir, f"importancia_{r['fold']}.csv"), index=False)
        resumo["folds"].append({
            "fold": r["fold"], "n_teste": r["n_teste"], "iteracao_otima": r["iteracao_otima"],
            "elo_xg": {v: {"posicao_shap": int(pos[v]), "shap_pct": float(t.loc[v, "shap_pct"]), "pvc": float(t.loc[v, "pvc"]), "lfc": float(t.loc[v, "lfc"])} for v in elo},
            "elo_xg_grupo_shap_pct": float(t.loc[elo, "shap_pct"].sum()),
            "top10": [{"variavel": v, "shap_pct": float(x)} for v, x in r["tabela"].head(10)[["variavel", "shap_pct"]].itertuples(index=False)],
            "direcao": direcao(r["sv"], r["X"], features, elo),
            "parceiros_interacao": {v: parceiros[v] for v in elo},
            "pares_mais_fortes": pares_mais_fortes(r["inter"], features, 8),
            "elo_xg_diff_efeito_principal": principal,
            "elo_xg_diff_soma_interacoes": float(r["inter"][features.index("elo_xg_diff")].sum()),
        })
    est = estabilidade_entre_folds({r["fold"]: r["tabela"] for r in resultados})
    resumo["estabilidade"] = est
    print("\n=== Estabilidade entre folds ===")
    for k, v in est["spearman"].items():
        print(f"  {k}: Spearman do ranking SHAP {v:.3f} | top-{est['top_n']} em comum: {est['sobreposicao_top_n'][k]}")
    with open(os.path.join(args.saida_dir, "resumo_shap.json"), "w", encoding="utf-8") as fh:
        json.dump(resumo, fh, ensure_ascii=False, indent=2)
    print(f"\nArquivos em {args.saida_dir}/")


if __name__ == "__main__":
    main()
