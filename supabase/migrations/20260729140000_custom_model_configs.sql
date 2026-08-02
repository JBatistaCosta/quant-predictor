-- Tabela de configurações de modelos personalizados
-- Permite ao usuário definir conjuntos de features + algoritmo e disparar treinos via GitHub Actions.

CREATE TABLE IF NOT EXISTS public.custom_model_configs (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT        NOT NULL,
    algorithm     TEXT        NOT NULL CHECK (algorithm IN ('catboost', 'xgboost', 'lightgbm', 'mlp')),
    features      TEXT[]      NOT NULL DEFAULT '{}',
    status        TEXT        NOT NULL DEFAULT 'rascunho'
                              CHECK (status IN ('rascunho', 'aguardando_treino', 'treinando', 'concluido', 'erro')),
    metrics       JSONB,
    model_key     TEXT        UNIQUE,
    notes         TEXT,
    error_message TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    trained_at    TIMESTAMPTZ
);

ALTER TABLE public.custom_model_configs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "auth_select" ON public.custom_model_configs
    FOR SELECT USING (auth.uid() IS NOT NULL);

CREATE POLICY "auth_insert" ON public.custom_model_configs
    FOR INSERT WITH CHECK (auth.uid() IS NOT NULL);

CREATE POLICY "auth_update" ON public.custom_model_configs
    FOR UPDATE USING (auth.uid() IS NOT NULL);

CREATE POLICY "auth_delete" ON public.custom_model_configs
    FOR DELETE USING (auth.uid() IS NOT NULL);

COMMENT ON TABLE public.custom_model_configs IS
    'Configurações de modelos ML personalizados criados pelo usuário no painel de treino.';
