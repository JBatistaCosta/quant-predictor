-- Verificação da busca por apelido/semelhança (migration 20261003120000_busca_jogadores_apelido_fuzzy.sql).
-- Rodar no SQL Editor do Supabase DEPOIS de aplicar a migration. Resultado esperado em cada comentário.

-- 1) Apelido: Gabriel Barbosa (fotmob 450848) primeiro, score 1.00, apelido 'Gabigol'.
select b.player_id, p.name, round(b.score::numeric, 2) score, b.apelido
  from buscar_jogadores('Gabigol', 5) b join players p on p.id = b.player_id;

-- 2) Espaço sobrando e espaços duplos: Gabriel Barbosa (1.00) antes de 'Mastherson Gabriel Barbosa Lopes' (0.90).
select p.name, round(b.score::numeric, 2) score
  from buscar_jogadores('  Gabriel   Barbosa ', 5) b join players p on p.id = b.player_id;

-- 3) Erro de digitação: 'Cristiano Ronalod' -> Cristiano Ronaldo (~0.83) no topo.
select p.name, round(b.score::numeric, 2) score
  from buscar_jogadores('cristiano ronalod', 3) b join players p on p.id = b.player_id;

-- 4) Acento: 'Paquetá' acha 'Lucas Paqueta' (nome sem acento no FotMob).
select p.name, round(b.score::numeric, 2) score
  from buscar_jogadores('Paquetá', 3) b join players p on p.id = b.player_id;

-- 5) Lixo não passa: nenhuma linha.
select * from buscar_jogadores('xyzxyz', 5);

-- 6) Semente de apelidos (esperado: 7 linhas, fonte 'semente').
select a.alias, p.name, p.fotmob_player_id
  from player_aliases a join players p on p.id = a.player_id order by p.name, a.alias;

-- Para cadastrar um apelido novo (confira o fotmob_player_id do jogador antes; NUNCA case por nome):
-- insert into player_aliases (player_id, alias, fonte)
-- select id, 'Apelido Aqui', 'manual' from players where fotmob_player_id = '000000' on conflict do nothing;
