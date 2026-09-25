"""Runner fino do pricing pipeline de 4 camadas (`pricing_pipeline.py`) para
partidas `scheduled` dentro da janela de dias configurada -- grava os
mercados de gols da Camada 3 (Dixon-Coles reconciliado) com
`model_name='pricing_pipeline_v1'`, mesmo padrão de duas tabelas já usado
por `rodar_xg_agregado_previsto.py`:
  - `model_match_estimates.params` (`lambda_home`/`lambda_away`/`rho`) --
    é isso que faz `pricing_pipeline_v1` aparecer como mais uma aba de
    modelo em `AnaliseAvancadaEvento.jsx` ao lado de `hibrido_gols_v1`/
    `hibrido_gols_xg_v1`/`xg_jogador_agregado_v1`, com TODA a UI genérica
    de matriz/mercados/comparação de EV/Kelly/CSV já existente pra
    qualquer modelo (a página deriva a matriz e todos os mercados client-
    side a partir desses 3 números via `matrizPlacares`/`mercadosDeGols`,
    o mesmo algoritmo Dixon-Coles de `distribuicoes.py` com paridade JS/
    Python já validada -- não precisa de nenhum código novo de exibição).
  - `model_predictions` (mercado/seleção/probabilidade) -- tabela flat
    equivalente, mesmo papel que já tem pros outros 3 modelos (consumo
    programático/futuro, não é o que alimenta a UI de EV).

Escopo deliberadamente restrito às Camadas 1-3 (agregação bottom-up +
reconciliação + matriz conjunta) -- NÃO roda a Camada 4 (`EnsembleVetoManager`,
que decide ordem de aposta contra odds reais). O que este runner persiste é
"que probabilidade o modelo atribui a cada mercado", não "que aposta fazer" --
a segunda pergunta depende de odds reais capturadas por partida, que nem toda
partida tem, e além disso mistura decisão de risco (Kelly/vetos) com a
previsão em si. Rodar `process_match()` completo fica pra quando essa
integração de odds + decisão de aposta for pedida explicitamente (ver plano
da sessão).

Fontes de dado (todas já existentes, nenhuma tabela nova):
  - `player_match_estimates` -- estimativas por jogador (Camada 1), mesma
    tabela/mesmo `select` que `AnaliseAvancadaEvento.jsx` já usa. Prioriza
    `fonte_titular='real'` sobre `'previsto'` quando as duas existem (mesma
    prioridade da UI, ver `SecaoJogadorMercados`).
  - `model_match_estimates.params` -- lambda_home/lambda_away/rho do modelo
    macro já treinado (mesmo jsonb que `AnaliseAvancadaEvento.jsx::
    lerParametrosPartida` lê) -- usado como `macro_priors` da Camada 2.
    Partida sem nenhum `model_name` com params utilizáveis é pulada (não há
    macro pra reconciliar contra).

GSAx (goleiro) NEUTRALIZADO (25/09) -- achado desta sessão (Fase 7/Frente B
do plano + refinamentos, ver `CONTEXTO_PROJETO.md`): validação walk-forward
formal (`scripts/backtest_gsax_walkforward.py`) mostrou `gsax_rate` como
ATIVAMENTE PREJUDICIAL à previsão de gols sofridos (RMSE pior que ignorar o
goleiro, 65% dos grupos liga×temporada com degradação sustentada por
IC95%). Testado corrigir o viés de nível (relativo à média da liga: RMSE
melhora mas não fecha o gap) e a diferença de GSAx entre os dois goleiros
do confronto contra o resultado real (correlação ≈0, n=18.944) -- nenhuma
correção simples recupera sinal aproveitável. `_gsax_do_goleiro` foi
removida; os dois lados agora sempre agregam com `gsax_rate_adversario=0.0`
(default já neutro de `PlayerToTeamAggregator.agregar`, passado explícito
aqui só pra documentar a decisão no diff). `player_match_estimates.gsax_rate`
continua sendo calculado/persistido por `rodar_jogador_mercados_previsto.py`
(não é bug manter -- outros consumidores podem existir/surgir), só este
runner parou de lê-lo. `delta_shooting` (xGOT-xG por jogador) segue sem
existir em `player_match_estimates` -- gap de dado documentado, não bug --
e `jogadores` continua sem essa coluna (a Camada 1 já trata coluna ausente
como 0 com aviso, não exceção).

Uso:
    SUPABASE_URL=... SUPABASE_KEY=... python3 rodar_pricing_pipeline.py [--dias N] [--match-ids ID,ID,...]

Modo `--backtest`: em vez de partidas `scheduled` nos próximos `--dias` dias,
processa partidas já `finished` nos últimos `--dias` dias (ou os `--match-ids`
explícitos, que nesse modo NÃO ficam restritos a `scheduled`). Existe pra
medir estatísticas de desempenho reais do `pricing_pipeline_v1` (log-loss,
Brier, calibração em `/modelos`) -- até rodar isso, o modelo só tinha
previsões pra partidas ainda não realizadas, sem nenhum resultado real pra
comparar. `player_match_estimates`/`model_match_estimates.params` de uma
partida não são apagados quando ela termina (confirmado via SQL: 259
partidas `finished` de 30/ago a 14/set com as duas tabelas populadas), então
a Camada 1-3 roda igual, só a origem da lista de partidas muda -- não é uma
previsão "adivinhando o passado", é gerar HOJE a mesma probabilidade que o
pipeline teria dado antes do apito, usando o mesmo dado que estava disponível
então (estimativas de jogador/macro não usam resultado da partida).

Comparação escalação real vs. prevista (pedido do usuário, 15/09): pra cada
partida, `player_match_estimates` guarda `fonte_titular='previsto'` (XI
provável, disponível desde a semana anterior) e `'real'` (escalação oficial,
só sai ~60min antes do apito) LADO A LADO, sem uma sobrescrever a outra --
mesmo padrão de `xi_previsto` vs. escalação oficial já usado em
`rodar_jogador_mercados_previsto.py`. Antes desta mudança, este runner
escolhia só UMA fonte por partida (real se disponível, senão previsto) e
gravava só em `pricing_pipeline_v1` -- quando a real saía depois de já ter
rodado com a previsto, a versão baseada em previsto era perdida (sobrescrita
pelo `upsert`), então não dava pra comparar as duas depois. Agora, quando as
DUAS fontes têm elenco completo dos 2 times pra uma partida, a Camada 1-3
roda 1x pra cada fonte e grava em `model_name` SEPARADOS:
  - `pricing_pipeline_previsto_v1` -- sempre que houver XI previsto completo.
  - `pricing_pipeline_real_v1` -- sempre que houver escalação oficial completa.
  - `pricing_pipeline_v1` (nome original, inalterado) continua existindo
    como "melhor fonte disponível no momento" (real quando existe, senão
    previsto) -- é o que a aba de Análise Avançada e o backtest já feito
    seguem usando, sem nenhuma mudança de comportamento pra quem já
    consumia esse nome.
As 3 "visões" nunca conflitam entre si no `upsert` (chaves `match_id,
model_name` distintas) -- rodar de novo só atualiza a fonte que mudou.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os

import pandas as pd
from supabase import Client, create_client

import distribuicoes as dist
import dados_historicos as dh
from pricing_pipeline import DixonColesJointEngine, HierarchicalReconciler, PlayerToTeamAggregator
from rodar_jogador_mercados_previsto import buscar_fixtures

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROB_MIN, PROB_MAX = 0.0001, 0.9999


def _clamp_probabilidade(p: float) -> float:
    """`model_predictions.probability` tem `CHECK(0<probability<1)` --
    `round(p, 5)` pode zerar/saturar exatamente em 0.0/1.0 pra combinações
    extremas de λ (ex.: `over 2.5` de um time com λ_assistências baixo),
    mesmo achado/mesmo fix já feito em `scripts/rodar_markov_batch.mjs`
    (`PROB_MIN`/`PROB_MAX`, ver `CONTEXTO_PROJETO.md` Fase 6.1)."""
    return min(max(float(p), PROB_MIN), PROB_MAX)


MODEL_NAME_SAIDA = "pricing_pipeline_v1"
FONTES_RASTREADAS = ("previsto", "real")
COLUNAS_JOGADOR = [
    "match_id", "team_id", "player_id", "fonte_titular", "is_titular_previsto", "prob_titular_usada",
    "posicao_detalhe", "minutos_esperados", "lambda_chutes_jogo", "lambda_chutes_no_alvo_jogo",
    "lambda_xg_jogo", "taxa_conversao_bayesiana", "lambda_xa_jogo",
]


def _buscar_macro_priors(supabase: Client, match_ids: list[int]) -> dict[int, dict]:
    """`{match_id: {lambda_home, lambda_away, rho}}` -- primeiro `model_name`
    com lambda_home/lambda_away > 0 em `model_match_estimates.params`, mesmo
    critério de `lerParametrosPartida` (frontend). Partida sem nenhum
    `model_name` utilizável simplesmente não aparece no dict -- quem chama
    pula (não há macro pra reconciliar contra, ver docstring do módulo).

    Pagina de verdade (`dh._paginar`) -- várias partidas x até 3
    `model_name` cada facilmente passa do corte silencioso de 1000 linhas
    do PostgREST (achado real: sem isso, só as primeiras ~1000 linhas
    retornadas viravam macro_priors, derrubando a cobertura da Camada 3
    pra uma fração pequena e arbitrária das partidas na janela)."""
    linhas = dh._paginar(
        lambda inicio, fim: (
            supabase.table("model_match_estimates")
            .select("match_id, params")
            .in_("match_id", match_ids)
            .not_.is_("params", "null")
            .range(inicio, fim)
        )
    )
    saida: dict[int, dict] = {}
    for linha in linhas:
        if linha["match_id"] in saida:
            continue  # já achou um macro utilizável pra essa partida, primeiro que aparece vale
        params = linha.get("params") or {}
        try:
            lam = float(params.get("lambda_home"))
            mu = float(params.get("lambda_away"))
        except (TypeError, ValueError):
            continue
        if lam <= 0 or mu <= 0:
            continue
        rho = params.get("rho")
        try:
            rho = float(rho)
        except (TypeError, ValueError):
            rho = 0.0
        saida[linha["match_id"]] = {"lambda_home": lam, "lambda_away": mu, "rho_liga": rho}
    return saida


def _buscar_jogadores(supabase: Client, match_ids: list[int]) -> pd.DataFrame:
    """Uma linha por jogador das partidas em `match_ids`, nas DUAS fontes
    quando existirem -- a escolha de qual fonte usar (real > previsto,
    mesma prioridade de `SecaoJogadorMercados`) acontece depois, por
    partida, em `_selecionar_fonte_por_partida`.

    Pagina de verdade (`dh._paginar`) -- ~130 partidas x 40-80 jogadores
    cada estoura o corte silencioso de 1000 linhas do PostgREST muito
    fácil (achado real: sem paginação, só as partidas cujas linhas
    calhavam de vir nas primeiras 1000 ganhavam elenco pra Camada 1 --
    todas as outras eram puladas silenciosamente como "sem player_match_
    estimates", mesmo tendo dado real no banco)."""
    linhas = dh._paginar(
        lambda inicio, fim: (
            supabase.table("player_match_estimates")
            .select(",".join(COLUNAS_JOGADOR))
            .in_("match_id", match_ids)
            .range(inicio, fim)
        )
    )
    return pd.DataFrame(linhas, columns=COLUNAS_JOGADOR)


def _fonte_melhor_disponivel(fontes_presentes: set[str]) -> str:
    """Mesma prioridade de sempre (real > previsto) -- usada só pra decidir
    qual das fontes rastreadas também grava em `MODEL_NAME_SAIDA` (o nome
    "melhor disponível", inalterado pra quem já consumia esse model_name)."""
    return "real" if "real" in fontes_presentes else "previsto"


def _buscar_fixtures_finalizadas(supabase: Client, dias: int, match_ids: list[int] | None) -> pd.DataFrame:
    """Mesmas colunas de `buscar_fixtures`, mas partidas `status='finished'`
    em vez de `scheduled` -- usado só pelo modo `--backtest` (ver docstring
    do módulo). Com `match_ids` explícito, filtra só por eles (sem janela de
    dias); sem `match_ids`, pega toda `finished` com `match_date` nos
    últimos `dias` dias."""
    query = (
        supabase.table("matches")
        .select("id, match_date, home_team_id, away_team_id, league_id")
        .eq("status", "finished")
    )
    if match_ids:
        query = query.in_("id", match_ids)
    else:
        piso = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=dias)
        query = query.gte("match_date", piso.isoformat())
    resp = query.execute()
    return pd.DataFrame(resp.data or [])


def rodar(supabase: Client, dias: int, match_ids: list[int] | None, backtest: bool = False) -> int:
    fixtures = (
        _buscar_fixtures_finalizadas(supabase, dias, match_ids)
        if backtest
        else buscar_fixtures(supabase, dias, match_ids)
    )
    if fixtures.empty:
        logger.info("Nenhuma partida '%s' na janela -- nada a precificar.", "finished" if backtest else "scheduled")
        return 0

    fixture_ids = [int(m) for m in fixtures["id"].tolist()]
    macro_por_partida = _buscar_macro_priors(supabase, fixture_ids)
    jogadores_todos = _buscar_jogadores(supabase, fixture_ids)

    agregador = PlayerToTeamAggregator()
    reconciler = HierarchicalReconciler()
    engine = DixonColesJointEngine()

    linhas_saida = []
    estimativas_saida = []
    partidas_processadas = 0
    for _, fixture in fixtures.iterrows():
        match_id = int(fixture["id"])
        macro = macro_por_partida.get(match_id)
        if macro is None:
            logger.info("Partida %s sem modelo macro (model_match_estimates.params) utilizável -- pulando.", match_id)
            continue

        jogadores_partida = jogadores_todos[jogadores_todos["match_id"] == match_id]
        if jogadores_partida.empty:
            logger.info("Partida %s sem player_match_estimates -- pulando.", match_id)
            continue

        home_id, away_id = int(fixture["home_team_id"]), int(fixture["away_team_id"])
        fontes_presentes = set(jogadores_partida["fonte_titular"])
        fonte_melhor = _fonte_melhor_disponivel(fontes_presentes)

        processou_alguma_fonte = False
        for fonte in FONTES_RASTREADAS:
            if fonte not in fontes_presentes:
                continue
            jogadores_fonte = jogadores_partida[jogadores_partida["fonte_titular"] == fonte]
            jogadores_home = jogadores_fonte[jogadores_fonte["team_id"] == home_id]
            jogadores_away = jogadores_fonte[jogadores_fonte["team_id"] == away_id]
            if jogadores_home.empty or jogadores_away.empty:
                continue  # essa fonte não tem elenco dos 2 times ainda -- tenta a outra fonte

            try:
                agregacao_home = agregador.agregar(jogadores_home, gsax_rate_adversario=_gsax_do_goleiro(jogadores_away))
                agregacao_away = agregador.agregar(jogadores_away, gsax_rate_adversario=_gsax_do_goleiro(jogadores_home))
            except ValueError as exc:
                logger.warning("Partida %s (fonte=%s): falha na Camada 1 (%s) -- pulando essa fonte.", match_id, fonte, exc)
                continue

            reconciliacao_home = reconciler.reconciliar(
                agregacao_home.lambda_bottom_up, macro["lambda_home"], contexto=f"match {match_id} mandante ({fonte})"
            )
            reconciliacao_away = reconciler.reconciliar(
                agregacao_away.lambda_bottom_up, macro["lambda_away"], contexto=f"match {match_id} visitante ({fonte})"
            )
            # Quarentena não bloqueia a Camada 3 (documentado em pricing_
            # pipeline.py) -- a matriz ainda é construída com o lambda_final
            # devolvido pela reconciliação: FORA de quarentena, desde 15/09
            # isso é a média ponderada macro/bottom-up (não mais igual ao
            # macro puro -- é assim que GSAx e outras features da Camada 1
            # passam a mexer no preço de verdade); EM quarentena, continua
            # sendo o lambda_macro puro (documentado na classe). Como este
            # runner só grava probabilidade (não decide aposta), não há
            # Camada 4 aqui pra propagar quarantine_flag em stake=0 -- fica
            # registrado só via log.
            if reconciliacao_home.quarantine_flag or reconciliacao_away.quarantine_flag:
                logger.warning(
                    "Partida %s (fonte=%s): reconciliação em quarentena (home kappa=%s, away kappa=%s) -- "
                    "gravando mesmo assim (Camada 3 usa lambda_macro puro nesse lado), sem decisão de aposta.",
                    match_id, fonte, reconciliacao_home.kappa, reconciliacao_away.kappa,
                )

            resultado = engine.gerar(reconciliacao_home.lambda_final, reconciliacao_away.lambda_final, macro["rho_liga"])

            # Mercado de assistências (25/09, sem odds no sistema ainda --
            # ver `distribuicoes.mercados_de_assistencias`/`CONTEXTO_
            # PROJETO.md`) -- λ vem direto da Camada 1 (soma de todo o
            # elenco, não passa por reconciliação com macro porque não há
            # modelo macro de assistências pra reconciliar contra).
            mercados_assistencias = dist.mercados_de_assistencias(
                agregacao_home.lambda_assistencias_total, agregacao_away.lambda_assistencias_total
            )
            todos_mercados = {**resultado.mercados, **mercados_assistencias}

            # `pricing_pipeline_{fonte}_v1` sempre; `MODEL_NAME_SAIDA` (nome
            # original) só na fonte "melhor disponível" -- ver docstring do
            # módulo (comparação real vs. prevista).
            nomes_modelo = [f"pricing_pipeline_{fonte}_v1"]
            if fonte == fonte_melhor:
                nomes_modelo.append(MODEL_NAME_SAIDA)

            for nome_modelo in nomes_modelo:
                for (mercado, selecao), probabilidade in todos_mercados.items():
                    linhas_saida.append({
                        "match_id": match_id, "model_name": nome_modelo, "market": mercado,
                        "selection": selecao, "probability": round(_clamp_probabilidade(probabilidade), 5),
                    })
                estimativas_saida.append({
                    "match_id": match_id, "model_name": nome_modelo,
                    "params": {
                        "lambda_home": round(reconciliacao_home.lambda_final, 4),
                        "lambda_away": round(reconciliacao_away.lambda_final, 4),
                        "rho": round(float(resultado.rho_efetivo), 4),
                        "lambda_assistencias_home": round(agregacao_home.lambda_assistencias_total, 4),
                        "lambda_assistencias_away": round(agregacao_away.lambda_assistencias_total, 4),
                    },
                })
            processou_alguma_fonte = True

        if processou_alguma_fonte:
            partidas_processadas += 1
        else:
            logger.info("Partida %s sem elenco dos 2 times em nenhuma fonte de player_match_estimates -- pulando.", match_id)

    if not linhas_saida:
        logger.info("Nenhuma linha gerada.")
        return 0

    for i in range(0, len(estimativas_saida), 500):
        lote = estimativas_saida[i : i + 500]
        supabase.table("model_match_estimates").upsert(lote, on_conflict="match_id,model_name").execute()
    for i in range(0, len(linhas_saida), 500):
        lote = linhas_saida[i : i + 500]
        supabase.table("model_predictions").upsert(lote, on_conflict="match_id,model_name,market,selection").execute()

    logger.info(
        "%d partidas gravadas em model_match_estimates (params) + %d linhas em model_predictions "
        "(%d de %d partidas na janela).",
        len(estimativas_saida), len(linhas_saida), partidas_processadas, len(fixtures),
    )
    return len(linhas_saida)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=7, help="janela de dias (à frente por padrão; pra trás com --backtest) -- default 7")
    ap.add_argument("--match-ids", type=str, default=None, help="lista de match_id separados por vírgula -- ignora --dias quando presente")
    ap.add_argument(
        "--backtest", action="store_true",
        help="processa partidas 'finished' (últimos --dias dias, ou --match-ids explícito) em vez de 'scheduled' -- ver docstring do módulo",
    )
    args = ap.parse_args()

    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_KEY"].strip()
    sb = create_client(url, key)

    ids = [int(x) for x in args.match_ids.split(",")] if args.match_ids else None
    rodar(sb, args.dias, ids, backtest=args.backtest)
