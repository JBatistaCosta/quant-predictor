-- Posição granular (CB/LB/RB/LWB/RWB/CDM/CM/etc, positionIdsDesc do FotMob
-- -- confirmado real testando o payload bruto do endpoint de elenco já
-- usado por ingestao_fotmob_elenco.py) -- pedido do usuário: distinguir
-- zagueiro de lateral pra exigir 2 ZAG + 2 LAT (não só "4 defensores"
-- genéricos) na seleção do XI previsto. Guarda só a PRIMEIRA posição
-- (a mais comum do jogador, quando `positionIdsDesc` lista mais de uma).
alter table public.player_availability_fotmob add column posicao_detalhe text;
comment on column public.player_availability_fotmob.posicao_detalhe is
  'Posição granular do FotMob (positionIdsDesc, ex. CB/LB/RB/CDM/CM/RW/ST) -- primeira posição listada quando há mais de uma.';
