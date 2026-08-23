-- Tabelas de suporte ao walk-forward CV do modelo v11 (walkforward_cv_v11.py)
-- -- mesmo propósito de model_v9_walkforward_results/feature_importance/
-- feature_coverage, versão paralela pra v11 (v10 + força do XI titular
-- abertura/fechamento, ver FEATURES_V11 em dados_historicos.py).
--
-- Schema espelha o schema REAL de produção de model_v9_walkforward_results
-- (conferido via information_schema -- os arquivos de migration da v9
-- ficaram incompletos ao longo do tempo: `market` e as 4 colunas
-- `*_calibrado_*` foram aplicadas direto em produção sem migration
-- correspondente no repo). Criando já completo aqui pra não repetir esse
-- drift nem o bug já documentado (HTTP 201 com 0 linhas inseridas
-- silenciosamente quando falta coluna que o script grava).

CREATE TABLE IF NOT EXISTS model_v11_walkforward_results (
    id                          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    modelo                      TEXT NOT NULL,
    fold                        TEXT NOT NULL,
    market                      TEXT NOT NULL DEFAULT '1X2',
    train_max_ano               INT,
    val_ano                     INT,
    test_ano                    INT,
    n_treino                    INT,
    n_val                       INT,
    n_test                      INT,
    logloss_test                DOUBLE PRECISION,
    brier_score_test            DOUBLE PRECISION,
    accuracy_test               DOUBLE PRECISION,
    logloss_por_liga            JSONB,
    learning_curve              JSONB,
    logloss_calibrado_platt     DOUBLE PRECISION,
    brier_calibrado_platt       DOUBLE PRECISION,
    logloss_calibrado_isotonic  DOUBLE PRECISION,
    brier_calibrado_isotonic    DOUBLE PRECISION,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Importância de feature via CatBoost.get_feature_importance() — calculada
-- no modelo treinado no fold_3 (janela mais recente, mais representativa).
CREATE TABLE IF NOT EXISTS model_v11_feature_importance (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feature_name    TEXT NOT NULL,
    feature_group   TEXT NOT NULL,
    importance_mean DOUBLE PRECISION NOT NULL,
    importance_std  DOUBLE PRECISION,
    fold            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cobertura de feature no dataset histórico completo: % não-nulos + stats.
-- Especialmente relevante pra v11: titular_rating_abertura/fechamento tem
-- cobertura parcial por natureza (depende de backtest_xi_walkforward.py /
-- match_lineup_fotmob), essa tabela é como se mede isso de verdade em vez
-- de assumir.
CREATE TABLE IF NOT EXISTS model_v11_feature_coverage (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feature_name  TEXT NOT NULL,
    feature_group TEXT NOT NULL,
    coverage_pct  DOUBLE PRECISION NOT NULL,
    mean_value    DOUBLE PRECISION,
    std_value     DOUBLE PRECISION,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- RLS: leitura pública (dados de modelo, sem PII), escrita só via service_role.
ALTER TABLE model_v11_walkforward_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_v11_feature_importance  ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_v11_feature_coverage    ENABLE ROW LEVEL SECURITY;

CREATE POLICY "leitura publica model_v11_walkforward_results"
    ON model_v11_walkforward_results FOR SELECT USING (true);

CREATE POLICY "leitura publica model_v11_feature_importance"
    ON model_v11_feature_importance FOR SELECT USING (true);

CREATE POLICY "leitura publica model_v11_feature_coverage"
    ON model_v11_feature_coverage FOR SELECT USING (true);
