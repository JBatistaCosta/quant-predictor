-- Elo global atualizado por xG, EM PARALELO ao Elo por resultado (escopo 'global').
-- Tabelas PRÓPRIAS: os CHECKs de team_elo/team_elo_history só aceitam liga|geral|global, e
-- não mexer neles/nos consumidores (RatingClubes.jsx, equipeBanco.js, modelos treinados) é
-- justamente o ponto de rodar em paralelo. Gravado por scripts/elo_global_xg.py (sempre
-- delete-e-regrava numa transação). Especificação e ressalvas: docstring do script e
-- CONTEXTO_PROJETO.md (experimento de 01/10/2026).

create table if not exists public.team_elo_xg (
  team_id bigint primary key references public.teams (id) on delete cascade,
  rating numeric not null,
  partidas integer not null default 0,
  atualizado_em timestamptz not null default now()
);

create table if not exists public.team_elo_xg_history (
  id bigserial primary key,
  team_id bigint not null references public.teams (id) on delete cascade,
  match_id bigint not null references public.matches (id) on delete cascade,
  rodada integer,
  rating_antes numeric not null,
  rating_depois numeric not null,
  match_date date not null,
  usou_xg boolean not null,   -- true = partida atualizada com o escore misto; false = só resultado (fallback)
  unique (team_id, match_id)
);
create index if not exists team_elo_xg_history_match_idx on public.team_elo_xg_history (match_id);
create index if not exists team_elo_xg_history_team_idx on public.team_elo_xg_history (team_id, match_date);

alter table public.team_elo_xg enable row level security;
alter table public.team_elo_xg_history enable row level security;
drop policy if exists "leitura publica" on public.team_elo_xg;
drop policy if exists "leitura publica" on public.team_elo_xg_history;
create policy "leitura publica" on public.team_elo_xg for select using (true);
create policy "leitura publica" on public.team_elo_xg_history for select using (true);

-- escrita atômica (mesmo padrão de registrar_team_elo_lote): chamada só pela service_role
create or replace function public.registrar_team_elo_xg_lote(
  p_elo jsonb, p_historico jsonb, p_apagar boolean default false
) returns void
language plpgsql
as $$
begin
  if p_apagar then
    delete from public.team_elo_xg_history where true;
    delete from public.team_elo_xg where true;
  end if;

  insert into public.team_elo_xg (team_id, rating, partidas, atualizado_em)
  select (e->>'team_id')::bigint, (e->>'rating')::numeric, (e->>'partidas')::integer, (e->>'atualizado_em')::timestamptz
  from jsonb_array_elements(p_elo) as e
  on conflict (team_id) do update set
    rating = excluded.rating, partidas = excluded.partidas, atualizado_em = excluded.atualizado_em;

  insert into public.team_elo_xg_history (team_id, match_id, rodada, rating_antes, rating_depois, match_date, usou_xg)
  select (h->>'team_id')::bigint, (h->>'match_id')::bigint, (h->>'rodada')::integer,
         (h->>'rating_antes')::numeric, (h->>'rating_depois')::numeric, (h->>'match_date')::date, (h->>'usou_xg')::boolean
  from jsonb_array_elements(p_historico) as h
  on conflict (team_id, match_id) do update set
    rodada = excluded.rodada, rating_antes = excluded.rating_antes, rating_depois = excluded.rating_depois,
    match_date = excluded.match_date, usou_xg = excluded.usou_xg;
end;
$$;

revoke all on function public.registrar_team_elo_xg_lote(jsonb, jsonb, boolean) from public, anon, authenticated;

-- Comparação A/B: Brier do escore esperado de cada Elo, com os ratings de ANTES de cada partida,
-- nos MESMOS jogos. HFA de cada Elo no cálculo do esperado: 65 (global por resultado, de
-- elo_global.py) e 60 (xG, de elo_global_xg.py); campo neutro = 0 nos dois. `com_xg` separa os
-- jogos em que o xG existe (onde o experimento foi validado) dos demais (fallback por resultado).
-- Menor Brier = melhor. Os jogos de 10/2026 em diante são o teste de verdade (fora da amostra
-- usada para escolher os parâmetros).
create or replace view public.v_elo_ab_brier as
with j as (
  select m.id, m.match_date::date as d, coalesce(m.is_neutral, false) as neutro,
         case when m.home_goals > m.away_goals then 1.0 when m.home_goals = m.away_goals then 0.5 else 0.0 end as s,
         g1.rating_antes as gh, g2.rating_antes as ga,
         x1.rating_antes as xh, x2.rating_antes as xa,
         exists (select 1 from public.match_stats_fotmob sh where sh.match_id = m.id and sh.team_id = m.home_team_id and sh.xg is not null)
           and exists (select 1 from public.match_stats_fotmob sa where sa.match_id = m.id and sa.team_id = m.away_team_id and sa.xg is not null) as com_xg
  from public.matches m
  join public.team_elo_history g1 on g1.match_id = m.id and g1.team_id = m.home_team_id and g1.escopo = 'global'
  join public.team_elo_history g2 on g2.match_id = m.id and g2.team_id = m.away_team_id and g2.escopo = 'global'
  join public.team_elo_xg_history x1 on x1.match_id = m.id and x1.team_id = m.home_team_id
  join public.team_elo_xg_history x2 on x2.match_id = m.id and x2.team_id = m.away_team_id
  where m.home_goals is not null and m.away_goals is not null
)
select date_trunc('month', d)::date as mes, com_xg, count(*) as jogos,
       avg(power(s - 1 / (1 + power(10, -((gh + case when neutro then 0 else 65 end) - ga) / 400.0)), 2)) as brier_global,
       avg(power(s - 1 / (1 + power(10, -((xh + case when neutro then 0 else 60 end) - xa) / 400.0)), 2)) as brier_xg
from j
group by 1, 2;

grant select on public.v_elo_ab_brier to anon, authenticated;
