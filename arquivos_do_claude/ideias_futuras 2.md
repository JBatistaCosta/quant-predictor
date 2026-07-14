# Ideias futuras — quant-futebol-dados

## Fase atual (foco)
Nível de time: resultados + xG/xGA/chutes/posse/faltas/cartões via FBref,
modelo Dixon-Coles para 1X2, Over/Under de gols, e escanteios (quando a
fonte de escanteios estiver disponível). Ligas: Brasileirão + 5 grandes
europeias, temporadas 2023-2025.

## Fase futura — dados de jogadores (não iniciada)

**Descoberta (13/07/2026):** o FBref usa o mesmo padrão de ID estável para
jogadores que já usamos para times:
- Time: `https://fbref.com/en/squads/{fbref_id}/...`
- Jogador: `https://fbref.com/en/players/{fbref_id}/{Nome-Slug}`
- Escudo do time (via CDN, mesmo `fbref_id`):
  `https://cdn.ssref.net/req/{versao}/tlogo/fb/{fbref_id}.png`
  — ⚠️ o segmento `{versao}` muda com o tempo (confirmado: mudou entre
  duas capturas). Nunca gravar a URL completa no banco — só o `fbref_id`,
  e montar a URL na hora de exibir (frontend).

**O que isso habilitaria:**
- Mercados de "props" (artilheiro, assistências, cartões por jogador)
- Ajuste do Dixon-Coles por escalação/lesões — ex: ausência do artilheiro
  titular reduz o λ de ataque do time naquela partida, gerando valor no
  mercado antes das casas ajustarem
- A mesma arquitetura de `team_source_ids` já construída pode virar
  `player_source_ids` (crosswalk de ID estável -> nosso player_id),
  evitando o mesmo problema de casamento de nome por fuzzy que já
  resolvemos para times (e que é ainda mais arriscado em nomes de
  jogadores — muito mais homônimos que clubes)

**Escopo necessário quando for iniciada:**
- Nova tabela `players` + `player_source_ids`
- Nova tabela `player_match_stats` (gols, assistências, cartões, minutos)
- Pipeline de ingestão análogo ao `ingestao_stats_fbref.py`
- Extensão do modelo (Dixon-Coles com ajuste por ausência de jogador-chave)

Retomar depois que o modelo de time estiver validado e calibrado.

## Fase futura — fadiga / dias de descanso entre partidas (não iniciada)

**Contexto (14/07/2026):** ao investigar mando por time (heterogeneidade
real encontrada na Ligue 1), surgiu a pergunta natural de se desgaste
físico (jogos em sequência, fim de temporada) afeta o desempenho. Não dá
pra calcular isso direito hoje porque **só guardamos jogos da liga
doméstica** — um time que jogou Champions League na quarta-feira antes de
um jogo de liga no sábado aparece pra nós como "descansado", quando não
está.

**O que seria necessário:**
- Tabela nova só com fixtures (data + adversário + competição, sem
  precisar de estatística completa) de **todas** as competições de cada
  time — copa nacional, continentais, seleção.
- Feature de "dias desde o último jogo" (e talvez "jogos nos últimos 14
  dias") por time, calculada a partir dessa tabela.
- Indicador de "fim de temporada" (ex: últimas N rodadas), pra testar se
  o efeito de fadiga é mais forte nesse recorte.
- Provavelmente entraria no modelo como um ajuste multiplicativo no lambda
  (parecido com a ideia de fadiga que apareceu — e foi descartada por
  falta de dado — na primeira especificação de projeto lá no início).

Prioridade mais baixa que o histórico ampliado (mando por time), que já
está em andamento.
