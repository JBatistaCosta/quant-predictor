-- Re-sync único de xG/xGOT pós-jogo -- achado real (20/09, investigação de
-- match_id 110225): o FotMob revisa xG/xGOT (e possivelmente outros campos
-- do bloco content.stats.Periods.All.stats) algumas horas depois do apito
-- final, conforme o provedor de dados (Opta) corrige o número inicial --
-- confirmado comparando o snapshot já salvo (capturado ~15h após o jogo)
-- contra a API ao vivo, que retornava xG/xGOT diferentes (2.76->3.30,
-- 5.00->5.34 pro lado casa daquele jogo). `scripts/atualizar_partidas_
-- finalizadas.py` só busca match_stats_fotmob UMA vez por partida (nunca
-- mais rebusca depois que existe 1 linha, exceto com --forcar manual) --
-- essa coluna marca quando essa partida já passou pelo re-fetch único de
-- correção (janela 24-48h pós-jogo, ver script), pra não rebuscar de novo
-- indefinidamente nem competir com o primeiro sync (que continua
-- acontecendo assim que a partida termina, sem esperar 24h).
alter table match_stats_fotmob
  add column if not exists resync_pos_jogo_em timestamptz;
