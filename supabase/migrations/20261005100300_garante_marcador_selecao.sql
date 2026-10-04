-- Toda seleção precisa ter teams.is_national_team = true.
-- Regra (pelos dados, nunca por nome): time que aparece em partida de uma liga
-- com leagues.type = 'international' (Eurocopa, Copa do Mundo, Copa América...)
-- é seleção.
--
-- 1) Conserto: 12 seleções da Copa do Mundo 2026 estavam sem o marcador
--    (Argélia, Bósnia, Cabo Verde, RD Congo, Curaçao, Iraque, Costa do Marfim,
--    Jordânia, Nova Zelândia, Noruega, África do Sul, Uzbequistão). Nenhum dos
--    78 times dessas ligas joga competição fora delas.
-- (Forma eficiente: parte só dos jogos das ligas de seleções. A forma com
-- EXISTS correlacionado varria `matches` inteira por time e estourou 60 s.)
with ids as (
  select m.home_team_id as id from public.matches m
   where m.league_id in (select id from public.leagues where type = 'international')
  union
  select m.away_team_id from public.matches m
   where m.league_id in (select id from public.leagues where type = 'international')
)
update public.teams
set is_national_team = true
where id in (select id from ids) and is_national_team is not true;

-- 2) Garantia: qualquer partida gravada ou alterada numa liga de seleções marca
--    os dois times como seleção, seja qual for o importador (football-data,
--    API-Football, FotMob...). Barato: depois da primeira vez o UPDATE não
--    encontra linha para mudar.
create or replace function public.marcar_selecoes_por_liga()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if exists (select 1 from public.leagues l where l.id = new.league_id and l.type = 'international') then
    update public.teams
    set is_national_team = true
    where id in (new.home_team_id, new.away_team_id)
      and is_national_team is not true;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_marcar_selecoes_por_liga on public.matches;
create trigger trg_marcar_selecoes_por_liga
  after insert or update of league_id, home_team_id, away_team_id on public.matches
  for each row execute function public.marcar_selecoes_por_liga();
