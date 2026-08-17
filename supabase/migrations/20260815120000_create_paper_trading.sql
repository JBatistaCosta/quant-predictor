-- Carteira (Paper Trading): apostas simuladas PRA FRENTE, sobre partidas
-- ainda não disputadas (status='scheduled') das ligas domésticas com odds
-- ao vivo (`tarefaOddsSync`/`tarefaOddsTodas`, api/model-maintenance.js,
-- LIGAS_DOMESTICAS = [1,4,7,10,13,16]), nos mesmos 3 mercados já cobertos
-- em treino/Simulação de Carteira (MERCADOS_CARTEIRA_SUPORTADOS: '1X2',
-- 'over_under_2.5', 'btts'). Diferente de `custom_model_configs`/
-- `models_registry` (que guardam CONFIGURAÇÃO/METADADO de modelo), estas
-- tabelas guardam o ESTADO de uma carteira que evolui no tempo real (banca
-- persistente, não recalculada a cada chamada como em
-- tarefaSimulacaoCarteira) e o log de cada aposta colocada.
create table paper_trading_carteiras (
  id                 uuid primary key default gen_random_uuid(),
  nome               text not null,
  modelo             text not null,
  mercado            text not null check (mercado in ('1X2', 'over_under_2.5', 'btts')),
  liga_id            integer references leagues(id),
  usar_calibracao    text not null default 'nenhuma' check (usar_calibracao in ('nenhuma', 'platt', 'isotonic')),
  tipo_stake         text not null default 'kelly' check (tipo_stake in ('kelly', 'fixa')),
  stake_fixa         numeric not null default 10,
  kelly_multiplier   numeric not null default 0.25,
  teto_exposicao_pct numeric not null default 0.15,
  ev_minimo          numeric not null default 1.02,
  ev_maximo          numeric not null default 2.0,
  stake_minima_pct   numeric not null default 0.005,
  banca_inicial      numeric not null default 1000,
  banca_atual        numeric not null default 1000,
  ativa              boolean not null default true,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

create index idx_paper_trading_carteiras_ativa on paper_trading_carteiras (ativa);

comment on table paper_trading_carteiras is
  'Carteiras de paper trading (apostas simuladas, dinheiro fictício) sobre '
  'partidas futuras reais, usando odds ao vivo capturadas (odds_market, '
  'snapshot=pre_closing) e as predições que já existem pra partidas '
  'agendadas (predicoes/model_predictions). banca_atual é o saldo vivo '
  '(debitado ao apostar, creditado ao resolver) -- diferente da Simulação '
  'de Carteira (/simulacao-carteira), que é sempre recalculada do zero '
  'sobre histórico já resolvido.';
comment on column paper_trading_carteiras.liga_id is
  'Opcional. Quando null, a carteira considera todas as LIGAS_DOMESTICAS '
  '(únicas com odds ao vivo sincronizadas) -- ver api/model-maintenance.js.';
comment on column paper_trading_carteiras.banca_atual is
  'Saldo disponível AGORA. Cai quando uma aposta pendente é colocada '
  '(stake reservado) e sobe quando ela resolve (stake*odd se ganhou, nada '
  'se perdeu, stake de volta se anulada).';

create table paper_trading_apostas (
  id               uuid primary key default gen_random_uuid(),
  carteira_id      uuid not null references paper_trading_carteiras(id) on delete cascade,
  match_id         integer not null references matches(id),
  selection        text not null,
  bookmaker        text not null,
  odd              numeric not null,
  p_modelo         numeric not null,
  ev               numeric not null,
  stake            numeric not null,
  status           text not null default 'pendente' check (status in ('pendente', 'ganhou', 'perdeu', 'anulada')),
  resultado_liquido numeric,
  banca_antes      numeric not null,
  banca_depois     numeric,
  data_partida     timestamptz not null,
  criado_em        timestamptz not null default now(),
  resolvido_em     timestamptz,
  unique (carteira_id, match_id, selection)
);

create index idx_paper_trading_apostas_carteira_status on paper_trading_apostas (carteira_id, status);
create index idx_paper_trading_apostas_match on paper_trading_apostas (match_id);

comment on table paper_trading_apostas is
  'Uma linha por aposta colocada numa carteira de paper trading. '
  'status=pendente até a partida (matches.status) resolver: finished vira '
  'ganhou/perdeu (calcularResultadoMercadoSimulacao), cancelled vira '
  'anulada (stake devolvido sem lucro/prejuízo), o resto (scheduled/live/'
  'postponed) fica pendente. unique(carteira_id, match_id, selection) '
  'impede apostar duas vezes na mesma seleção da mesma partida na mesma '
  'carteira.';
comment on column paper_trading_apostas.bookmaker is
  'Casa que ofereceu a MELHOR odd capturada pra essa seleção no momento '
  'da aposta, entre as casas de BOOKMAKERS_ALVO (pinnacle/bet365/betano).';

alter table paper_trading_carteiras enable row level security;
alter table paper_trading_apostas enable row level security;

create policy "paper_trading_carteiras_public_read"
  on paper_trading_carteiras for select
  to anon, authenticated
  using (true);

create policy "paper_trading_carteiras_auth_write"
  on paper_trading_carteiras for all
  to authenticated
  using (true)
  with check (true);

create policy "paper_trading_apostas_public_read"
  on paper_trading_apostas for select
  to anon, authenticated
  using (true);

create policy "paper_trading_apostas_auth_write"
  on paper_trading_apostas for all
  to authenticated
  using (true)
  with check (true);
