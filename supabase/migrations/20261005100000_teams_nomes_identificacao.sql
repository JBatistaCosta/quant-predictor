-- Identificação redundante de time: vários nomes por time para o casamento de
-- nome de fonte externa (api/_lib/nomesTimes.js) não depender de um único
-- texto. `name` continua sendo o nome canônico usado por todo o resto do app;
-- as colunas abaixo são opcionais e só ajudam a reconhecer o time.
alter table public.teams
  add column if not exists display_name text,        -- nome a ser mostrado na interface
  add column if not exists name_pt text,             -- nome em português
  add column if not exists name_en text,             -- nome em inglês
  add column if not exists name_native text,         -- nome na língua original (alfabeto original)
  add column if not exists nicknames text[] not null default '{}';  -- apelidos (0..n)

comment on column public.teams.display_name is 'Nome a ser mostrado na interface (cai em name quando nulo).';
comment on column public.teams.name_pt is 'Nome em português.';
comment on column public.teams.name_en is 'Nome em inglês.';
comment on column public.teams.name_native is 'Nome na língua original, no alfabeto original (ex.: Россия, 日本).';
comment on column public.teams.nicknames is 'Apelidos do time (ex.: Seleção Canarinho, Three Lions). Apelido repetido em dois times vira ambiguidade e não casa.';
