#!/usr/bin/env python3
"""Ad-hoc: mede se as features de pressão ofensiva (chutes, chutes
bloqueados, chutes no alvo, posse, chances claras, toques na área
adversária) contribuem de verdade pra `hibrido_corners_v1`, o regressor de
lambda de escanteios do modelo misto.

Contexto: o item 2 de "PRÓXIMOS PASSOS PENDENTES" (CONTEXTO_PROJETO.md)
propunha "testar pareamento de escanteios com chutes/xG como covariável",
partindo da premissa de que essas estatísticas eram ignoradas. Investigação
mostrou que a premissa está DESATUALIZADA pro modelo misto: `hibrido_corners_v1`
usa `FEATURES_V12_MESMA_LIGA` (modelos_ml.py), que já herda a forma pré-jogo
de chutes/chutes bloqueados/posse desde FEATURES_V7/V8 (dados_historicos.py)
-- essas colunas JÁ são entrada do CatBoost Poisson que estima o lambda de
escanteios, não estão faltando.

O que continuava sem resposta -- e é o que este script mede -- é se essas
~26 colunas (entre ~190 features totais) de fato CARREGAM sinal usado pelo
modelo, ou se são ruído que o CatBoost já ignora na prática. Teste direto:
treina `hibrido_corners_v1` duas vezes (mesmo split treino/calibração/teste,
mesmos hiperparâmetros) -- uma com o feature set atual (baseline), outra
removendo só as colunas de pressão ofensiva (ablação) -- e compara a
log-verossimilhança/log-loss O/U9.5 de escanteios no MESMO holdout de teste,
reaproveitando `avaliar()`/`ajustar_parametros_estruturais()` de
`treinar_modelo_hibrido.py` (mesma métrica que decide promoção de modelo em
produção, não uma métrica nova inventada pra este teste).

lambda de gols (`hibrido_gols_v1`) é treinado uma única vez e reaproveitado
nos dois cenários -- a ablação só afeta o feature set de escanteios, então
isolar o efeito exige manter tudo mais constante.

Não faz parte do pipeline de produção: roda uma vez, imprime no stdout, não
grava nada no Supabase. Mesmo espírito de `scripts/verificar_distribuicoes.py`.

Uso:
    set SUPABASE_URL=...
    set SUPABASE_KEY=sua_service_role_key
    python testar_covariaveis_corners.py
"""

from __future__ import annotations

import logging
import os
import sys

from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dados_historicos as dh
import modelos_ml as ml
import treinar_modelo_hibrido as tmh

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("testar_covariaveis_corners")

# As ~26 colunas de "pressão ofensiva" -- chutes/chutes no alvo/posse do
# FBref (COLUNAS_FORMA_*) + os equivalentes do FotMob (via
# colunas_forma_fotmob, mesmo nome curto de COLUNAS_STATS_FOTMOB) que tocam
# diretamente na hipótese mecânica original (chute -> chute bloqueado ->
# escanteio; pressão ofensiva -> mais escanteios). NÃO inclui xG/xGOT (sinal
# já estabelecido como núcleo do modelo de GOLS, fora do escopo desta
# hipótese especfica) nem a forma de escanteios em si (é o histórico do
# próprio alvo, não uma covariável nova).
COLUNAS_PRESSAO_OFENSIVA = [
    *dh.COLUNAS_FORMA_CHUTES.values(),
    *dh.COLUNAS_FORMA_CHUTES_ALVO.values(),
    *dh.COLUNAS_FORMA_POSSE.values(),
    *dh.colunas_forma_fotmob("chutes_fm").values(),
    *dh.colunas_forma_fotmob("chutes_alvo_fm").values(),
    *dh.colunas_forma_fotmob("chutes_fora_fm").values(),
    *dh.colunas_forma_fotmob("chutes_bloqueados_fm").values(),
    *dh.colunas_forma_fotmob("chutes_area_fm").values(),
    *dh.colunas_forma_fotmob("chutes_fora_area_fm").values(),
    *dh.colunas_forma_fotmob("chances_claras_fm").values(),
    *dh.colunas_forma_fotmob("chances_claras_perdidas_fm").values(),
    *dh.colunas_forma_fotmob("posse_fm").values(),
    *dh.colunas_forma_fotmob("toques_area_adv_fm").values(),
]


def obter_env(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        sys.exit(f"Configure {nome} antes de rodar.")
    return valor


def main() -> None:
    supabase = create_client(obter_env("SUPABASE_URL"), obter_env("SUPABASE_KEY"))

    resposta = supabase.table("leagues").select("id, name").eq("modelo_misto_escopo", "treino").execute()
    ligas_treino = resposta.data or []
    league_ids = [linha["id"] for linha in ligas_treino] or None
    logger.info("Escopo: %d liga(s) de treino: %s", len(ligas_treino), sorted(l["name"] for l in ligas_treino))

    logger.info("Montando dataset (pode levar alguns minutos)...")
    dataset = dh.montar_dataset_ml_empilhado(supabase, league_ids_manual=league_ids)
    if dataset.empty:
        sys.exit("Dataset vazio -- nada pra testar.")

    # Mesma defesa de main() (treinar_modelo_hibrido.py) contra feature
    # pedida que não existe no dataset -- protege contra KeyError se algum
    # nome de coluna mudar sem este script acompanhar.
    colunas_dataset = set(dataset.columns)
    for chave in ("hibrido_gols_v1", tmh.MODELO_CORNERS):
        originais = ml.FEATURES_POR_MODELO.get(chave, [])
        faltando = [f for f in originais if f not in colunas_dataset]
        if faltando:
            logger.warning("[%s] features ausentes no dataset (ignoradas): %s", chave, faltando)
            ml.FEATURES_POR_MODELO[chave] = [f for f in originais if f in colunas_dataset]

    treino, calib, teste = tmh.dividir_cronologicamente(dataset)
    logger.info("Split cronológico: treino=%d, calibração=%d, teste=%d (%s → %s)",
                len(treino), len(calib), len(teste),
                teste["match_date"].min(), teste["match_date"].max())

    # lambda de gols -- uma vez só, reaproveitado nos dois cenários de
    # escanteios (avaliar() precisa dele, mas a ablação não o afeta).
    logger.info("Treinando λ de gols (hibrido_gols_v1, reaproveitado nos dois cenários)...")
    (lam_h_calib, lam_a_calib), (lam_h_teste, lam_a_teste) = tmh.treinar_lambdas_gols(
        "hibrido_gols_v1", tmh.VARIANTES_GOLS["hibrido_gols_v1"], treino, [calib, teste],
    )

    features_originais = list(ml.FEATURES_POR_MODELO[tmh.MODELO_CORNERS])
    presentes = [c for c in COLUNAS_PRESSAO_OFENSIVA if c in features_originais]
    ausentes = [c for c in COLUNAS_PRESSAO_OFENSIVA if c not in features_originais]
    if ausentes:
        logger.warning("Colunas de pressão ofensiva esperadas mas ausentes do feature set atual: %s", ausentes)
    logger.info("%d/%d colunas de pressão ofensiva confirmadas no feature set atual de %s.",
                len(presentes), len(COLUNAS_PRESSAO_OFENSIVA), tmh.MODELO_CORNERS)
    if not presentes:
        sys.exit("Nenhuma coluna de pressão ofensiva encontrada no feature set -- nada pra ablacionar.")

    cenarios = [
        ("baseline (feature set atual, com chutes/chutes bloqueados/posse/etc.)", features_originais),
        (f"ablação (sem as {len(presentes)} colunas de pressão ofensiva)",
         [c for c in features_originais if c not in presentes]),
    ]

    resultados = {}
    for nome_cenario, features in cenarios:
        ml.FEATURES_POR_MODELO[tmh.MODELO_CORNERS] = features
        logger.info("--- Treinando cenário: %s (%d features) ---", nome_cenario, len(features))
        lam_corners_calib, lam_corners_teste = tmh.treinar_lambda_corners(treino, [calib, teste])
        parametros = tmh.ajustar_parametros_estruturais(calib, lam_h_calib, lam_a_calib, lam_corners_calib)
        metricas = tmh.avaliar(teste, lam_h_teste, lam_a_teste, lam_corners_teste, parametros)
        resultados[nome_cenario] = {
            "n_teste_corners": metricas.get("n_teste_corners"),
            "log_verossimilhanca_corners": metricas.get("log_verossimilhanca_corners"),
            "log_loss_corners_ou95": metricas.get("log_loss_corners_ou95"),
            "corners_disp_r": parametros.get("corners_disp_r"),
        }
        logger.info(
            "[%s] n=%s log-verossimilhança=%.4f log-loss O/U9.5=%.4f disp_r=%.1f",
            nome_cenario, metricas.get("n_teste_corners"),
            metricas.get("log_verossimilhanca_corners"), metricas.get("log_loss_corners_ou95"),
            parametros.get("corners_disp_r", float("nan")),
        )

    ml.FEATURES_POR_MODELO[tmh.MODELO_CORNERS] = features_originais  # restaura, por higiene

    nomes = list(resultados.keys())
    base, ablado = resultados[nomes[0]], resultados[nomes[1]]
    delta_ll = base["log_verossimilhanca_corners"] - ablado["log_verossimilhanca_corners"]
    delta_ll_ou = ablado["log_loss_corners_ou95"] - base["log_loss_corners_ou95"]
    logger.info("=" * 78)
    logger.info("RESULTADO FINAL")
    logger.info("Δ log-verossimilhança (baseline - ablação) = %+.4f -- positivo = features de pressão AJUDAM", delta_ll)
    logger.info("Δ log-loss O/U9.5 (ablação - baseline)      = %+.4f -- positivo = features de pressão MELHORAM", delta_ll_ou)
    logger.info("Referência de ruído de amostra: com n=%s, diferenças de log-verossimilhança abaixo de "
                "~0.01-0.02 não costumam ser distinguíveis de ruído nesta escala (ver disciplina de IC95% "
                "já aplicada a outras comparações do projeto, achado #18/#27 do CONTEXTO_PROJETO.md) -- "
                "não declarar 'ajuda'/'não ajuda' só pelo sinal, considerar a magnitude.",
                base.get("n_teste_corners"))


if __name__ == "__main__":
    main()
