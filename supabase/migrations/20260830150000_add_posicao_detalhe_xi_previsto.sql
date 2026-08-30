-- Posição granular (GK/CB/RB/LB/RWB/LWB/CDM/CM/CAM/RM/LM/RW/LW/ST, mesmo
-- código do FotMob já usado em player_availability_fotmob.posicao_detalhe)
-- usada de fato na SELEÇÃO do XI previsto, gravada junto com a linha --
-- mesmo raciocínio de posicao_bucket (migration 20260816201100): a mesma
-- fonte usada pra selecionar precisa ser a que aparece na exibição, sem
-- re-derivar via join que pode divergir do que foi usado na hora.
--
-- Pedido do usuário: identificar posição fina (lateral D/E, volante, ponta
-- D/E, centroavante etc.) além do bucket goleiro/defesa/meio/ataque, pra
-- facilitar achar substituto direto na mesma posição. Nullable: nem todo
-- jogador tem posicao_detalhe capturado ainda (~15-20% do elenco em
-- produção, ver GROUP BY em player_availability_fotmob) -- cai pro bucket
-- grosso (posicao_bucket) quando ausente.
alter table public.xi_previsto add column posicao_detalhe text;
comment on column public.xi_previsto.posicao_detalhe is
  'Posição granular (código FotMob: GK/CB/RB/LB/RWB/LWB/CDM/CM/CAM/RM/LM/RW/LW/ST) usada na seleção do XI, derivada de player_availability_fotmob.posicao_detalhe no momento da predição. Nullable -- nem todo jogador tem esse dado capturado.';
