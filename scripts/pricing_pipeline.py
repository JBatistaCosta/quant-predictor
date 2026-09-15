#!/usr/bin/env python3
"""Pipeline de precificação em 4 camadas sequenciais, de estimativas por
jogador até ordem de aposta — micro (jogador) -> macro (equipe/liga) ->
matriz conjunta (Dixon-Coles) -> decisão financeira.

    CAMADA 1 (`PlayerToTeamAggregator`): agrega os lambdas por jogador de
        `player_match_estimates` (mesma tabela que `src/pages/
        AnaliseAvancadaEvento.jsx` já lê pra tabela "Chutes & gols por
        jogador", ver `scripts/rodar_jogador_mercados_previsto.py`) num
        lambda de gols "bottom-up" por time.
    CAMADA 2 (`HierarchicalReconciler`): confronta o lambda bottom-up com o
        lambda macro do modelo de equipe/liga — funciona como PORTÃO DE
        SANIDADE (ver docstring da classe), não como média ponderada.
    CAMADA 3 (`DixonColesJointEngine`): monta a matriz conjunta de placares
        e deriva TODOS os mercados dela — reaproveita `distribuicoes.py`
        (matriz_placares/mercados_de_gols), já validado com paridade
        JS/SQL, em vez de reimplementar Dixon-Coles.
    CAMADA 4 (`EnsembleVetoManager`): confronta as probabilidades da matriz
        contra as odds de mercado devigadas, aplica os protocolos de veto
        (incoerência direcional, zebra longa, quarentena) e decide stake —
        reaproveita devig e Kelly fracionado por faixa de odd de
        `backtest_kelly.py`, já validados em produção.

Este módulo é PURO (mesma disciplina de `distribuicoes.py`/`modelo_dixon_
coles.py`) — não conhece Supabase, não faz I/O, recebe tudo por parâmetro.
Isso é o que permite testar as identidades matemáticas em `pytest` sem
tocar no banco.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

import backtest_kelly as bk
import distribuicoes as dist

LOGGER = logging.getLogger("pricing_pipeline")

# ---------------------------------------------------------------------------
# Camada 1 -- constantes
# ---------------------------------------------------------------------------
# Restrição de minutagem: soma de minutos esperados do elenco selecionado
# (11 titulares + reservas do Método 3, ver MAPA_PAPEL_SUBSTITUICAO)
# deveria ficar perto de 90*11=990 + acréscimos -- a banda pedida (1100-
# 1150) é generosa o bastante pra cobrir os ~10-20min extras que os 5
# reservas contribuem por cima do que os titulares "cedem" ao saírem, sem
# ser tão apertada que qualquer imprecisão de escalação dispare o aviso.
MINUTOS_ESPERADOS_MIN_DEFAULT = 1100.0
MINUTOS_ESPERADOS_MAX_DEFAULT = 1150.0

# "5 suplentes mais prováveis de entrar" -- Método 3 (heurística setorial),
# escolhido entre 3 opções (ver plano da sessão): 1 reserva por papel
# padrão de substituição, mapeado por `posicao_detalhe` (código FotMob já
# em `player_match_estimates`, mesmo dicionário de
# `AnaliseAvancadaEvento.jsx::POSICAO_FINA_CURTA`). Método 1 (pareamento
# 1-para-1 por slot titular/reserva) e Método 2 (matriz P(Entrar)
# normalizada) ficam registrados pra uma iteração futura -- exigem lógica
# de casamento por formação e um modelo de probabilidade de substituição
# que não existem hoje, respectivamente.
#
# `CDM` entra só no papel defensivo ("zagueiro_volante_marcador"), nunca em
# "meia_ofensivo" -- FotMob não distingue volante de armação de volante
# marcador (só tem 1 código `CDM`), então contar o mesmo código nos 2
# papéis dobraria a chance de um médio defensivo ser escolhido.
MAPA_PAPEL_SUBSTITUICAO: dict[str, str] = {
    "ST": "centroavante",
    "RW": "ponta", "LW": "ponta", "RM": "ponta", "LM": "ponta",
    "CAM": "meia_ofensivo", "CM": "meia_ofensivo",
    "RB": "lateral", "LB": "lateral", "RWB": "lateral", "LWB": "lateral",
    "CB": "zagueiro_volante_marcador", "CDM": "zagueiro_volante_marcador",
}
# Minuto de entrada esperado por papel (meio da faixa pedida) -- goleiro
# reserva fica de fora do mapa de propósito, sempre E[M]=0.
MINUTO_ENTRADA_POR_PAPEL: dict[str, float] = {
    "centroavante": 70.0,
    "ponta": 65.0,
    "meia_ofensivo": 72.5,
    "lateral": 77.5,
    "zagueiro_volante_marcador": 85.0,
}
DURACAO_REGULAMENTAR = 90.0

# ---------------------------------------------------------------------------
# Camada 2 -- constantes
# ---------------------------------------------------------------------------
KAPPA_MIN = 0.80
KAPPA_MAX = 1.20
LAMBDA_BOTTOM_UP_MINIMO = 1e-9
# Peso do lambda MACRO na média ponderada (dentro da faixa de kappa aceita)
# -- default conservador de propósito: o modelo macro (equipe/liga) é o
# mais validado do projeto, o bottom-up (soma de estimativas por jogador)
# é mais novo e mais ruidoso. Ajustável/testável via backtest
# (`rodar_pricing_pipeline.py --backtest`), não fixado por autoridade
# nenhuma -- só um ponto de partida seguro.
PESO_MACRO_DEFAULT = 0.80

# ---------------------------------------------------------------------------
# Camada 3 -- constantes
# ---------------------------------------------------------------------------
# 10x10 (placares 0-9) pedido explicitamente -- `distribuicoes.MAX_GOLS`
# (10, matriz 11x11) é o padrão do resto do projeto; aqui passamos
# max_gols=9 pra bater com o enunciado.
MAX_GOLS_MATRIZ = 9

# ---------------------------------------------------------------------------
# Camada 4 -- constantes
# ---------------------------------------------------------------------------
# "Alta certeza" do modelo macro no veto por incoerência direcional -- o
# enunciado não dá um número, 0.60 é um default documentado e ajustável.
CONFIANCA_MACRO_MINIMA = 0.60
ODD_LONGA_LIMIAR = 3.50
EDGE_MINIMO_ZEBRA = 0.05
# A trava de zebra longa só faz sentido pra seleções com noção de
# "mandante/visitante/empate" (1X2, dupla_chance, handicap) -- over/btts
# não têm lado "zebra".
SELECOES_ZEBRA = {"away", "draw"}


# ---------------------------------------------------------------------------
# Dataclasses de saída
# ---------------------------------------------------------------------------
@dataclass
class AgregacaoResultado:
    """Saída da Camada 1 pra um time."""

    lambda_chutes_total: float
    lambda_alvo_total: float
    lambda_xg_total: float
    lambda_xgot_total: float
    lambda_thinning: float
    lambda_gols_xgot: float
    lambda_bottom_up: float
    soma_minutos_esperados: float
    minutagem_valida: bool
    avisos: list[str] = field(default_factory=list)


@dataclass
class ReconciliacaoResultado:
    """Saída da Camada 2 pra um time."""

    lambda_final: float
    kappa: Optional[float]
    quarantine_flag: bool
    motivo: Optional[str] = None


@dataclass
class DixonColesResultado:
    """Saída da Camada 3."""

    matriz: np.ndarray
    mercados: dict[tuple[str, str], float]
    rho_efetivo: float
    rho_ajustado: bool


@dataclass
class SelecaoAvaliada:
    """Uma linha da saída da Camada 4 -- 1 seleção de 1 mercado."""

    mercado: str
    selecao: str
    prob_modelo: float
    prob_mercado_devig: float
    odd_justa: float
    odd_real: float
    edge: float
    ev_pct: float
    stake_pct: float
    status: str


COLUNAS_ORDENS = [
    "mercado", "selecao", "prob_modelo", "prob_mercado_devig",
    "odd_justa", "odd_real", "edge", "ev_pct", "stake_pct", "status",
]


# ---------------------------------------------------------------------------
# Camada 1
# ---------------------------------------------------------------------------
class PlayerToTeamAggregator:
    """Agrega estimativas por jogador (Camada Proprietária Micro) num
    lambda de gols "bottom-up" por time, pronto pra reconciliar contra o
    modelo macro na Camada 2.

    Espera um DataFrame com uma linha por jogador do MESMO time, no
    vocabulário de `player_match_estimates`: `player_id`,
    `is_titular_previsto`, `prob_titular_usada`, `posicao_detalhe`,
    `minutos_esperados`, `lambda_chutes_jogo`, `lambda_chutes_no_alvo_jogo`,
    `lambda_xg_jogo`, `taxa_conversao_bayesiana` e, opcionalmente,
    `delta_shooting` (xGOT-xG histórico do jogador -- ainda não existe em
    `player_match_estimates` hoje, default 0.0 quando ausente).
    """

    def __init__(
        self,
        minutos_min: float = MINUTOS_ESPERADOS_MIN_DEFAULT,
        minutos_max: float = MINUTOS_ESPERADOS_MAX_DEFAULT,
    ) -> None:
        self.minutos_min = minutos_min
        self.minutos_max = minutos_max

    # -- seleção do elenco provável (Método 3) ------------------------------
    def selecionar_elenco_provavel(self, jogadores: pd.DataFrame) -> pd.DataFrame:
        """Titulares (`is_titular_previsto`) + no máximo 1 reserva por papel
        de substituição padrão (ver `MAPA_PAPEL_SUBSTITUICAO`), escolhido
        dentro do papel pelo maior `prob_titular_usada` (proxy de "reserva
        principal" já disponível, sem precisar de modelo novo de P(Entrar)).

        Papel sem nenhum reserva correspondente contribui `E[M]=0` (fica de
        fora, não promove outro papel pra compensar) -- inclusive o goleiro
        reserva, que nunca tem papel mapeado. O `minutos_esperados` de cada
        reserva selecionado é recalculado como `90 - minuto_de_entrada_do_
        papel`: o valor que já vinha na tabela é uma média histórica
        genérica de banco, não a estimativa por papel que este método pede.
        """
        if jogadores.empty:
            raise ValueError("`jogadores` está vazio -- nenhum jogador pra agregar.")
        obrigatorias = {"player_id", "is_titular_previsto", "posicao_detalhe", "prob_titular_usada"}
        faltando = obrigatorias - set(jogadores.columns)
        if faltando:
            raise ValueError(f"`jogadores` sem colunas obrigatórias: {sorted(faltando)}")

        titular_mask = jogadores["is_titular_previsto"].fillna(False).astype(bool)
        titulares = jogadores[titular_mask].copy()
        banco = jogadores[~titular_mask].copy()

        banco["_papel"] = banco["posicao_detalhe"].map(MAPA_PAPEL_SUBSTITUICAO)
        banco = banco.dropna(subset=["_papel"])

        reservas_selecionadas = []
        for papel, grupo in banco.groupby("_papel"):
            linha = grupo.sort_values("prob_titular_usada", ascending=False).iloc[0].copy()
            linha["minutos_esperados"] = DURACAO_REGULAMENTAR - MINUTO_ENTRADA_POR_PAPEL[papel]
            reservas_selecionadas.append(linha)

        if not reservas_selecionadas:
            return titulares.reset_index(drop=True)
        reservas = pd.DataFrame(reservas_selecionadas).drop(columns=["_papel"])
        return pd.concat([titulares, reservas], ignore_index=True)

    # -- restrição de minutagem ---------------------------------------------
    def validar_minutagem(self, elenco: pd.DataFrame) -> tuple[float, bool, list[str]]:
        avisos: list[str] = []
        if "minutos_esperados" not in elenco.columns:
            raise ValueError("`jogadores` sem coluna `minutos_esperados`.")
        minutos = pd.to_numeric(elenco["minutos_esperados"], errors="coerce")
        if minutos.isna().any():
            avisos.append("minutos_esperados com valor não-numérico tratado como 0.")
        soma = float(minutos.fillna(0.0).sum())
        valida = self.minutos_min <= soma <= self.minutos_max
        if not valida:
            avisos.append(
                f"soma de minutos_esperados ({soma:.1f}) fora da banda "
                f"[{self.minutos_min:.0f}, {self.minutos_max:.0f}]."
            )
        return soma, valida, avisos

    @staticmethod
    def _coluna_numerica(elenco: pd.DataFrame, coluna: str, avisos: list[str]) -> np.ndarray:
        """Lê uma coluna numérica do elenco com robustez: coluna ausente ou
        valor não-numérico vira 0.0 com aviso coletado, nunca exceção --
        só odd/probabilidade/CSV vazio abortam o pipeline (ver Camada 4 e
        `process_match`)."""
        if coluna not in elenco.columns:
            avisos.append(f"coluna `{coluna}` ausente -- tratada como 0 pra todo o elenco.")
            return np.zeros(len(elenco))
        valores = pd.to_numeric(elenco[coluna], errors="coerce")
        if valores.isna().any():
            avisos.append(f"coluna `{coluna}` com valor não-numérico tratado como 0.")
        return valores.fillna(0.0).to_numpy(dtype=float)

    # -- agregação de volume --------------------------------------------------
    def agregar_volume(self, elenco: pd.DataFrame, avisos: list[str]) -> tuple[float, float, float]:
        """λ_chutes = Σλ_chutes,k, λ_alvo = Σλ_alvo,k, λ_xG = Σλ_xG,k."""
        lambda_chutes = self._coluna_numerica(elenco, "lambda_chutes_jogo", avisos)
        lambda_alvo = self._coluna_numerica(elenco, "lambda_chutes_no_alvo_jogo", avisos)
        lambda_xg = self._coluna_numerica(elenco, "lambda_xg_jogo", avisos)
        return float(lambda_chutes.sum()), float(lambda_alvo.sum()), float(lambda_xg.sum())

    # -- modelagem de xGOT -----------------------------------------------------
    def agregar_xgot(self, elenco: pd.DataFrame, avisos: list[str]) -> float:
        """λ_xGOT = Σ(λ_xG,k + Δshooting,k) -- cada termo clipado em ≥0 antes
        de somar (evita que uma habilidade de chute muito negativa produza
        uma contribuição negativa sem sentido pro total)."""
        lambda_xg = self._coluna_numerica(elenco, "lambda_xg_jogo", avisos)
        delta_shooting = self._coluna_numerica(elenco, "delta_shooting", avisos)
        termos = np.clip(lambda_xg + delta_shooting, 0.0, None)
        return float(termos.sum())

    # -- modulação pelo goleiro adversário --------------------------------------
    @staticmethod
    def modular_por_goleiro(lambda_xgot: float, gsax_rate: float) -> float:
        """λ_gols,xGOT = λ_xGOT · (1 - GSAx_rate_goleiro_adv). `gsax_rate`
        clipado a [-1,1] (fora disso o fator (1-gsax_rate) vira negativo ou
        >2, sem sentido físico); resultado nunca fica negativo."""
        gsax_rate = float(np.clip(gsax_rate, -1.0, 1.0))
        return float(max(0.0, lambda_xgot * (1.0 - gsax_rate)))

    # -- afinamento de Poisson (thinning) --------------------------------------
    def afinar_poisson(self, elenco: pd.DataFrame, avisos: list[str]) -> float:
        """λ_thinning = Σ(λ_chutes,k · θ_conv,k), θ_conv,k clipado a [0,1]."""
        lambda_chutes = self._coluna_numerica(elenco, "lambda_chutes_jogo", avisos)
        taxa_conversao = np.clip(self._coluna_numerica(elenco, "taxa_conversao_bayesiana", avisos), 0.0, 1.0)
        return float((lambda_chutes * taxa_conversao).sum())

    # -- consolidação -----------------------------------------------------------
    def agregar(self, jogadores: pd.DataFrame, gsax_rate_adversario: float = 0.0) -> AgregacaoResultado:
        """Orquestra as 6 etapas da Camada 1 e devolve
        λ_bottom-up = 0.5·λ_thinning + 0.5·λ_gols,xGOT."""
        elenco = self.selecionar_elenco_provavel(jogadores)
        avisos: list[str] = []
        soma_minutos, minutagem_valida, avisos_minutagem = self.validar_minutagem(elenco)
        avisos.extend(avisos_minutagem)

        lambda_chutes_total, lambda_alvo_total, lambda_xg_total = self.agregar_volume(elenco, avisos)
        lambda_xgot_total = self.agregar_xgot(elenco, avisos)
        lambda_gols_xgot = self.modular_por_goleiro(lambda_xgot_total, gsax_rate_adversario)
        lambda_thinning = self.afinar_poisson(elenco, avisos)
        lambda_bottom_up = 0.5 * lambda_thinning + 0.5 * lambda_gols_xgot

        return AgregacaoResultado(
            lambda_chutes_total=lambda_chutes_total,
            lambda_alvo_total=lambda_alvo_total,
            lambda_xg_total=lambda_xg_total,
            lambda_xgot_total=lambda_xgot_total,
            lambda_thinning=lambda_thinning,
            lambda_gols_xgot=lambda_gols_xgot,
            lambda_bottom_up=lambda_bottom_up,
            soma_minutos_esperados=soma_minutos,
            minutagem_valida=minutagem_valida,
            avisos=avisos,
        )


# ---------------------------------------------------------------------------
# Camada 2
# ---------------------------------------------------------------------------
class HierarchicalReconciler:
    """Confronta o λ bottom-up (Camada 1) com o λ macro do modelo de
    equipe/liga.

    Até 15/09 esta camada era um PORTÃO DE SANIDADE puro: `lambda_final =
    lambda_bottom_up * kappa = lambda_macro` sempre que κ era aceito, então
    nenhuma feature calculada só na Camada 1 (ex. GSAx de goleiro, ver
    `PlayerToTeamAggregator.modular_por_goleiro`) chegava a mudar o preço
    de verdade -- só podia empurrar a partida pra quarentena. Trocado a
    pedido do usuário por uma MÉDIA PONDERADA de verdade dentro da faixa
    aceita: `lambda_final = peso_macro*lambda_macro + (1-peso_macro)*
    lambda_bottom_up`. `peso_macro` alto por padrão (`PESO_MACRO_DEFAULT`)
    -- o modelo macro é o mais validado/estável do projeto, o bottom-up é
    mais novo e mais ruidoso (soma de estimativas individuais por
    jogador, cada uma com sua própria incerteza), então a mudança é uma
    correção pequena, não uma substituição.

    A quarentena continua sendo a rede de segurança pros casos em que o
    bottom-up diverge demais do macro (κ fora de [`kappa_min`,
    `kappa_max`]) OU é degenerado -- nesses casos `lambda_final` continua
    sendo o `lambda_macro` puro, sem nenhuma mistura (não faz sentido
    confiar parcialmente num bottom-up que já falhou o teste de
    plausibilidade). `quarantine_flag=True` também segue sendo o sinal que
    a Camada 4 usa pra impedir aposta automática naquele lado.
    """

    def __init__(
        self,
        kappa_min: float = KAPPA_MIN,
        kappa_max: float = KAPPA_MAX,
        peso_macro: float = PESO_MACRO_DEFAULT,
    ) -> None:
        if not 0.0 < peso_macro <= 1.0:
            raise ValueError(f"peso_macro precisa estar em (0, 1]: {peso_macro}")
        self.kappa_min = kappa_min
        self.kappa_max = kappa_max
        self.peso_macro = peso_macro

    def reconciliar(
        self, lambda_bottom_up: float, lambda_macro: float, contexto: str = ""
    ) -> ReconciliacaoResultado:
        if lambda_bottom_up is None or not np.isfinite(lambda_bottom_up) or lambda_bottom_up <= LAMBDA_BOTTOM_UP_MINIMO:
            motivo = f"lambda_bottom_up degenerado ({lambda_bottom_up!r}) -- quarentena imediata."
            LOGGER.warning("Reconciliação [%s]: %s", contexto, motivo)
            return ReconciliacaoResultado(
                lambda_final=lambda_macro, kappa=None, quarantine_flag=True, motivo=motivo
            )

        kappa = lambda_macro / lambda_bottom_up

        if self.kappa_min <= kappa <= self.kappa_max:
            lambda_final = self.peso_macro * lambda_macro + (1.0 - self.peso_macro) * lambda_bottom_up
            return ReconciliacaoResultado(lambda_final=lambda_final, kappa=kappa, quarantine_flag=False)

        motivo = (
            f"kappa={kappa:.3f} fora de [{self.kappa_min}, {self.kappa_max}] "
            f"(lambda_bottom_up={lambda_bottom_up:.3f}, lambda_macro={lambda_macro:.3f}) -- "
            "elenco (bottom-up) e modelo de equipe (macro) divergem demais, quarentena acionada."
        )
        LOGGER.warning("Reconciliação [%s] em quarentena: %s", contexto, motivo)
        # Quarentenado -- sem mistura, lambda_macro puro (ver docstring da classe).
        return ReconciliacaoResultado(
            lambda_final=lambda_macro, kappa=kappa, quarantine_flag=True, motivo=motivo
        )


# ---------------------------------------------------------------------------
# Camada 3
# ---------------------------------------------------------------------------
class DixonColesJointEngine:
    """Constrói a matriz conjunta de placares (Dixon-Coles) a partir dos λ
    reconciliados e extrai TODOS os mercados dela — reaproveita
    `distribuicoes.py` (`matriz_placares`/`mercados_de_gols`, já validado
    com paridade JS/SQL) em vez de reimplementar a matemática. Como todo
    mercado sai da MESMA matriz, as identidades (soma-1, complementaridade
    over/under) valem por construção, não por coincidência.
    """

    def __init__(self, max_gols: int = MAX_GOLS_MATRIZ) -> None:
        self.max_gols = max_gols

    @staticmethod
    def validar_rho(lam: float, mu: float, rho: float) -> tuple[float, bool]:
        """Clipa ρ pro intervalo de validade
        `max(-1/λ, -1/μ) <= ρ <= min(1/(λμ), 1)` ANTES de construir a
        matriz -- `distribuicoes.matriz_placares` já clipa célula negativa
        por trás como 2ª rede de segurança, mas resolver aqui na frente dá
        um sinal explícito (`rho_ajustado`) de quando isso aconteceu."""
        if lam <= 0 or mu <= 0:
            raise ValueError(f"lambda/mu precisam ser positivos (lam={lam}, mu={mu}).")
        limite_inf = max(-1.0 / lam, -1.0 / mu)
        limite_sup = min(1.0 / (lam * mu), 1.0)
        if limite_inf > limite_sup:
            # combinação de lambda/mu tão extrema que não sobra rho válido
            # nenhum -- cai pra independência (rho=0), mais seguro que
            # devolver um intervalo vazio.
            return 0.0, rho != 0.0
        rho_clipado = float(np.clip(rho, limite_inf, limite_sup))
        return rho_clipado, rho_clipado != rho

    def construir_matriz(self, lam: float, mu: float, rho: float) -> np.ndarray:
        return dist.matriz_placares(lam, mu, rho, max_gols=self.max_gols)

    @staticmethod
    def mercados(matriz: np.ndarray) -> dict[tuple[str, str], float]:
        return dist.mercados_de_gols(matriz)

    def gerar(self, lam: float, mu: float, rho: float) -> DixonColesResultado:
        rho_efetivo, rho_ajustado = self.validar_rho(lam, mu, rho)
        matriz = self.construir_matriz(lam, mu, rho_efetivo)
        mercados = self.mercados(matriz)
        return DixonColesResultado(
            matriz=matriz, mercados=mercados, rho_efetivo=rho_efetivo, rho_ajustado=rho_ajustado
        )


# ---------------------------------------------------------------------------
# Camada 4
# ---------------------------------------------------------------------------
class EnsembleVetoManager:
    """Confronta as probabilidades da matriz (Camada 3) contra as odds de
    mercado devigadas e decide ordem de aposta. Reaproveita devig e Kelly
    fracionado por faixa de odd já validados em `backtest_kelly.py` (a
    política de staking em produção, não a fração/teto fixos do enunciado
    original — divergência resolvida explicitamente com o usuário)."""

    def __init__(
        self,
        confianca_macro_minima: float = CONFIANCA_MACRO_MINIMA,
        odd_longa_limiar: float = ODD_LONGA_LIMIAR,
        edge_minimo_zebra: float = EDGE_MINIMO_ZEBRA,
    ) -> None:
        self.confianca_macro_minima = confianca_macro_minima
        self.odd_longa_limiar = odd_longa_limiar
        self.edge_minimo_zebra = edge_minimo_zebra

    @staticmethod
    def devig(odds_selecao: dict[str, float]) -> dict[str, float]:
        for selecao, odd in odds_selecao.items():
            if odd <= 1.0:
                raise ValueError(f"Odd inválida pra `{selecao}`: {odd} (precisa ser > 1.0).")
        return bk._devig_odds_ratio(odds_selecao)

    @staticmethod
    def calcular_edge_ev(p_modelo: float, p_mercado_devig: float, odd_real: float) -> tuple[float, float]:
        if not 0.0 <= p_modelo <= 1.0:
            raise ValueError(f"prob_modelo fora de [0,1]: {p_modelo}")
        edge = p_modelo - p_mercado_devig
        ev_pct = (p_modelo * odd_real - 1.0) * 100.0
        return edge, ev_pct

    @staticmethod
    def calcular_stake(p_modelo: float, odd_real: float) -> float:
        return bk.kelly_fracionario(p_modelo, odd_real)

    def checar_veto_macro(
        self, mercado: str, selecao_micro: str, macro_direction: Optional[dict]
    ) -> bool:
        """Protocolo de veto por incoerência direcional: True quando o
        modelo macro (ex.: XGBoost v11) aponta uma seleção DIFERENTE da
        implícita pela matriz, com confiança >= `confianca_macro_minima`.
        Sem consenso macro informado pra esse mercado, não há o que vetar."""
        if not macro_direction or mercado not in macro_direction:
            return False
        consenso = macro_direction[mercado]
        selecao_macro = consenso.get("selecao")
        confianca = float(consenso.get("confianca", 0.0))
        return bool(
            selecao_macro and selecao_macro != selecao_micro and confianca >= self.confianca_macro_minima
        )

    def checar_trava_zebra(self, selecao: str, odd_real: float, edge: float) -> bool:
        """Trava de zebras longas: veta visitante/empate com odd > 3.50 e
        edge < 5pp, eliminando o viés de cauda longa já identificado nos
        backtests deste projeto."""
        return (
            selecao in SELECOES_ZEBRA
            and odd_real > self.odd_longa_limiar
            and edge < self.edge_minimo_zebra
        )

    def avaliar_selecao(
        self,
        mercado: str,
        selecao: str,
        prob_modelo: float,
        prob_mercado_devig: float,
        odd_real: float,
        quarantine_flag: bool,
        macro_direction: Optional[dict] = None,
    ) -> SelecaoAvaliada:
        """Aplica os vetos na ordem: quarentena -> incoerência direcional ->
        zebra longa -> edge mínimo -> Kelly fracionado."""
        edge, ev_pct = self.calcular_edge_ev(prob_modelo, prob_mercado_devig, odd_real)
        odd_justa = float("inf") if prob_modelo <= 0 else 1.0 / prob_modelo

        if quarantine_flag:
            status, stake = "VETADO_QUARENTENA", 0.0
        elif self.checar_veto_macro(mercado, selecao, macro_direction):
            status, stake = "VETADO_INCOERENCIA", 0.0
        elif self.checar_trava_zebra(selecao, odd_real, edge):
            status, stake = "VETADO_ZEBRA_LONGA", 0.0
        elif edge < bk.EDGE_MINIMO:
            status, stake = "REJEITADA_EDGE", 0.0
        else:
            stake = self.calcular_stake(prob_modelo, odd_real)
            status = "APROVADA" if stake > 0 else "REJEITADA_EDGE"

        return SelecaoAvaliada(
            mercado=mercado, selecao=selecao, prob_modelo=prob_modelo,
            prob_mercado_devig=prob_mercado_devig, odd_justa=odd_justa, odd_real=odd_real,
            edge=edge, ev_pct=ev_pct, stake_pct=stake, status=status,
        )

    def gerar_ordens(
        self,
        mercados: dict[tuple[str, str], float],
        odds_mercado: dict[str, dict[str, float]],
        quarantine_flag: bool,
        macro_direction: Optional[dict] = None,
    ) -> pd.DataFrame:
        linhas = []
        for mercado, odds_selecao in odds_mercado.items():
            if not odds_selecao:
                continue
            try:
                devigadas = self.devig(odds_selecao)
            except ValueError as exc:
                LOGGER.warning("Pulando mercado `%s`: %s", mercado, exc)
                continue
            for selecao, odd_real in odds_selecao.items():
                prob_modelo = mercados.get((mercado, selecao))
                if prob_modelo is None:
                    continue  # mercado/seleção sem contraparte na matriz -- nada a avaliar
                avaliacao = self.avaliar_selecao(
                    mercado, selecao, prob_modelo, devigadas[selecao], odd_real,
                    quarantine_flag, macro_direction,
                )
                linhas.append(avaliacao.__dict__)
        return pd.DataFrame(linhas, columns=COLUNAS_ORDENS)


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------
def process_match(
    jogadores: pd.DataFrame,
    odds_mercado: dict[str, dict[str, float]],
    macro_priors: dict,
    gk_stats: dict[str, dict[str, float]],
    incluir_vetadas: bool = False,
) -> pd.DataFrame:
    """Executa o fluxo completo (Camadas 1-4) pra 1 partida e devolve um
    DataFrame padronizado com as seleções aprovadas (ou todas, com
    `incluir_vetadas=True`): EV(%), Odd Justa, Odd Real e Stake recomendada.

    `jogadores`: DataFrame com 1 linha por jogador dos 2 times (mesmo
    vocabulário de `player_match_estimates`, ver `PlayerToTeamAggregator`),
    precisa da coluna `is_home` (bool) pra separar os lados.
    `gk_stats`: `{"home": {"gsax_rate": float}, "away": {"gsax_rate": float}}`.
    `macro_priors`: `{"lambda_home": float, "lambda_away": float,
    "rho_liga": float, "macro_direction": {...} (opcional)}`.
    `odds_mercado`: `{mercado: {selecao: odd_real}}`, mesmo vocabulário que
    `distribuicoes.mercados_de_gols` produz.
    """
    if "is_home" not in jogadores.columns:
        raise ValueError("`jogadores` sem coluna `is_home`.")
    for chave in ("home", "away"):
        if chave not in gk_stats:
            raise ValueError(f"`gk_stats` sem chave obrigatória `{chave}`.")
    for chave in ("lambda_home", "lambda_away", "rho_liga"):
        if chave not in macro_priors:
            raise ValueError(f"`macro_priors` sem chave obrigatória `{chave}`.")

    is_home_mask = jogadores["is_home"].fillna(False).astype(bool)
    jogadores_home = jogadores[is_home_mask]
    jogadores_away = jogadores[~is_home_mask]
    if jogadores_home.empty or jogadores_away.empty:
        raise ValueError("`jogadores` precisa ter linhas dos 2 times (is_home True e False).")

    agregador = PlayerToTeamAggregator()
    agregacao_home = agregador.agregar(
        jogadores_home, gsax_rate_adversario=gk_stats["away"].get("gsax_rate", 0.0)
    )
    agregacao_away = agregador.agregar(
        jogadores_away, gsax_rate_adversario=gk_stats["home"].get("gsax_rate", 0.0)
    )

    reconciler = HierarchicalReconciler()
    reconciliacao_home = reconciler.reconciliar(
        agregacao_home.lambda_bottom_up, macro_priors["lambda_home"], contexto="mandante"
    )
    reconciliacao_away = reconciler.reconciliar(
        agregacao_away.lambda_bottom_up, macro_priors["lambda_away"], contexto="visitante"
    )

    engine = DixonColesJointEngine()
    resultado_matriz = engine.gerar(
        reconciliacao_home.lambda_final, reconciliacao_away.lambda_final, macro_priors["rho_liga"]
    )

    # Quarentena de qualquer lado veta o jogo inteiro -- a matriz depende
    # dos 2 lambdas, um lado degenerado contamina todos os mercados dela.
    quarantine_flag = reconciliacao_home.quarantine_flag or reconciliacao_away.quarantine_flag

    veto_manager = EnsembleVetoManager()
    ordens = veto_manager.gerar_ordens(
        resultado_matriz.mercados, odds_mercado, quarantine_flag, macro_priors.get("macro_direction")
    )

    if incluir_vetadas:
        return ordens
    return ordens[ordens["status"] == "APROVADA"].reset_index(drop=True)
