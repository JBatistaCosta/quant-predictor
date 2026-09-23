-- =============================================================================
-- Migration: league_markov_params -- parâmetros calibrados do motor de
-- simulação por Cadeia de Markov multi-evento
-- =============================================================================
-- Espelha o espírito de `league_model_params` (parâmetro por liga, fallback
-- global quando `league_id is null`), mas com forma própria: aqui cada
-- evento (gol/chute/cartão/...) tem VÁRIOS tipos de multiplicador (estado de
-- placar, vantagem numérica, janela pós-evento, minuto), cada um com várias
-- chaves (buckets) -- não um escalar único como `disp_r`. Misturar as duas
-- semânticas na tabela existente juntaria "forma da distribuição" com "série
-- de multiplicadores de taxa por contexto"; melhor tabela dedicada, mesma
-- disciplina de RLS/índices das outras.
--
-- GRANULARIDADE: por liga, agregando todas as temporadas (não liga+temporada)
-- -- mesma decisão de `league_model_params`/`disp_r`. Os próprios achados que
-- alimentam esta tabela (ACHADOS_COMPORTAMENTO.md, Achados 9/13) já alertam
-- pra amostra pequena mesmo agregando uma temporada inteira; abrir por
-- temporada dentro do projeto fragmentaria ainda mais um evento já raro
-- (cartão vermelho: só aparece de forma robusta quando controlado por força,
-- que já precisa de volume).
--
-- `amostra_n` é OBRIGATÓRIO por causa desse histórico de achados com amostra
-- pequena -- permite depois decidir, por liga, se o valor calibrado é
-- confiável o bastante ou se deve cair no fallback global. `origem` documenta
-- de onde veio o número (tabela/método próprio do projeto, ou achado externo
-- StatsBomb não recalibrável por liga -- ver ACHADOS_COMPORTAMENTO.md Achado
-- 13 pra escanteio/falta).
-- =============================================================================

create table if not exists public.league_markov_params (
  id            bigint generated always as identity primary key,
  league_id     bigint references public.leagues(id) on delete cascade,
  evento        text not null check (evento in ('gol','chute','chute_no_alvo','cartao_amarelo','cartao_vermelho','escanteio','falta')),
  tipo          text not null check (tipo in ('taxa_base_90','mult_estado_placar','mult_vantagem_numerica','mult_janela_pos_evento','mult_minuto_bin')),
  chave         text not null,
  valor         numeric not null,
  amostra_n     integer,
  origem        text not null,
  calibrado_em  timestamptz not null default now(),

  constraint league_markov_params_key unique (league_id, evento, tipo, chave)
);

-- ATENÇÃO PRA QUEM FOR ESCREVER NESTA TABELA COM league_id NULL (fallback
-- global): o Postgres trata NULL como distinto de qualquer outro NULL, então
-- a constraint acima NÃO deduplica duas linhas com o mesmo (evento, tipo,
-- chave) e league_id=null -- um upsert simples (ON CONFLICT nesses 4
-- campos) cria linha nova em vez de atualizar. Scripts de calibração
-- (arquivos_do_claude/calibrar_markov_*.py) nunca escrevem league_id=null
-- (sempre agregam por liga real), então o upsert deles é seguro. Quem
-- precisar semear/atualizar uma linha de fallback global deve fazer
-- DELETE do escopo antes do INSERT (mesmo padrão de idempotência já usado
-- em derivar_game_state/derivar_cartoes_estado), nunca confiar em ON
-- CONFLICT pra essas linhas -- ver migration
-- 20260922130000_seed_markov_params_escanteio_falta.sql.

comment on table public.league_markov_params is
  'Parâmetros calibrados (taxa base e multiplicadores de estado/janela/minuto) do motor de simulação por Cadeia de Markov multi-evento. league_id NULL = fallback global. Escrito só pelos scripts de calibração em arquivos_do_claude/ (service_role).';
comment on column public.league_markov_params.evento is
  'Mercado simulado: gol, chute, chute_no_alvo, cartao_amarelo, cartao_vermelho, escanteio, falta.';
comment on column public.league_markov_params.tipo is
  'taxa_base_90 = taxa por 90 minutos sem contexto; mult_estado_placar = multiplicador por perdendo/empatando/ganhando; mult_vantagem_numerica = por diferença de jogadores em campo; mult_janela_pos_evento = por janela após gol/expulsão (marcou/sofreu/expulsao_pro/expulsao_contra × 0-5/5-15/regime); mult_minuto_bin = por faixa de 15 minutos.';
comment on column public.league_markov_params.chave is
  'Bucket dentro do tipo -- ex.: "perdendo"/"empatando"/"ganhando", "marcou_0-5", "0-14".';
comment on column public.league_markov_params.amostra_n is
  'Nº de partidas/observações por trás do valor. Histórico do projeto (ACHADOS_COMPORTAMENTO.md) tem múltiplos casos de efeito que some ou inverte com amostra pequena -- decidir uso do valor por liga com isto em mãos, não só o valor cru.';
comment on column public.league_markov_params.origem is
  'Tabela/método usado na calibração, ex. "match_team_game_state (Elo-controlado, v_game_state_por_forca)", "match_shots_fotmob", "match_events", ou "statsbomb_achado_13 (forma externa, não recalibrável por liga)".';

create index if not exists idx_league_markov_params_lookup
  on public.league_markov_params (evento, tipo, league_id);

alter table public.league_markov_params enable row level security;

drop policy if exists "league_markov_params_public_read" on public.league_markov_params;
create policy "league_markov_params_public_read"
  on public.league_markov_params for select to anon, authenticated using (true);
