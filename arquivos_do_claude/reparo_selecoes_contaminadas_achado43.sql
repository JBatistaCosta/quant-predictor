-- REPARO JÁ EXECUTADO em 2026-10-04 (Achado 43). Guardado só como registro e
-- para reaproveitar o método -- NÃO reaplicar: usa ids deste banco.
--
-- Problema: a regra antiga de casamento de nome ("um nome contido no outro")
-- misturou dois times diferentes em um só `team_id`:
--   * 466 'England'    = seleção inglesa + New England Revolution (138 jogos
--                        da MLS 2022-2025; FotMob 'New England' = id 6580)
--   * 744 'Costa Rica ' = clube brasileiro (API-Football 13103, 2 jogos de
--                        Copa do Brasil) + seleção (FotMob 6705, 12 jogos de
--                        Copa América 2016/2024 e Copa do Mundo 2018/2022)
--
-- Método: (1) criar o time que faltava e o vínculo certo; (2) mover os jogos
-- (matches.home/away_team_id); (3) mover as linhas de cada tabela com team_id
-- E match_id, só para os jogos movidos (as colunas team_id das tabelas por
-- jogo nunca se movem sem o jogo); (4) mover tabelas sem match_id caso a caso;
-- (5) reconstruir o Elo global e o Elo de xG (workflows elo_global.yml em modo
-- completo e elo_global_xg.yml). Conferência: em cada tabela, linhas
-- encontradas = linhas movidas; depois, nenhuma linha com team_id fora dos
-- dois times do jogo.
--
-- Resultado: 466 ficou só com 42 jogos de Eurocopa/Copa do Mundo; 1046 = New
-- England Revolution (138 jogos MLS); 1047 = Costa Rica seleção (12 jogos);
-- 744 ficou como o clube brasileiro (2 jogos de Copa do Brasil).

-- (1) times e vínculos
with ne as (
  insert into teams(name,country,is_national_team,crest_url,aliases,display_name,name_en,name_pt,name_native)
  values ('New England Revolution',(select country from teams where id=811),false,
          'https://images.fotmob.com/image_resources/logo/teamlogo/6580_xsmall.png',
          array['New England'],'New England Revolution','New England Revolution','New England Revolution','New England Revolution')
  returning id),
crc as (
  insert into teams(name,country,is_national_team,crest_url,aliases,display_name,name_en,name_pt,name_native,nicknames)
  values ('Costa Rica',null,true,'https://images.fotmob.com/image_resources/logo/teamlogo/6705_xsmall.png',
          '{}','Costa Rica','Costa Rica','Costa Rica','Costa Rica',array['Los Ticos'])
  returning id),
l1 as (insert into team_source_ids(team_id,source,source_id,source_name) select id,'fotmob','6580','New England' from ne returning 1),
l2 as (update team_source_ids set team_id=(select id from crc) where source='fotmob' and source_id='6705' and team_id=744 returning 1),
-- (2) jogos
m1 as (update matches set home_team_id=(select id from ne) where league_id=29 and home_team_id=466 returning 1),
m2 as (update matches set away_team_id=(select id from ne) where league_id=29 and away_team_id=466 returning 1),
m3 as (update matches set home_team_id=(select id from crc) where league_id in (27,30) and home_team_id=744 returning 1),
m4 as (update matches set away_team_id=(select id from crc) where league_id in (27,30) and away_team_id=744 returning 1)
select (select id from ne) ne_id, (select id from crc) crc_id;   -- 1046 e 1047

-- (3) tabelas por jogo (executado em lotes; esta é a forma de um lote)
-- Tabelas: match_disciplina, match_events, match_features_contexto,
-- match_formation_fotmob, match_goal_timeline, match_stats, match_stats_fotmob,
-- match_stats_fotmob_periodo, match_team_cartoes_estado, match_team_event_response,
-- match_team_game_state, match_unavailable_coleta_fotmob, team_unavailable_fotmob,
-- xi_previsto, xi_titular_walkforward, match_shots_fotmob, match_lineup_fotmob,
-- match_player_stats_fotmob, match_player_stats_detalhe_fotmob,
-- match_player_heatmap_fotmob, player_match_estimates, player_match_walkforward,
-- team_elo_history, team_elo_xg_history (+ scratch_* de análise).
--   update <tabela> set team_id=1046 where team_id=466
--     and match_id in (select id from matches where league_id=29 and (home_team_id=1046 or away_team_id=1046));
--   update <tabela> set team_id=1047 where team_id=744
--     and match_id in (select id from matches where league_id in (27,30) and (home_team_id=1047 or away_team_id=1047));

-- (4) sem match_id
update team_elo set team_id=1046 where id=11862 and team_id=466 and escopo='liga' and league_id=29;  -- Elo da MLS
update team_transfers_fotmob set team_id=1046 where team_id=466;       -- 25 transferências, todas do New England
update player_availability_fotmob set team_id=1047 where team_id=744;  -- 36 jogadores da seleção
-- elenco_atual_fotmob da seleção: copiado de 744 para 1047 (com "team_id" interno reescrito) e zerado em 744.
