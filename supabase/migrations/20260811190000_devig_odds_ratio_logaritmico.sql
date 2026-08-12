-- Devig de odds via Odds Ratio (Cheung) e Logarithmic function (power),
-- mesmos dois métodos implementados em api/backtest-betting.js e
-- api/model-stats.js (ver true_odds_calculator.xlsm, planilha de
-- referência). Fórmulas:
--   Odds Ratio: resolve c em sum(q_i / (c + q_i - c*q_i)) = 1, q_i = 1/odd_i
--   Logarithmic: resolve c em sum(odd_i^c) = 1, prob_i = odd_i^c
-- Ambas resolvidas por bissecção genérica (funciona pra qualquer N de
-- seleções, não só 2/3-way) em vez de reproduzir as fórmulas fechadas
-- quadrática/cúbica da planilha. Substitui a normalização proporcional
-- simples (implícita/soma) que v_market_edge usava antes.

CREATE OR REPLACE FUNCTION _devig_or_g(q double precision[], t double precision)
RETURNS double precision LANGUAGE sql IMMUTABLE AS $$
  SELECT sum(qi / ((1 + t) + qi - (1 + t) * qi)) - 1
  FROM unnest(q) AS qi;
$$;

CREATE OR REPLACE FUNCTION _devig_log_g(odds double precision[], t double precision)
RETURNS double precision LANGUAGE sql IMMUTABLE AS $$
  SELECT sum(power(o, -t)) - 1
  FROM unnest(odds) AS o;
$$;

-- Bissecção genérica: acha t>=0 tal que g(t)=0, expandindo o bracket
-- dinamicamente (favoritos muito curtos, tipo odd=1.01, precisam de t bem
-- maior que um bracket fixo pequeno daria conta -- mesma lógica de
-- resolverParametroDevig() no lado JS, testada contra esse caso).
CREATE OR REPLACE FUNCTION _devig_resolver_t(valores double precision[], eh_odds_ratio boolean)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  g0 double precision;
  t_lo double precision := 0;
  g_lo double precision;
  t_hi double precision := 0;
  g_hi double precision;
  t_mid double precision;
  g_mid double precision;
  i integer;
BEGIN
  g0 := CASE WHEN eh_odds_ratio THEN _devig_or_g(valores, 0) ELSE _devig_log_g(valores, 0) END;
  IF abs(g0) < 1e-14 THEN RETURN 0; END IF;

  g_lo := g0;
  g_hi := g0;
  WHILE g_hi > 0 LOOP
    t_hi := CASE WHEN t_hi = 0 THEN 1 ELSE t_hi * 2 END;
    g_hi := CASE WHEN eh_odds_ratio THEN _devig_or_g(valores, t_hi) ELSE _devig_log_g(valores, t_hi) END;
    IF t_hi > 1e15 THEN EXIT; END IF; -- odds degeneradas (<=1) não deveriam chegar aqui
  END LOOP;

  FOR i IN 1..200 LOOP
    t_mid := (t_lo + t_hi) / 2;
    g_mid := CASE WHEN eh_odds_ratio THEN _devig_or_g(valores, t_mid) ELSE _devig_log_g(valores, t_mid) END;
    IF abs(g_mid) < 1e-12 THEN RETURN t_mid; END IF;
    IF (g_mid > 0) = (g_lo > 0) THEN
      t_lo := t_mid; g_lo := g_mid;
    ELSE
      t_hi := t_mid;
    END IF;
  END LOOP;
  RETURN (t_lo + t_hi) / 2;
END;
$$;

CREATE OR REPLACE FUNCTION devig_odds_ratio(odds numeric[])
RETURNS numeric[] LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  q double precision[] := ARRAY(SELECT (1.0 / o)::double precision FROM unnest(odds) AS o);
  t double precision;
  c double precision;
BEGIN
  t := _devig_resolver_t(q, true);
  c := 1 + t;
  RETURN ARRAY(SELECT (qi / (c + qi - c * qi))::numeric FROM unnest(q) AS qi);
END;
$$;

CREATE OR REPLACE FUNCTION devig_logaritmico(odds numeric[])
RETURNS numeric[] LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  odds_f double precision[] := ARRAY(SELECT o::double precision FROM unnest(odds) AS o);
  t double precision;
  c double precision;
BEGIN
  t := _devig_resolver_t(odds_f, false);
  c := -t;
  RETURN ARRAY(SELECT (power(o, c))::numeric FROM unnest(odds_f) AS o);
END;
$$;

-- v_market_edge: cada grupo (match_id, bookmaker, market, snapshot) precisa
-- do conjunto INTEIRO de odds pra resolver o parâmetro compartilhado (c) --
-- por isso agrega em array antes de devigar, diferente da versão anterior
-- (window function 1/odds / soma, linha a linha, O(1) por linha).
-- market_devigged_prob é a probabilidade "principal" (agora Odds Ratio, era
-- proporcional simples); market_devigged_prob_log é o método alternativo.
--
-- IMPORTANTE — SEM "AS MATERIALIZED" nas CTEs de propósito: a bissecção é
-- bem mais cara que a normalização O(1) antiga, e uma consulta sem filtro
-- de match_id (full scan de odds_market inteira, ~90k+ grupos) dá timeout
-- de qualquer jeito -- MATERIALIZED só faz isso acontecer SEMPRE, mesmo
-- filtrando por 1 partida, porque bloqueia o planner de empurrar o filtro
-- pra dentro do GROUP BY (testado e revertido durante a validação desta
-- migration). Sem MATERIALIZED, o Postgres empurra `WHERE match_id = X` pra
-- dentro de `grupos`, e o uso real documentado (comparar 1 partida/modelo
-- por vez) fica rápido (~30ms, plano com Index Scan em ambas as tabelas).
-- Não usar esta view para full-scan sem filtro de match_id/liga.
CREATE OR REPLACE VIEW v_market_edge AS
WITH grupos AS (
  SELECT
    match_id, bookmaker, market, snapshot,
    array_agg(selection ORDER BY selection) AS selecoes,
    array_agg(odds ORDER BY selection) AS odds_arr
  FROM odds_market
  WHERE odds > 0
  GROUP BY match_id, bookmaker, market, snapshot
),
devigged AS (
  SELECT
    match_id, bookmaker, market, snapshot,
    unnest(selecoes) AS selection,
    unnest(odds_arr) AS odds,
    unnest(devig_odds_ratio(odds_arr)) AS devigged_prob_or,
    unnest(devig_logaritmico(odds_arr)) AS devigged_prob_log
  FROM grupos
)
SELECT
  mp.match_id,
  mp.model_name,
  d.bookmaker,
  d.snapshot,
  mp.market,
  mp.selection,
  round(mp.probability::numeric, 5) AS model_probability,
  round(d.odds::numeric, 3) AS market_odds,
  round(1.0 / d.odds, 5) AS market_implied_prob,
  round(d.devigged_prob_or, 5) AS market_devigged_prob,
  round(d.devigged_prob_log, 5) AS market_devigged_prob_log,
  round((mp.probability - d.devigged_prob_or) / d.devigged_prob_or * 100::numeric, 2) AS edge_pct,
  round((mp.probability - d.devigged_prob_log) / d.devigged_prob_log * 100::numeric, 2) AS edge_pct_log,
  round(mp.probability * d.odds - 1::numeric, 4) AS ev_per_unit
FROM model_predictions mp
JOIN devigged d ON d.match_id = mp.match_id AND d.market = mp.market AND d.selection = mp.selection;
