#!/usr/bin/env python3
"""Suíte pytest de `pricing_pipeline.py` -- primeira suíte pytest do repo.

Sem mocks de Supabase: o módulo é puro, tudo entra por parâmetro (mesma
disciplina de `distribuicoes.py`). Roda com `python -m pytest
test_pricing_pipeline.py -v` a partir de `scripts/` (ou `pytest
scripts/test_pricing_pipeline.py` da raiz do repo -- pytest insere o
diretório do arquivo de teste em `sys.path` por padrão, mesmo sem
`__init__.py`).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import backtest_kelly as bk
import pricing_pipeline as pp

# ---------------------------------------------------------------------------
# Helpers de dado sintético
# ---------------------------------------------------------------------------
def _linha_jogador(**kwargs) -> dict:
    base = {
        "player_id": 1, "team_id": 10, "is_home": True,
        "is_titular_previsto": True, "prob_titular_usada": 0.9,
        "posicao_detalhe": "ST", "minutos_esperados": 90.0,
        "lambda_chutes_jogo": 2.0, "lambda_chutes_no_alvo_jogo": 0.8,
        "lambda_xg_jogo": 0.3, "taxa_conversao_bayesiana": 0.15,
        "delta_shooting": 0.0,
    }
    base.update(kwargs)
    return base


# 11 titulares, 1 por posição (não precisa ser uma formação "de verdade" --
# só precisa cobrir os códigos de posição usados nos testes).
_POSICOES_TITULARES = ["GK", "CB", "CB", "RB", "LB", "CDM", "CM", "CAM", "RW", "LW", "ST"]
# 1 reserva por papel de substituição (Método 3) + o goleiro reserva (sem
# papel mapeado, deve ficar sempre de fora).
_POSICOES_BANCO_1_POR_PAPEL = ["GK", "CB", "RB", "CAM", "RW", "ST"]


def _titulares(team_id: int, is_home: bool, id_inicial: int = 0) -> list[dict]:
    return [
        _linha_jogador(player_id=id_inicial + i, team_id=team_id, is_home=is_home, posicao_detalhe=pos)
        for i, pos in enumerate(_POSICOES_TITULARES)
    ]


def _banco(team_id: int, is_home: bool, id_inicial: int = 100) -> list[dict]:
    return [
        _linha_jogador(
            player_id=id_inicial + i, team_id=team_id, is_home=is_home,
            is_titular_previsto=False, prob_titular_usada=0.3 + i * 0.05,
            posicao_detalhe=pos, minutos_esperados=15.0,
        )
        for i, pos in enumerate(_POSICOES_BANCO_1_POR_PAPEL)
    ]


def _elenco_completo(team_id: int = 10, is_home: bool = True) -> pd.DataFrame:
    id_inicial_banco = 100 if is_home else 1100
    id_inicial_titulares = 0 if is_home else 1000
    return pd.DataFrame(
        _titulares(team_id, is_home, id_inicial_titulares) + _banco(team_id, is_home, id_inicial_banco)
    )


# ---------------------------------------------------------------------------
# Camada 1 -- PlayerToTeamAggregator
# ---------------------------------------------------------------------------
class TestPlayerToTeamAggregator:
    def test_jogadores_vazio_levanta_valueerror(self):
        with pytest.raises(ValueError):
            pp.PlayerToTeamAggregator().selecionar_elenco_provavel(pd.DataFrame())

    def test_coluna_obrigatoria_ausente_levanta_valueerror(self):
        elenco = _elenco_completo().drop(columns=["posicao_detalhe"])
        with pytest.raises(ValueError):
            pp.PlayerToTeamAggregator().selecionar_elenco_provavel(elenco)

    def test_selecao_titulares_mais_1_reserva_por_papel(self):
        elenco = _elenco_completo()
        selecionado = pp.PlayerToTeamAggregator().selecionar_elenco_provavel(elenco)
        titulares = selecionado[selecionado["is_titular_previsto"] == True]  # noqa: E712
        banco = selecionado[selecionado["is_titular_previsto"] == False]  # noqa: E712
        assert len(titulares) == 11
        # GK do banco não tem papel mapeado -- fica de fora; os outros 5
        # (CB, RB, CAM, RW, ST) cobrem os 5 papéis de MAPA_PAPEL_SUBSTITUICAO.
        assert len(banco) == 5
        assert set(banco["posicao_detalhe"]) == {"CB", "RB", "CAM", "RW", "ST"}

    def test_papel_sem_reserva_correspondente_fica_de_fora(self):
        elenco = pd.DataFrame(
            _titulares(10, True)
            + [_linha_jogador(player_id=200, is_titular_previsto=False, posicao_detalhe="GK", prob_titular_usada=0.1)]
        )
        selecionado = pp.PlayerToTeamAggregator().selecionar_elenco_provavel(elenco)
        assert len(selecionado) == 11  # só titulares -- GK reserva nunca entra

    def test_papel_com_2_candidatos_escolhe_maior_prob_titular_usada(self):
        elenco = pd.DataFrame(
            _titulares(10, True)
            + [
                _linha_jogador(player_id=201, is_titular_previsto=False, posicao_detalhe="ST", prob_titular_usada=0.2),
                _linha_jogador(player_id=202, is_titular_previsto=False, posicao_detalhe="ST", prob_titular_usada=0.6),
            ]
        )
        selecionado = pp.PlayerToTeamAggregator().selecionar_elenco_provavel(elenco)
        banco = selecionado[selecionado["is_titular_previsto"] == False]  # noqa: E712
        assert list(banco["player_id"]) == [202]

    def test_minutos_esperados_reserva_e_complemento_do_minuto_de_entrada(self):
        elenco = pd.DataFrame(
            _titulares(10, True)
            + [_linha_jogador(player_id=201, is_titular_previsto=False, posicao_detalhe="ST", prob_titular_usada=0.5, minutos_esperados=999.0)]
        )
        selecionado = pp.PlayerToTeamAggregator().selecionar_elenco_provavel(elenco)
        linha = selecionado[selecionado["player_id"] == 201].iloc[0]
        assert linha["minutos_esperados"] == pytest.approx(90.0 - pp.MINUTO_ENTRADA_POR_PAPEL["centroavante"])

    def test_cdm_conta_so_no_papel_defensivo(self):
        # CDM só deve poder ser escolhido pro papel "zagueiro_volante_marcador",
        # nunca "meia_ofensivo" (ver comentário em MAPA_PAPEL_SUBSTITUICAO).
        assert pp.MAPA_PAPEL_SUBSTITUICAO["CDM"] == "zagueiro_volante_marcador"
        assert pp.MAPA_PAPEL_SUBSTITUICAO["CAM"] == "meia_ofensivo"

    def test_minutagem_fora_da_banda_gera_aviso_sem_crashar(self):
        elenco = _elenco_completo()  # 11 titulares * 90 = 990 < banda [1100,1150]
        resultado = pp.PlayerToTeamAggregator().agregar(elenco)
        assert resultado.minutagem_valida is False
        assert any("banda" in aviso for aviso in resultado.avisos)

    def test_minutagem_dentro_da_banda(self):
        # 11 titulares*90 (990) + 5 reservas do Método 3 (90-70 + 90-65 +
        # 90-72.5 + 90-77.5 + 90-85 = 80) = 1070.
        agregador = pp.PlayerToTeamAggregator(minutos_min=1000.0, minutos_max=1100.0)
        elenco = _elenco_completo()
        soma, valida, avisos = agregador.validar_minutagem(
            agregador.selecionar_elenco_provavel(elenco)
        )
        assert soma == pytest.approx(1070.0)
        assert valida is True
        assert avisos == []

    def test_limites_exatos_da_banda_sao_validos(self):
        agregador = pp.PlayerToTeamAggregator(minutos_min=990.0, minutos_max=990.0)
        elenco = pd.DataFrame(_titulares(10, True))  # 11*90 = 990 exato
        _, valida, _ = agregador.validar_minutagem(elenco)
        assert valida is True

    def test_coluna_ausente_vira_zero_com_aviso(self):
        elenco = _elenco_completo().drop(columns=["lambda_xg_jogo"])
        resultado = pp.PlayerToTeamAggregator().agregar(elenco)
        assert resultado.lambda_xg_total == 0.0
        assert any("lambda_xg_jogo" in aviso for aviso in resultado.avisos)

    def test_nan_vira_zero_sem_excecao(self):
        elenco = _elenco_completo()
        elenco.loc[0, "lambda_chutes_jogo"] = np.nan
        resultado = pp.PlayerToTeamAggregator().agregar(elenco)
        assert np.isfinite(resultado.lambda_chutes_total)

    def test_gsax_positivo_reduz_lambda_gols_xgot(self):
        elenco = _elenco_completo()
        agregador = pp.PlayerToTeamAggregator()
        sem_goleiro_bom = agregador.agregar(elenco, gsax_rate_adversario=0.0)
        com_goleiro_bom = agregador.agregar(elenco, gsax_rate_adversario=0.2)
        assert com_goleiro_bom.lambda_gols_xgot < sem_goleiro_bom.lambda_gols_xgot

    def test_gsax_negativo_aumenta_lambda_gols_xgot(self):
        elenco = _elenco_completo()
        agregador = pp.PlayerToTeamAggregator()
        base = agregador.agregar(elenco, gsax_rate_adversario=0.0)
        goleiro_ruim = agregador.agregar(elenco, gsax_rate_adversario=-0.2)
        assert goleiro_ruim.lambda_gols_xgot > base.lambda_gols_xgot

    def test_gsax_fora_de_1_1_e_clipado_sem_lambda_negativo(self):
        assert pp.PlayerToTeamAggregator.modular_por_goleiro(1.0, gsax_rate=5.0) == pytest.approx(0.0)
        assert pp.PlayerToTeamAggregator.modular_por_goleiro(1.0, gsax_rate=-5.0) == pytest.approx(2.0)

    def test_lambda_bottom_up_e_media_dos_2_componentes(self):
        elenco = _elenco_completo()
        resultado = pp.PlayerToTeamAggregator().agregar(elenco)
        esperado = 0.5 * resultado.lambda_thinning + 0.5 * resultado.lambda_gols_xgot
        assert resultado.lambda_bottom_up == pytest.approx(esperado)


# ---------------------------------------------------------------------------
# Camada 2 -- HierarchicalReconciler
# ---------------------------------------------------------------------------
class TestHierarchicalReconciler:
    def test_kappa_valido_lambda_final_e_media_ponderada(self):
        reconciler = pp.HierarchicalReconciler()  # peso_macro default
        r = reconciler.reconciliar(lambda_bottom_up=1.5, lambda_macro=1.6)
        assert r.quarantine_flag is False
        assert r.kappa == pytest.approx(1.6 / 1.5)
        esperado = reconciler.peso_macro * 1.6 + (1.0 - reconciler.peso_macro) * 1.5
        assert r.lambda_final == pytest.approx(esperado)
        assert r.lambda_final != pytest.approx(1.6)  # não colapsa mais pro macro puro

    def test_peso_macro_customizado_muda_lambda_final(self):
        r = pp.HierarchicalReconciler(peso_macro=0.5).reconciliar(lambda_bottom_up=1.0, lambda_macro=1.1)
        # kappa = 1.1, dentro da banda default [0.8, 1.2] -- média 50/50.
        assert r.quarantine_flag is False
        assert r.lambda_final == pytest.approx(0.5 * 1.1 + 0.5 * 1.0)

    def test_peso_macro_invalido_levanta_valueerror(self):
        with pytest.raises(ValueError):
            pp.HierarchicalReconciler(peso_macro=0.0)
        with pytest.raises(ValueError):
            pp.HierarchicalReconciler(peso_macro=1.5)

    def test_kappa_abaixo_do_limite_aciona_quarentena(self):
        r = pp.HierarchicalReconciler().reconciliar(lambda_bottom_up=2.0, lambda_macro=1.0)  # kappa=0.5
        assert r.quarantine_flag is True
        assert r.motivo is not None
        assert r.lambda_final == pytest.approx(1.0)  # quarentenado -- macro puro, sem mistura

    def test_kappa_acima_do_limite_aciona_quarentena(self):
        r = pp.HierarchicalReconciler().reconciliar(lambda_bottom_up=1.0, lambda_macro=2.0)  # kappa=2.0
        assert r.quarantine_flag is True
        assert r.lambda_final == pytest.approx(2.0)  # quarentenado -- macro puro, sem mistura

    def test_limites_exatos_de_kappa_sao_validos(self):
        reconciler = pp.HierarchicalReconciler()
        r_min = reconciler.reconciliar(lambda_bottom_up=1.0, lambda_macro=0.80)
        r_max = reconciler.reconciliar(lambda_bottom_up=1.0, lambda_macro=1.20)
        assert r_min.quarantine_flag is False
        assert r_max.quarantine_flag is False
        assert r_min.lambda_final == pytest.approx(reconciler.peso_macro * 0.80 + (1 - reconciler.peso_macro) * 1.0)
        assert r_max.lambda_final == pytest.approx(reconciler.peso_macro * 1.20 + (1 - reconciler.peso_macro) * 1.0)

    def test_lambda_bottom_up_degenerado_nao_crasha(self):
        r = pp.HierarchicalReconciler().reconciliar(lambda_bottom_up=0.0, lambda_macro=1.5)
        assert r.quarantine_flag is True
        assert r.kappa is None
        assert r.lambda_final == pytest.approx(1.5)

    def test_lambda_bottom_up_nan_nao_crasha(self):
        r = pp.HierarchicalReconciler().reconciliar(lambda_bottom_up=float("nan"), lambda_macro=1.5)
        assert r.quarantine_flag is True

    def test_gsax_do_goleiro_adversario_move_lambda_final(self):
        """Fim-a-fim Camada 1 -> Camada 2: GSAx do goleiro adversário muda
        lambda_bottom_up o bastante pra lambda_final divergir de
        lambda_macro (dentro da banda de kappa aceita) -- prova que a
        média ponderada de fato deixa o GSAx mexer no preço, não só na
        Camada 1 isolada (o que já valia antes desta mudança)."""
        elenco = _elenco_completo(team_id=10, is_home=True)
        agregador = pp.PlayerToTeamAggregator()
        reconciler = pp.HierarchicalReconciler()

        agregacao_neutra = agregador.agregar(elenco, gsax_rate_adversario=0.0)
        # gsax_rate=0.2 (goleiro bom, mas não extremo) -- reduz lambda_bottom_up
        # o bastante pra medir o efeito, mas mantém kappa dentro da banda
        # [0.8, 1.2] aceita (fora disso cairia em quarentena, que é outro
        # comportamento -- já coberto pelos testes de quarentena acima).
        agregacao_goleiro_bom = agregador.agregar(elenco, gsax_rate_adversario=0.2)
        assert agregacao_goleiro_bom.lambda_bottom_up < agregacao_neutra.lambda_bottom_up

        lambda_macro = agregacao_neutra.lambda_bottom_up  # macro "concorda" com o cenário neutro
        r_neutro = reconciler.reconciliar(agregacao_neutra.lambda_bottom_up, lambda_macro)
        r_goleiro_bom = reconciler.reconciliar(agregacao_goleiro_bom.lambda_bottom_up, lambda_macro)

        assert r_neutro.quarantine_flag is False
        assert r_goleiro_bom.quarantine_flag is False
        assert r_goleiro_bom.lambda_final < r_neutro.lambda_final
        assert r_goleiro_bom.lambda_final != pytest.approx(lambda_macro)


# ---------------------------------------------------------------------------
# Camada 3 -- DixonColesJointEngine
# ---------------------------------------------------------------------------
class TestDixonColesJointEngine:
    @pytest.mark.parametrize(
        "lam,mu,rho",
        [(1.2, 1.0, 0.0), (1.5, 1.3, -0.05), (0.8, 0.9, 0.05), (2.5, 0.4, -0.2)],
    )
    def test_matriz_soma_unitaria(self, lam, mu, rho):
        matriz = pp.DixonColesJointEngine().construir_matriz(lam, mu, rho)
        assert matriz.sum() == pytest.approx(1.0, abs=1e-9)

    def test_matriz_e_10x10(self):
        matriz = pp.DixonColesJointEngine().construir_matriz(1.3, 1.1, 0.0)
        assert matriz.shape == (10, 10)

    def test_placar_0x0_reflete_correcao_tau(self):
        lam, mu, rho = 1.3, 1.1, -0.1
        matriz_com_rho = pp.DixonColesJointEngine().construir_matriz(lam, mu, rho)
        matriz_independente = pp.DixonColesJointEngine().construir_matriz(lam, mu, 0.0)
        assert matriz_com_rho[0, 0] != pytest.approx(matriz_independente[0, 0])

    def test_rho_zero_e_poisson_dupla_independente(self):
        lam, mu = 1.3, 1.1
        matriz = pp.DixonColesJointEngine().construir_matriz(lam, mu, 0.0)
        # Sem correção tau, P(0,0) deveria bater com Poisson(0;lam)*
        # Poisson(0;mu) -- tolerância frouxa (não 1e-9) porque a matriz é
        # truncada em max_gols=9 e sempre renormalizada (mesmo com rho=0),
        # então sobra um resíduo de massa de cauda cortada (<1e-4 relativo
        # pra esses lambdas) rescalando todas as células por igual.
        from scipy.stats import poisson as scipy_poisson

        esperado = scipy_poisson.pmf(0, lam) * scipy_poisson.pmf(0, mu)
        assert matriz[0, 0] == pytest.approx(esperado, rel=1e-3)

    def test_rho_fora_do_limite_e_clipado(self):
        lam, mu = 0.3, 0.3  # lambda baixo -> limite inferior de rho é bem restrito
        rho_efetivo, ajustado = pp.DixonColesJointEngine.validar_rho(lam, mu, rho=-5.0)
        assert ajustado is True
        assert rho_efetivo > -5.0

    def test_rho_dentro_do_limite_nao_e_ajustado(self):
        rho_efetivo, ajustado = pp.DixonColesJointEngine.validar_rho(1.3, 1.1, rho=-0.05)
        assert ajustado is False
        assert rho_efetivo == pytest.approx(-0.05)

    def test_lambda_ou_mu_nao_positivo_levanta_valueerror(self):
        with pytest.raises(ValueError):
            pp.DixonColesJointEngine.validar_rho(0.0, 1.0, rho=0.0)

    def test_mercados_1x2_soma_1(self):
        resultado = pp.DixonColesJointEngine().gerar(1.4, 1.1, -0.05)
        soma = sum(resultado.mercados[("1X2", s)] for s in ("home", "draw", "away"))
        assert soma == pytest.approx(1.0)

    def test_mercados_over_under_complementares(self):
        resultado = pp.DixonColesJointEngine().gerar(1.4, 1.1, -0.05)
        soma = resultado.mercados[("over_under_2.5", "over")] + resultado.mercados[("over_under_2.5", "under")]
        assert soma == pytest.approx(1.0)

    def test_mercados_btts_soma_1(self):
        resultado = pp.DixonColesJointEngine().gerar(1.4, 1.1, -0.05)
        soma = resultado.mercados[("btts", "yes")] + resultado.mercados[("btts", "no")]
        assert soma == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Camada 4 -- EnsembleVetoManager
# ---------------------------------------------------------------------------
class TestEnsembleVetoManager:
    @pytest.mark.parametrize("prob,odd", [(0.55, 2.0), (0.3, 3.5), (0.15, 6.0), (0.05, 10.0), (0.9, 1.2)])
    def test_stake_bate_com_backtest_kelly(self, prob, odd):
        gerente = pp.EnsembleVetoManager()
        assert gerente.calcular_stake(prob, odd) == pytest.approx(bk.kelly_fracionario(prob, odd))

    def test_devig_soma_1(self):
        odds = {"home": 2.0, "draw": 3.4, "away": 3.8}
        devigadas = pp.EnsembleVetoManager.devig(odds)
        assert sum(devigadas.values()) == pytest.approx(1.0, abs=1e-8)

    def test_devig_odd_invalida_levanta_valueerror(self):
        with pytest.raises(ValueError):
            pp.EnsembleVetoManager.devig({"home": 0.9, "away": 2.0})

    def test_prob_modelo_fora_de_0_1_levanta_valueerror(self):
        with pytest.raises(ValueError):
            pp.EnsembleVetoManager.calcular_edge_ev(1.5, 0.5, 2.0)

    def test_veto_incoerencia_direcional(self):
        gerente = pp.EnsembleVetoManager()
        macro_direction = {"over_under_2.5": {"selecao": "under", "confianca": 0.9}}
        avaliacao = gerente.avaliar_selecao(
            "over_under_2.5", "over", prob_modelo=0.6, prob_mercado_devig=0.5, odd_real=2.0,
            quarantine_flag=False, macro_direction=macro_direction,
        )
        assert avaliacao.status == "VETADO_INCOERENCIA"
        assert avaliacao.stake_pct == 0.0

    def test_sem_incoerencia_quando_confianca_macro_baixa(self):
        gerente = pp.EnsembleVetoManager()
        macro_direction = {"over_under_2.5": {"selecao": "under", "confianca": 0.3}}
        avaliacao = gerente.avaliar_selecao(
            "over_under_2.5", "over", prob_modelo=0.6, prob_mercado_devig=0.5, odd_real=2.0,
            quarantine_flag=False, macro_direction=macro_direction,
        )
        assert avaliacao.status != "VETADO_INCOERENCIA"

    def test_sem_incoerencia_quando_mesma_selecao(self):
        gerente = pp.EnsembleVetoManager()
        macro_direction = {"over_under_2.5": {"selecao": "over", "confianca": 0.95}}
        avaliacao = gerente.avaliar_selecao(
            "over_under_2.5", "over", prob_modelo=0.6, prob_mercado_devig=0.5, odd_real=2.0,
            quarantine_flag=False, macro_direction=macro_direction,
        )
        assert avaliacao.status != "VETADO_INCOERENCIA"

    def test_trava_zebra_longa_dispara(self):
        gerente = pp.EnsembleVetoManager()
        # edge = 0.32 - 0.30 = 0.02 < 0.05 -> veta
        avaliacao = gerente.avaliar_selecao(
            "1X2", "away", prob_modelo=0.32, prob_mercado_devig=0.30, odd_real=4.0, quarantine_flag=False,
        )
        assert avaliacao.status == "VETADO_ZEBRA_LONGA"

    def test_trava_zebra_longa_nao_dispara_com_edge_alto(self):
        gerente = pp.EnsembleVetoManager()
        # edge = 0.40 - 0.30 = 0.10 >= 0.05 -> não veta por zebra
        avaliacao = gerente.avaliar_selecao(
            "1X2", "away", prob_modelo=0.40, prob_mercado_devig=0.30, odd_real=4.0, quarantine_flag=False,
        )
        assert avaliacao.status != "VETADO_ZEBRA_LONGA"

    def test_trava_zebra_nao_aplica_a_odd_baixa(self):
        gerente = pp.EnsembleVetoManager()
        avaliacao = gerente.avaliar_selecao(
            "1X2", "away", prob_modelo=0.32, prob_mercado_devig=0.30, odd_real=3.0, quarantine_flag=False,
        )
        assert avaliacao.status != "VETADO_ZEBRA_LONGA"

    def test_trava_zebra_nao_aplica_a_home_ou_over(self):
        gerente = pp.EnsembleVetoManager()
        avaliacao = gerente.avaliar_selecao(
            "1X2", "home", prob_modelo=0.32, prob_mercado_devig=0.30, odd_real=4.0, quarantine_flag=False,
        )
        assert avaliacao.status != "VETADO_ZEBRA_LONGA"

    def test_quarentena_veta_com_stake_zero_independente_de_edge(self):
        gerente = pp.EnsembleVetoManager()
        avaliacao = gerente.avaliar_selecao(
            "1X2", "home", prob_modelo=0.7, prob_mercado_devig=0.3, odd_real=2.0, quarantine_flag=True,
        )
        assert avaliacao.status == "VETADO_QUARENTENA"
        assert avaliacao.stake_pct == 0.0

    def test_edge_abaixo_do_minimo_e_rejeitada(self):
        gerente = pp.EnsembleVetoManager()
        avaliacao = gerente.avaliar_selecao(
            "1X2", "home", prob_modelo=0.51, prob_mercado_devig=0.50, odd_real=1.95, quarantine_flag=False,
        )
        assert avaliacao.status == "REJEITADA_EDGE"

    def test_selecao_aprovada_tem_stake_positivo(self):
        gerente = pp.EnsembleVetoManager()
        avaliacao = gerente.avaliar_selecao(
            "1X2", "home", prob_modelo=0.60, prob_mercado_devig=0.50, odd_real=2.0, quarantine_flag=False,
        )
        assert avaliacao.status == "APROVADA"
        assert avaliacao.stake_pct > 0.0

    def test_gerar_ordens_ignora_selecao_sem_contraparte_na_matriz(self):
        gerente = pp.EnsembleVetoManager()
        mercados = {("1X2", "home"): 0.5, ("1X2", "draw"): 0.25}  # sem "away" de propósito
        odds_mercado = {"1X2": {"home": 2.0, "draw": 3.4, "away": 3.8}}
        ordens = gerente.gerar_ordens(mercados, odds_mercado, quarantine_flag=False)
        assert "away" not in set(ordens["selecao"])

    def test_gerar_ordens_sem_blender_preenche_pre_blend_igual_ao_final(self):
        gerente = pp.EnsembleVetoManager()
        mercados = {("over_under_2.5", "over"): 0.55, ("over_under_2.5", "under"): 0.45}
        odds_mercado = {"over_under_2.5": {"over": 1.9, "under": 1.95}}
        ordens = gerente.gerar_ordens(mercados, odds_mercado, quarantine_flag=False)
        assert (ordens["prob_modelo"] == ordens["prob_modelo_pre_blend"]).all()

    def test_gerar_ordens_com_blender_calibrado_muda_prob_modelo(self):
        gerente = pp.EnsembleVetoManager()
        mercados = {("over_under_2.5", "over"): 0.55, ("over_under_2.5", "under"): 0.45}
        odds_mercado = {"over_under_2.5": {"over": 1.9, "under": 1.95}}
        blender = pp.MercadoBlender(pesos_por_mercado={"over_under_2.5": (0.5, 0.5, 0.5)})
        ordens = gerente.gerar_ordens(mercados, odds_mercado, quarantine_flag=False, blender=blender)
        linha_over = ordens[ordens["selecao"] == "over"].iloc[0]
        assert linha_over["prob_modelo_pre_blend"] == pytest.approx(0.55)
        assert linha_over["prob_modelo"] != pytest.approx(0.55)


# ---------------------------------------------------------------------------
# Camada 5 -- MercadoBlender
# ---------------------------------------------------------------------------
class TestMercadoBlender:
    def test_sem_peso_configurado_e_no_op(self):
        blender = pp.MercadoBlender()  # PESOS_POR_FAIXA_BLEND_DEFAULT vazio
        p_final = blender.blend("over_under_2.5", prob_modelo=0.60, prob_mercado_devig=0.45)
        assert p_final == pytest.approx(0.60)

    def test_mercado_nao_listado_e_no_op_mesmo_com_outros_mercados_calibrados(self):
        blender = pp.MercadoBlender(pesos_por_mercado={"over_under_2.5": (0.1, 0.2, 0.3)})
        p_final = blender.blend("1X2", prob_modelo=0.40, prob_mercado_devig=0.30)
        assert p_final == pytest.approx(0.40)

    def test_peso_zero_na_faixa_e_no_op(self):
        blender = pp.MercadoBlender(pesos_por_mercado={"over_under_2.5": (0.0, 0.0, 0.0)})
        p_final = blender.blend("over_under_2.5", prob_modelo=0.60, prob_mercado_devig=0.60)
        assert p_final == pytest.approx(0.60)

    def test_peso_um_e_independente_da_probabilidade_de_mercado(self):
        # w=1.0 zera o peso da odd de mercado no logit -- mas a renormalização
        # (dividir pela soma de exp(logit) de "sim" e "não") não devolve
        # `prob_modelo` inalterado, ela "afia" o valor: p^2/(p^2+(1-p)^2).
        # Isso é uma propriedade conhecida do método (não um bug daqui) --
        # ver `test_paridade_com_rodar_blend_odds_pinnacle` confirmando que
        # é EXATAMENTE a mesma fórmula já validada em produção.
        blender = pp.MercadoBlender(pesos_por_mercado={"over_under_2.5": (1.0, 1.0, 1.0)})
        p_a = blender.blend("over_under_2.5", prob_modelo=0.60, prob_mercado_devig=0.10)
        p_b = blender.blend("over_under_2.5", prob_modelo=0.60, prob_mercado_devig=0.90)
        assert p_a == pytest.approx(p_b, abs=1e-9)
        assert p_a == pytest.approx(0.6 ** 2 / (0.6 ** 2 + 0.4 ** 2), abs=1e-9)

    def test_paridade_com_rodar_blend_odds_pinnacle(self):
        """A Camada 5 precisa reproduzir EXATAMENTE a fórmula já validada
        por walk-forward em `rodar_blend_odds_pinnacle._blend` -- não uma
        aproximação parecida."""
        import rodar_blend_odds_pinnacle as rbop

        for w in (0.075, 0.20, 0.51, 1.0):
            for pm, pk in [(0.6, 0.4), (0.3, 0.55), (0.5, 0.5)]:
                esperado = rbop._blend({"over": pm, "under": 1 - pm}, {"over": pk, "under": 1 - pk}, w)["over"]
                blender = pp.MercadoBlender(pesos_por_mercado={"over_under_2.5": (w, w, w)})
                obtido = blender.blend("over_under_2.5", prob_modelo=pm, prob_mercado_devig=pk)
                assert obtido == pytest.approx(esperado, abs=1e-9)

    def test_faixa_de_magnitude_correta_e_escolhida(self):
        blender = pp.MercadoBlender(
            pesos_por_mercado={"over_under_2.5": (0.0, 0.5, 1.0)},
            cortes_magnitude=(0.05, 0.10),
        )
        # magnitude baixa (jogo parelho) -> peso 0.0 -> no-op (prob_modelo puro).
        p_parelho = blender.blend("over_under_2.5", prob_modelo=0.60, prob_mercado_devig=0.52)
        assert p_parelho == pytest.approx(0.60)
        # magnitude alta (favorito claro) -> peso 1.0 -> mesma fórmula do
        # teste de paridade acima (0,6^2/(0,6^2+0,4^2), independente de pk).
        p_favorito = blender.blend("over_under_2.5", prob_modelo=0.60, prob_mercado_devig=0.80)
        assert p_favorito == pytest.approx(0.6 ** 2 / (0.6 ** 2 + 0.4 ** 2), abs=1e-9)

    def test_blend_e_simetrico_complementar(self):
        """P(over) + P(under) somam 1 quando tratados como complementares
        binários -- garante que o blend não quebra a identidade básica pro
        caso de 2 seleções (over/under), o único caso "exato" documentado
        na classe."""
        blender = pp.MercadoBlender(pesos_por_mercado={"over_under_2.5": (0.3, 0.3, 0.3)})
        p_over = blender.blend("over_under_2.5", prob_modelo=0.55, prob_mercado_devig=0.60)
        p_under = blender.blend("over_under_2.5", prob_modelo=0.45, prob_mercado_devig=0.40)
        assert p_over + p_under == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# process_match -- fim a fim
# ---------------------------------------------------------------------------
class TestProcessMatch:
    @staticmethod
    def _jogadores_partida() -> pd.DataFrame:
        home = _elenco_completo(team_id=10, is_home=True)
        away = _elenco_completo(team_id=20, is_home=False)
        return pd.concat([home, away], ignore_index=True)

    def test_schema_da_saida(self):
        jogadores = self._jogadores_partida()
        odds_mercado = {
            "1X2": {"home": 2.0, "draw": 3.4, "away": 3.8},
            "over_under_2.5": {"over": 1.9, "under": 1.95},
        }
        macro_priors = {"lambda_home": 1.4, "lambda_away": 1.1, "rho_liga": -0.05}
        gk_stats = {"home": {"gsax_rate": 0.0}, "away": {"gsax_rate": 0.0}}

        resultado = pp.process_match(jogadores, odds_mercado, macro_priors, gk_stats)
        assert list(resultado.columns) == pp.COLUNAS_ORDENS
        assert (resultado["status"] == "APROVADA").all()

    def test_incluir_vetadas_devolve_status_variados(self):
        jogadores = self._jogadores_partida()
        # kappa vai ficar fora da banda de propósito (lambda macro bem
        # maior que o bottom-up derivado do elenco sintético) -- garante
        # que apareça pelo menos uma VETADO_QUARENTENA na saída completa.
        odds_mercado = {"1X2": {"home": 2.0, "draw": 3.4, "away": 3.8}}
        macro_priors = {"lambda_home": 50.0, "lambda_away": 50.0, "rho_liga": -0.05}
        gk_stats = {"home": {"gsax_rate": 0.0}, "away": {"gsax_rate": 0.0}}

        resultado = pp.process_match(jogadores, odds_mercado, macro_priors, gk_stats, incluir_vetadas=True)
        assert set(resultado["status"]) == {"VETADO_QUARENTENA"}

    def test_sem_blender_prob_modelo_igual_pre_blend(self):
        jogadores = self._jogadores_partida()
        odds_mercado = {"over_under_2.5": {"over": 1.9, "under": 1.95}}
        macro_priors = {"lambda_home": 1.4, "lambda_away": 1.1, "rho_liga": -0.05}
        gk_stats = {"home": {"gsax_rate": 0.0}, "away": {"gsax_rate": 0.0}}

        resultado = pp.process_match(jogadores, odds_mercado, macro_priors, gk_stats, incluir_vetadas=True)
        assert (resultado["prob_modelo"] == resultado["prob_modelo_pre_blend"]).all()

    def test_com_blender_calibrado_prob_modelo_diverge_do_pre_blend(self):
        jogadores = self._jogadores_partida()
        odds_mercado = {"over_under_2.5": {"over": 1.9, "under": 1.95}}
        macro_priors = {"lambda_home": 1.4, "lambda_away": 1.1, "rho_liga": -0.05}
        gk_stats = {"home": {"gsax_rate": 0.0}, "away": {"gsax_rate": 0.0}}
        blender = pp.MercadoBlender(pesos_por_mercado={"over_under_2.5": (0.5, 0.5, 0.5)})

        resultado = pp.process_match(
            jogadores, odds_mercado, macro_priors, gk_stats, incluir_vetadas=True, blender=blender
        )
        assert (resultado["prob_modelo"] != resultado["prob_modelo_pre_blend"]).any()

    def test_is_home_ausente_levanta_valueerror(self):
        jogadores = self._jogadores_partida().drop(columns=["is_home"])
        with pytest.raises(ValueError):
            pp.process_match(jogadores, {}, {"lambda_home": 1.0, "lambda_away": 1.0, "rho_liga": 0.0}, {"home": {}, "away": {}})

    def test_gk_stats_sem_chave_obrigatoria_levanta_valueerror(self):
        jogadores = self._jogadores_partida()
        with pytest.raises(ValueError):
            pp.process_match(jogadores, {}, {"lambda_home": 1.0, "lambda_away": 1.0, "rho_liga": 0.0}, {"home": {}})

    def test_macro_priors_sem_chave_obrigatoria_levanta_valueerror(self):
        jogadores = self._jogadores_partida()
        with pytest.raises(ValueError):
            pp.process_match(jogadores, {}, {"lambda_home": 1.0}, {"home": {}, "away": {}})

    def test_falta_um_dos_2_times_levanta_valueerror(self):
        jogadores = _elenco_completo(team_id=10, is_home=True)  # só o mandante
        macro_priors = {"lambda_home": 1.4, "lambda_away": 1.1, "rho_liga": -0.05}
        gk_stats = {"home": {"gsax_rate": 0.0}, "away": {"gsax_rate": 0.0}}
        with pytest.raises(ValueError):
            pp.process_match(jogadores, {}, macro_priors, gk_stats)
