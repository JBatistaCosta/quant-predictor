#!/usr/bin/env python3
"""Sincroniza models_registry a partir das fontes que já existem no projeto —
não inventa hiperparâmetro/feature nenhum, só copia o que já está no código
(scripts/modelos_ml.FEATURES_POR_MODELO/PARAMS_DEFAULT) e no banco
(model_benchmarking_backtest, a métrica de teste mais recente por
model_name+mercado).

Roda depois de qualquer retreino novo em rodar_predicoes.py/backtest_kelly.py
— idempotente (upsert por name+market), então rodar de novo só atualiza.

status: escolhido automaticamente, mesma regra já usada em
ModelBenchmarking.jsx pra destacar o "melhor" por mercado — dentre os
`significativo=true` (ROI com IC95% positivo), o de maior `roi_ic95_inferior`
vira 'active'; os demais desse mercado viram 'testing'. Nunca 'deprecated'
automaticamente (isso é uma decisão do usuário, não do script).

dixon_coles_v1 (implementação antiga, arquivos_do_claude/modelo_dixon_coles.py)
e dixon_coles_walkforward_v1 não passam por model_benchmarking_backtest — são
somente as previsões em model_predictions, sem backtest formal de ROI ainda.
Registrados como 'testing' sempre (sem métrica de ROI pra comparar).

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python sync_models_registry.py
"""

from __future__ import annotations

import os
import sys

from supabase import create_client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from modelos_ml import FEATURES_POR_MODELO, PARAMS_DEFAULT  # noqa: E402

ALGORITMO_POR_PREFIXO = {
    "catboost": "CatBoost",
    "xgboost": "XGBoost",
    "lightgbm": "LightGBM",
    "dixon_coles": "Dixon-Coles (Poisson)",
    "stats_glm": "GLM (regressão logística)",
}


def obter_env_obrigatoria(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def algoritmo_do_nome(model_name: str) -> str:
    base = model_name.replace("_calibrado_platt", "").replace("_calibrado_isotonic", "")
    for prefixo, algoritmo in ALGORITMO_POR_PREFIXO.items():
        if base.startswith(prefixo):
            return algoritmo
    return "desconhecido"


def features_do_nome(model_name: str) -> list[str] | None:
    base = model_name.replace("_calibrado_platt", "").replace("_calibrado_isotonic", "")
    return FEATURES_POR_MODELO.get(base)


def hiperparametros_do_nome(model_name: str) -> dict:
    base = model_name.replace("_calibrado_platt", "").replace("_calibrado_isotonic", "")
    params = dict(PARAMS_DEFAULT.get(base, {}))
    if "_calibrado_platt" in model_name:
        params["calibracao_pos_hoc"] = "platt"
    elif "_calibrado_isotonic" in model_name:
        params["calibracao_pos_hoc"] = "isotonic"
    return params


def main() -> None:
    url = obter_env_obrigatoria("SUPABASE_URL")
    key = obter_env_obrigatoria("SUPABASE_KEY")
    supabase = create_client(url, key)

    backtests = supabase.table("model_benchmarking_backtest").select(
        "model_name, mercado, n_apostas, roi_medio, roi_ic95_inferior, roi_ic95_superior, "
        "significativo, log_loss, brier, accuracy, executado_em"
    ).execute().data

    # última linha (maior executado_em) por model_name+mercado
    ultima_por_chave: dict[tuple[str, str], dict] = {}
    for linha in backtests:
        chave = (linha["model_name"], linha["mercado"])
        atual = ultima_por_chave.get(chave)
        if atual is None or linha["executado_em"] > atual["executado_em"]:
            ultima_por_chave[chave] = linha

    # melhor roi_ic95_inferior entre os significativos, por mercado — mesma
    # regra de destaque já usada em ModelBenchmarking.jsx
    melhor_por_mercado: dict[str, tuple[str, float]] = {}
    for (nome, mercado), linha in ultima_por_chave.items():
        if not linha.get("significativo"):
            continue
        roi_inf = linha.get("roi_ic95_inferior")
        if roi_inf is None:
            continue
        atual = melhor_por_mercado.get(mercado)
        if atual is None or roi_inf > atual[1]:
            melhor_por_mercado[mercado] = (nome, roi_inf)

    linhas_registro = []
    for (nome, mercado), linha in ultima_por_chave.items():
        status = "active" if melhor_por_mercado.get(mercado, (None,))[0] == nome else "testing"
        linhas_registro.append({
            "name": nome,
            "market": mercado,
            "algorithm": algoritmo_do_nome(nome),
            "status": status,
            "features_used": features_do_nome(nome),
            "hyperparameters": hiperparametros_do_nome(nome),
            "metrics_test": {
                "n_apostas": linha.get("n_apostas"),
                "roi_medio": linha.get("roi_medio"),
                "roi_ic95_inferior": linha.get("roi_ic95_inferior"),
                "roi_ic95_superior": linha.get("roi_ic95_superior"),
                "significativo": linha.get("significativo"),
                "log_loss": linha.get("log_loss"),
                "brier": linha.get("brier"),
                "accuracy": linha.get("accuracy"),
                "fonte": "model_benchmarking_backtest",
                "avaliado_em": linha.get("executado_em"),
            },
        })

    # dixon_coles_v1 (pipeline antigo) e dixon_coles_walkforward_v1 — só
    # existem em model_predictions, sem backtest de ROI formal ainda.
    for nome in ("dixon_coles_v1", "dixon_coles_walkforward_v1"):
        ja_tem_1x2 = any(r["name"] == nome and r["market"] == "1X2" for r in linhas_registro)
        if ja_tem_1x2:
            continue  # já veio do model_benchmarking_backtest acima (mesmo nome, pipeline novo)
        linhas_registro.append({
            "name": nome,
            "market": "1X2",
            "algorithm": "Dixon-Coles (Poisson)",
            "status": "testing",
            "features_used": None,
            "hyperparameters": {"nota": "Poisson bivariado — ataque/defesa por time, não usa lista de features tabular"},
            "metrics_test": {"fonte": "model_predictions", "nota": "sem backtest de ROI formal — só log-loss calculado ao vivo em /api/model-stats"},
        })

    for i in range(0, len(linhas_registro), 200):
        supabase.table("models_registry").upsert(
            linhas_registro[i:i + 200], on_conflict="name,market",
        ).execute()

    print(f"{len(linhas_registro)} linhas sincronizadas em models_registry.")
    for mercado, (nome, roi) in sorted(melhor_por_mercado.items()):
        print(f"  active em '{mercado}': {nome} (roi_ic95_inferior={roi:.4f})")


if __name__ == "__main__":
    main()
