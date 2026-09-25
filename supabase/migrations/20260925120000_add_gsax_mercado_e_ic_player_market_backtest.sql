-- =============================================================================
-- Migration: mercado 'gsax_gols_adversario' + colunas de IC95% em
-- player_market_backtest -- validação walk-forward do modulador de GSAx do
-- goleiro no pricing pipeline (Fase 7/Frente B do plano da sessão)
-- =============================================================================
-- GSAx do goleiro (`player_match_estimates.gsax_rate`, calculado por
-- `dados_historicos.obter_gsax_atual`) já é consumido em produção por
-- `pricing_pipeline.py::PlayerToTeamAggregator.modular_por_goleiro`
-- (`λ_gols_ajustado = λ_xGOT_time_adversario · (1 - gsax_rate)`), mas nunca
-- foi validado se esse ajuste de fato melhora a previsão de gols sofridos
-- contra assumir goleiro neutro (`gsax_rate=0`, comportamento anterior à
-- feature existir) -- só existe uma checagem de ESTABILIDADE da métrica
-- (correlação por tamanho de janela), não de ACERTO real. Esta migration dá
-- espaço pra `scripts/backtest_gsax_walkforward.py` persistir essa
-- comparação (com_gsax vs. neutro) no mesmo padrão de `player_market_
-- backtest` já usado por chutes/gols/xg/xa.
--
-- 'gsax_gols_adversario' reaproveita as colunas rmse_modelo/rmse_baseline
-- (rmse_modelo = RMSE de λ_com_gsax, rmse_baseline = RMSE de λ_neutro) --
-- mesmo padrão semântico de "variante testada vs. variante de referência"
-- já usado pros outros mercados, só que aqui as duas variantes vêm do MESMO
-- sinal de xG (λ_xGOT_time_adversario), variando só o modulador de GSAx.
-- `log_loss` é reaproveitado pra guardar o NEGATIVO da log-verossimilhança
-- média de Poisson de λ_com_gsax (log-loss = -log-verossimilhança, mesma
-- unidade conceitual, sinal trocado -- documentado aqui pra não confundir
-- leitura futura). `brier`/`calibracao` ficam NULL (não se aplicam a esse
-- mercado, mesmo tratamento que os demais já dão a colunas que não usam).
--
-- ic95_diff_inf/ic95_diff_sup fecham um gap que já existia pra TODOS os
-- mercados (chutes/xg/xa/chutes_no_alvo só logam o IC95%, nunca persistem)
-- -- necessário aqui porque o critério de sucesso de GSAx depende
-- explicitamente do IC (efeito sustentado vs. ruído), não só da média
-- pontual. Nullable e retroativo: linhas antigas ficam NULL até a próxima
-- rodada do backtest de cada mercado.
-- =============================================================================

alter table public.player_market_backtest
  add column if not exists ic95_diff_inf numeric,
  add column if not exists ic95_diff_sup numeric;

comment on column public.player_market_backtest.ic95_diff_inf is
  'Limite inferior do IC95% (bootstrap pareado, 1000-2000 reamostragens) da diferença de erro (baseline-modelo, RMSE) ou de log-verossimilhança (variante-referência, ver mercado gsax_gols_adversario) -- ambos os limites >0 = vantagem estatisticamente sustentada, não só média pontual. Nullable: colunas retroativas, populadas só a partir desta migration.';
comment on column public.player_market_backtest.ic95_diff_sup is
  'Limite superior do mesmo IC95% de ic95_diff_inf.';

alter table public.player_market_backtest
  drop constraint if exists player_market_backtest_mercado_check;

alter table public.player_market_backtest
  add constraint player_market_backtest_mercado_check
  check (mercado in ('chutes', 'gols_thinning', 'gols_direto', 'xg', 'chutes_no_alvo_thinning', 'xa', 'gsax_gols_adversario'));
