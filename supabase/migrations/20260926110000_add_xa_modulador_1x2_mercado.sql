-- =============================================================================
-- Migration: mercado 'xa_modulador_1x2' em player_market_backtest --
-- validação em log-loss REAL de 1X2 (pipeline completo: Camada 1 + 2 + 3)
-- do modulador de xA próprio do elenco
-- (`pricing_pipeline.py::PlayerToTeamAggregator.modular_por_xa_propria`)
-- =============================================================================
-- Continuação de `20260925100000_add_xa_jogador_mercados_backtest.sql`
-- (validação de xA como REGRESSOR isolado, mercado 'xa') e do achado de
-- 26/09 (CONTEXTO_PROJETO.md) que reverteu a decisão de 25/09 de descartar
-- xA como modulador de λ_gols -- com xA corrigido, a mesma metodologia
-- (correlação parcial + split 70/30, RMSE de gols por time) agora
-- generaliza fora da amostra de forma estatisticamente significativa.
--
-- Esta migration dá espaço pra `scripts/backtest_xa_modulador_pipeline.py`
-- persistir a validação de nível mais alto: não RMSE de gols isolado, mas
-- log-loss REAL de 1X2 depois de passar pelas Camadas 1 (agregação,
-- com/sem `usar_modulador_xa`) + 2 (`HierarchicalReconciler`, mesmo λ
-- macro nas duas variantes) + 3 (`DixonColesJointEngine`, matriz conjunta
-- de verdade) -- é essa comparação que decide se `usar_modulador_xa=True`
-- deve virar o default em `rodar_pricing_pipeline.py`, não a validação de
-- RMSE isolado (necessária mas não suficiente, ver docstring de
-- `XA_RESIDUO_A`/`XA_BETA` em `pricing_pipeline.py`).
--
-- 'xa_modulador_1x2' reaproveita rmse_modelo/rmse_baseline pra guardar
-- log-loss médio de 1X2 (modelo=com xA, baseline=sem xA, goleiro/força
-- defensiva neutros nas duas variantes -- teste de ISOLAMENTO do efeito
-- de xA, mesmo espírito de `gsax_gols_adversario`, que também testa 1
-- feature de cada vez contra neutro em vez de contra a config completa de
-- produção). `log_loss` guarda o mesmo valor de `rmse_modelo` (log-loss
-- com xA), reaproveitado pela convenção genérica das outras linhas desta
-- tabela. `brier`/`calibracao` ficam NULL (não calculados nesta rodada).
-- ic95_diff_inf/ic95_diff_sup (já existentes, migration anterior) guardam
-- o IC95% bootstrap pareado da diferença (log-loss sem_xa - log-loss
-- com_xa) -- positivo nos dois limites = com xA bate sem xA de forma
-- estatisticamente sustentada, não só na média pontual.
-- =============================================================================

alter table public.player_market_backtest
  drop constraint if exists player_market_backtest_mercado_check;

alter table public.player_market_backtest
  add constraint player_market_backtest_mercado_check
  check (mercado in ('chutes', 'gols_thinning', 'gols_direto', 'xg', 'chutes_no_alvo_thinning', 'xa', 'gsax_gols_adversario', 'xa_modulador_1x2'));
