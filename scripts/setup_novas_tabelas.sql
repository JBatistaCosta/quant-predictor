-- scripts/setup_novas_tabelas.sql
-- Execute este script no SQL Editor do Supabase para criar as tabelas necessárias.

-- 1. Tabela para salvar as configurações e métricas dos Treinos Customizados
CREATE TABLE IF NOT EXISTS public.custom_model_configs (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    name text NOT NULL,
    algorithm text NOT NULL,
    features jsonb NOT NULL,
    target text NOT NULL,
    hyperparameters jsonb,
    status text DEFAULT 'criado',
    metrics jsonb,
    model_key text,
    notes text,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()),
    trained_at timestamp with time zone,
    error_message text
);

-- Ativar RLS e criar política de acesso (Permitir acesso total para fins de uso do painel admin)
ALTER TABLE public.custom_model_configs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Permitir tudo em custom_model_configs" ON public.custom_model_configs FOR ALL USING (true) WITH CHECK (true);

-- 2. Tabela para a Carteira Simulada (Simulated Wallet)
CREATE TABLE IF NOT EXISTS public.carteira_simulada (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    match_id int REFERENCES public.matches(id),
    data_aposta timestamp with time zone DEFAULT timezone('utc'::text, now()),
    mercado text NOT NULL,
    selecao text NOT NULL,
    odd_mercado numeric NOT NULL,
    odd_justa numeric NOT NULL,
    edge numeric NOT NULL,
    stake numeric NOT NULL,
    status text DEFAULT 'pendente',
    lucro_prejuizo numeric DEFAULT 0
);

-- Ativar RLS e criar política de acesso
ALTER TABLE public.carteira_simulada ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Permitir tudo em carteira_simulada" ON public.carteira_simulada FOR ALL USING (true) WITH CHECK (true);

-- 3. Tabela para a Configuração da Carteira Simulada (Gerenciamento de Banca e EV)
CREATE TABLE IF NOT EXISTS public.config_carteira (
    id int PRIMARY KEY DEFAULT 1,
    banca_inicial numeric DEFAULT 1000,
    banca_atual numeric DEFAULT 1000,
    tipo_stake text DEFAULT 'kelly',
    stake_fixa numeric DEFAULT 10,
    kelly_multiplier numeric DEFAULT 0.25,
    max_stake_per_round_pct numeric DEFAULT 0.15,
    min_ev_pct numeric DEFAULT 4.0,
    max_ev_pct numeric DEFAULT 10.0
);

-- Ativar RLS e criar política de acesso
ALTER TABLE public.config_carteira ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Permitir tudo em config_carteira" ON public.config_carteira FOR ALL USING (true) WITH CHECK (true);

-- Opcional: Garante que exista a linha com ID 1 em config_carteira
INSERT INTO public.config_carteira (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Notificar o Supabase PostgREST para recarregar o schema cache
NOTIFY pgrst, 'reload schema';
