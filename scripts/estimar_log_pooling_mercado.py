#!/usr/bin/env python3
"""Log-pooling entre modelo e mercado (Pinnacle devigada, snapshot
'pre_closing' -- a única odd realmente ACIONÁVEL antes do jogo; 'closing'
só existe em retrospecto, ver docstring de avaliar_modelo_misto_vs_mercado.py):

    p_pool(k) ∝ p_modelo(k)^w · p_mercado(k)^(1-w)      (renormalizado)

w é escolhido por grid search (passo 0,025) minimizando log-loss num split
TEMPORAL -- fit = 70% mais antigo das partidas com previsão+odd+resultado
completos, test = 30% mais recente -- nunca ajustado no mesmo conjunto que
valida, mesmo espírito walk-forward do resto do projeto (Dixon-Coles/Model
Benchmarking). Avaliação final no Test usa o bootstrap PAREADO já existente
(`backtest_kelly.comparar_pareado_com_mercado`), comparando o pool contra
MODELO SOZINHO e contra MERCADO SOZINHO.

Ferramenta de análise, não parte do pipeline de produção -- roda sob
demanda, mesma categoria de avaliar_modelo_misto_vs_mercado.py. Não grava
nada no Supabase.

**RESULTADO JÁ MEDIDO (25/08/2026, via execute_sql direto, reproduzido por
este script) -- w* = 0,000 em TODAS as 3 combinações testadas**
(hibrido_gols_v1×1X2, hibrido_gols_xg_v1×1X2, hibrido_gols_v1×over_under_2.5,
split 70/30 cronológico, ~3.900-4.000 partidas cada, Test Set ~1.180-1.190
partidas de 2025-09 a 2026-08): o grid search sempre bateu no limite w=0 --
qualquer peso dado ao modelo só PIORA o log-loss no Fit. O mercado bate o
modelo sozinho de forma estatisticamente significativa nas 3 combinações
(bootstrap pareado, IC95% da diferença modelo-mercado inteiro > 0, ex.:
+0,0293 [+0,0179,+0,0399] pra hibrido_gols_xg_v1×1X2). **Conclusão: hoje
não há nada pra "poolear" -- os modelos híbridos não carregam informação
incremental sobre o mercado devigado, na forma CRUA (sem calibração) em
que são gerados.** Log-pooling em cima de um modelo sem edge é um no-op por
construção (w*=0 == pool idêntico ao mercado puro).

Checagem rápida de calibração feita junto (reliability check por bucket de
`prob_home` de hibrido_gols_v1, ~4.000 partidas): o modelo é levemente
overconfident na faixa 0,3-0,7 (previsto 1-4pp acima da frequência real) e
bem UNDERCONFIDENT no bucket mais extremo, prob_home∈[0,7;1,0): previu
0,744 em média, frequência real de vitória foi 0,840 (+9,6pp) -- viés real,
mensurável, mas não necessariamente suficiente pra explicar sozinho a
diferença de log-loss medida. `model_calibration` está VAZIA pros dois
modelos híbridos (Platt/Isotonic nunca foram calculados pra eles -- só
existem pros modelos de árvore do Model Benchmarking, via
`backtest_kelly.tunar_treinar_e_calibrar`). Isso é candidato mais provável
a próximo passo antes de reinvestir em pooling: aplicar calibração nos
modelos híbridos e RODAR ESTE SCRIPT DE NOVO -- se w* sair do zero depois
disso, a causa era calibração; se continuar em zero, a causa é falta de
edge de verdade, não miscalibração.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key (ou anon -- só leitura)
    python scripts/estimar_log_pooling_mercado.py
"""

from __future__ import annotations

import logging
import os
import sys

import numpy as np
from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_kelly as bk
import dados_historicos as dh
from avaliar_modelo_misto_vs_mercado import _carregar_predicoes, _carregar_resultados_reais

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("estimar_log_pooling_mercado")

# btts/corners ficam de fora -- mesma decisão de cobertura já documentada em
# avaliar_modelo_misto_vs_mercado.py (amostra de abertura pequena demais,
# ex. btts abertura=90 partidas, pouco confiável pro bootstrap).
MODEL_NAMES = ["hibrido_gols_v1", "hibrido_gols_xg_v1"]
MERCADOS_AVALIADOS = ["1X2", "over_under_2.5"]

FRACAO_FIT = 0.7  # 70% mais antigo pra achar w, 30% mais recente pra validar (walk-forward)
GRADE_W = np.round(np.arange(0.0, 1.0001, 0.025), 4)
AMOSTRA_MINIMA = 100  # abaixo disso nem tenta -- split fica pequeno demais dos dois lados


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def _carregar_datas(supabase, match_ids: list[int]) -> dict[int, str]:
    def factory(lote, inicio, fim):
        return supabase.table("matches").select("id, match_date").in_("id", lote).order("id").range(inicio, fim)

    linhas = dh._paginar_por_lotes_de_id(factory, match_ids)
    return {l["id"]: l["match_date"] for l in linhas}


def _matriz_probs(predicoes: dict[int, dict[str, float]], match_ids: list[int], selecoes: list[str]) -> np.ndarray:
    """N x K, uma linha por partida, na MESMA ordem de `selecoes` -- garante
    que modelo e mercado fiquem alinhados coluna a coluna pra fazer
    p^w · q^(1-w)."""
    campos = [f"prob_{s}" for s in selecoes]
    return np.array([[predicoes[mid][c] for c in campos] for mid in match_ids])


def _indice_resultado(resultados_reais: dict[int, int], match_ids: list[int], codigo_por_selecao: dict[str, int]) -> np.ndarray:
    selecoes = list(codigo_por_selecao.keys())
    codigo_para_indice = {codigo: i for i, codigo in enumerate(codigo_por_selecao[s] for s in selecoes)}
    return np.array([codigo_para_indice[resultados_reais[mid]] for mid in match_ids])


def pool_log(p: np.ndarray, q: np.ndarray, w: float) -> np.ndarray:
    """p, q: N x K (cada linha soma 1). Devolve N x K normalizado. w=1 ==
    modelo puro, w=0 == mercado puro."""
    log_pool = w * np.log(np.clip(p, 1e-15, 1)) + (1 - w) * np.log(np.clip(q, 1e-15, 1))
    log_pool -= log_pool.max(axis=1, keepdims=True)  # estabilidade numérica antes do exp
    raw = np.exp(log_pool)
    return raw / raw.sum(axis=1, keepdims=True)


def log_loss_de(probs: np.ndarray, indice_real: np.ndarray) -> np.ndarray:
    """Log-loss POR PARTIDA (não a média) -- alimenta o bootstrap pareado."""
    p_real = probs[np.arange(len(indice_real)), indice_real]
    return -np.log(np.clip(p_real, 1e-15, 1))


def ajustar_w(p_fit: np.ndarray, q_fit: np.ndarray, indice_fit: np.ndarray) -> tuple[float, float]:
    """Grid search minimizando log-loss médio no FIT -- mesmo espírito de
    'não dá pra estimar via MLE junto com o resto, calibra por validação
    temporal' já documentado em forca_dinamica_desenho.md pro XI/Q."""
    melhor_w, melhor_loss = 0.5, np.inf
    for w in GRADE_W:
        loss = log_loss_de(pool_log(p_fit, q_fit, w), indice_fit).mean()
        if loss < melhor_loss:
            melhor_w, melhor_loss = float(w), float(loss)
    return melhor_w, melhor_loss


def avaliar_pooling(supabase, model_name: str, mercado: str) -> dict | None:
    codigo_por_selecao = bk.MERCADOS[mercado]["codigo_por_selecao"]
    selecoes = list(codigo_por_selecao.keys())

    predicoes = _carregar_predicoes(supabase, model_name, mercado)
    if not predicoes:
        logger.warning("%s [%s]: sem previsões -- pulando.", model_name, mercado)
        return None

    match_ids_pred = list(predicoes.keys())
    resultados_reais = _carregar_resultados_reais(supabase, match_ids_pred, mercado)
    odds_devig = bk.carregar_odds_pinnacle_devigadas(supabase, match_ids_pred, mercado)
    datas = _carregar_datas(supabase, match_ids_pred)

    match_ids_validos = [
        mid
        for mid in match_ids_pred
        if mid in resultados_reais
        and mid in odds_devig
        and mid in datas
        and all(predicoes[mid].get(f"prob_{s}") is not None for s in selecoes)
    ]
    if len(match_ids_validos) < AMOSTRA_MINIMA:
        logger.warning("%s [%s]: só %d partidas com dado completo -- amostra pequena demais.", model_name, mercado, len(match_ids_validos))
        return None

    match_ids_validos.sort(key=lambda mid: datas[mid])
    corte = int(len(match_ids_validos) * FRACAO_FIT)
    fit_ids, test_ids = match_ids_validos[:corte], match_ids_validos[corte:]
    if len(fit_ids) < 50 or len(test_ids) < 50:
        logger.warning("%s [%s]: split fit/test pequeno demais (fit=%d, test=%d).", model_name, mercado, len(fit_ids), len(test_ids))
        return None

    p_fit, q_fit = _matriz_probs(predicoes, fit_ids, selecoes), _matriz_probs(odds_devig, fit_ids, selecoes)
    idx_fit = _indice_resultado(resultados_reais, fit_ids, codigo_por_selecao)
    w_star, loss_fit = ajustar_w(p_fit, q_fit, idx_fit)

    p_test, q_test = _matriz_probs(predicoes, test_ids, selecoes), _matriz_probs(odds_devig, test_ids, selecoes)
    idx_test = _indice_resultado(resultados_reais, test_ids, codigo_por_selecao)
    perdas_pool = log_loss_de(pool_log(p_test, q_test, w_star), idx_test)
    perdas_modelo = log_loss_de(p_test, idx_test)
    perdas_mercado = log_loss_de(q_test, idx_test)

    logger.info(
        "%s [%s]: w*=%.3f (log-loss fit=%.4f, n_fit=%d) | TEST n=%d | log-loss pool=%.4f modelo=%.4f mercado=%.4f",
        model_name, mercado, w_star, loss_fit, len(fit_ids), len(test_ids),
        perdas_pool.mean(), perdas_modelo.mean(), perdas_mercado.mean(),
    )

    return {
        "model_name": model_name,
        "mercado": mercado,
        "w_estrela": w_star,
        "n_fit": len(fit_ids),
        "n_test": len(test_ids),
        "log_loss_pool": float(perdas_pool.mean()),
        "log_loss_modelo": float(perdas_modelo.mean()),
        "log_loss_mercado": float(perdas_mercado.mean()),
        "pool_vs_mercado": bk.comparar_pareado_com_mercado(perdas_pool, perdas_mercado),
        "pool_vs_modelo": bk.comparar_pareado_com_mercado(perdas_pool, perdas_modelo),
        "modelo_vs_mercado": bk.comparar_pareado_com_mercado(perdas_modelo, perdas_mercado),
    }


def _veredito(comparacao: dict, rotulo_a: str, rotulo_b: str) -> str:
    if comparacao["modelo_supera_mercado"]:
        return f"{rotulo_a} SUPERIOR"
    if comparacao["mercado_supera_modelo"]:
        return f"{rotulo_b} SUPERIOR"
    return "empate estatístico"


def imprimir_relatorio(resultados: list[dict]) -> None:
    logger.info("=" * 100)
    logger.info("LOG-POOLING MODELO x MERCADO -- w* ajustado no Fit (70%% mais antigo), avaliado no Test (30%% mais recente)")
    logger.info("=" * 100)
    for r in resultados:
        logger.info(
            "%-22s [%-14s] w*=%.3f | n_test=%4d | log-loss pool=%.4f modelo=%.4f mercado=%.4f | pool vs mercado: %s | pool vs modelo: %s | modelo vs mercado: %s",
            r["model_name"], r["mercado"], r["w_estrela"], r["n_test"],
            r["log_loss_pool"], r["log_loss_modelo"], r["log_loss_mercado"],
            _veredito(r["pool_vs_mercado"], "POOL", "MERCADO"),
            _veredito(r["pool_vs_modelo"], "POOL", "MODELO"),
            _veredito(r["modelo_vs_mercado"], "MODELO", "MERCADO"),
        )
        if r["w_estrela"] == 0.0:
            logger.info("  w*=0 -- pool é idêntico ao mercado puro aqui, não há informação do modelo sendo incorporada.")
    logger.info("=" * 100)


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))
    resultados = []
    for model_name in MODEL_NAMES:
        for mercado in MERCADOS_AVALIADOS:
            r = avaliar_pooling(supabase, model_name, mercado)
            if r is not None:
                resultados.append(r)
    imprimir_relatorio(resultados)


if __name__ == "__main__":
    main()
