-- Tabelas de suporte ao walk-forward CV do modelo v9 e ao painel de
-- análise exploratória de features.

-- Resultados por fold e por modelo: logloss, BS, acurácia geral + por liga.
CREATE TABLE IF NOT EXISTS model_v9_walkforward_results (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    modelo        TEXT    NOT NULL,
    fold          TEXT    NOT NULL,
    train_max_ano INT,
    val_ano       INT,
    test_ano      INT,
    n_treino      INT,
    n_val         INT,
    n_test        INT,
    logloss_test       FLOAT,
    brier_score_test   FLOAT,
    accuracy_test      FLOAT,
    logloss_por_liga   JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Importância de feature via CatBoost.get_feature_importance() — calculada
-- no modelo treinado no fold_3 (janela mais recente, mais representativa).
CREATE TABLE IF NOT EXISTS model_v9_feature_importance (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feature_name    TEXT  NOT NULL,
    feature_group   TEXT  NOT NULL,
    importance_mean FLOAT NOT NULL,
    importance_std  FLOAT,
    fold            TEXT  NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cobertura de feature no dataset histórico completo: % não-nulos + stats.
CREATE TABLE IF NOT EXISTS model_v9_feature_coverage (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feature_name  TEXT  NOT NULL,
    feature_group TEXT  NOT NULL,
    coverage_pct  FLOAT NOT NULL,
    mean_value    FLOAT,
    std_value     FLOAT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- RLS: leitura pública (dados de modelo, sem PII), escrita só via service_role.
ALTER TABLE model_v9_walkforward_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_v9_feature_importance  ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_v9_feature_coverage    ENABLE ROW LEVEL SECURITY;

CREATE POLICY "leitura publica model_v9_walkforward_results"
    ON model_v9_walkforward_results FOR SELECT USING (true);

CREATE POLICY "leitura publica model_v9_feature_importance"
    ON model_v9_feature_importance FOR SELECT USING (true);

CREATE POLICY "leitura publica model_v9_feature_coverage"
    ON model_v9_feature_coverage FOR SELECT USING (true);
