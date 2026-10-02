-- Registro de apostas hipotéticas de EV (hoje: 1X2 do modelo Elo por xG contra a Pinnacle).
-- Gravada por scripts/calcular_ev_mercados.py --gravar (service_role); lida pelo auditor
-- arquivos_do_claude/analisar_calibracao_ev.py --supabase. Aposta HIPOTÉTICA: nada aqui move dinheiro.
-- Entrada = snapshot pre_closing; fechamento = snapshot closing desvigado (CLV só existe com os dois).

create table if not exists public.ev_apostas_hipoteticas (
  id bigserial primary key,
  partida_id bigint not null references public.matches (id) on delete cascade,
  liga_codigo text,
  temporada text,
  modelo text not null,
  mercado text not null,
  selecao text not null,
  prob_modelo numeric not null check (prob_modelo > 0 and prob_modelo < 1),
  odd_entrada numeric not null check (odd_entrada > 1),
  odd_fechamento_pinnacle numeric,
  odd_fechamento_justa numeric,
  ev_estimado numeric not null,
  resultado_real smallint not null check (resultado_real in (0, 1)),
  retorno_unidade numeric not null,
  stake_flat numeric not null default 1,
  stake_kelly numeric not null,             -- fração da banca, Kelly x 0,25
  clv numeric,
  anomalia_dados boolean not null default false,   -- EV > +35% ou odds suspeitas: auditar à mão antes de usar
  motivo_anomalia text,
  entrada_verificada_por_timestamp boolean not null default false,  -- captured_at < horário do jogo
  criado_em timestamptz not null default now(),
  unique (partida_id, mercado, selecao, modelo)
);
create index if not exists ev_apostas_hipoteticas_partida_idx on public.ev_apostas_hipoteticas (partida_id);

alter table public.ev_apostas_hipoteticas enable row level security;
drop policy if exists "leitura publica" on public.ev_apostas_hipoteticas;
create policy "leitura publica" on public.ev_apostas_hipoteticas for select using (true);
