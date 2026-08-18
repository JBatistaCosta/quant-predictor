-- Posição (0=goleiro/1=defensor/2=meio/3=atacante) usada de fato na SELEÇÃO
-- do XI previsto, gravada junto com a linha em vez de re-derivada depois
-- via join com players.usual_position_id (fonte stale/errada -- bug real
-- reportado pelo usuário: jogador escalado no goleiro sem ser goleiro).
-- Nullable: apenas rede de segurança para eventual `role` fora do mapa
-- conhecido (Keeper/Defender/Midfielder/Attacker, hoje 100% de cobertura).
alter table public.xi_previsto add column posicao_bucket smallint;
comment on column public.xi_previsto.posicao_bucket is
  'Posição usada na seleção do XI (0=goleiro,1=defensor,2=meio,3=atacante), derivada de player_availability_fotmob.role no momento da predição.';
