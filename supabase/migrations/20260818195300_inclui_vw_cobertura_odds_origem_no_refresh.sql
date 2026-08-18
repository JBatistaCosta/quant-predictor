create or replace function public.refresh_vw_cobertura_odds()
returns void
language plpgsql
security definer
set search_path to 'public'
as $function$
begin
  refresh materialized view concurrently vw_cobertura_odds;
  refresh materialized view concurrently vw_cobertura_odds_bookmaker;
  refresh materialized view concurrently vw_cobertura_odds_origem;
end;
$function$;
