// src/pages/AnaliseAvancadaEvento.jsx — rota /analise-avancada/:matchId
//
// Painel "inspirado" em AnaliseEvento.jsx (/analise), mas invertendo o fluxo:
// lá o usuário escolhe times e fórmula manualmente; aqui os parâmetros do
// modelo misto (λ dos gols, ρ de Dixon-Coles, λ/dispersão/α/β de escanteios)
// são IMPRESSOS AUTOMATICAMENTE do banco (`model_match_estimates.params`)
// pra uma partida específica, sem nenhum campo editável. Todo o aparato de
// AnaliseEvento.jsx (Monte Carlo, Kelly, scanner EV+ multi-mercado, odds de
// bookmaker) fica de fora — esse painel só mostra o que o modelo misto já
// estimou e persistiu.
//
// Reaproveita a mesma matemática de `src/utils/distribuicoesMercados.js`
// (gêmeo JS de `scripts/distribuicoes.py`, com teste de paridade) — nenhuma
// derivação de mercado é escrita de novo aqui.
//
// `model_match_estimates` tem RLS de leitura pública, então a consulta é
// direta via supabase-js (sem função serverless nova — api/ já está no teto
// de 12 do plano Hobby do Vercel).
import React, { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, AlertTriangle, Shield, Loader2, FlaskConical, Target, TrendingUp, Percent, Scale, Download, Camera, Check, X } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import {
  matrizPlacares, mercadosDeGols, mercadosDeEscanteios, distribuicaoConjuntaEscanteios, lerParametrosPartida, rotuloLinha,
} from '../utils/distribuicoesMercados';
import { devigarOddsRatio, stakeKelly25 } from '../utils/devig';
import { toPct } from '../utils/format';
import { indexarCalibracao, calibrarProbabilidade } from '../utils/calibration';
import { poissonCDF } from '../utils/poisson';
import { extractJsonFromImage } from '../utils/ocr';

// Mercados em que o modelo misto (gols/escanteios) tem probabilidade
// calculada E que aparecem salvos em odds_market — únicos candidatos pra
// comparação de EV. A chave usada aqui é sempre a string de `market` como
// salva no banco; handicap (asiático e de escanteios) usa outra convenção
// de nome (`asian_handicap_<linha>`/`corners_handicap_<linha>`) e fica fora
// de escopo por ora.
const LINHAS_OU_EV = [0.5, 1.5, 2.5, 3.5, 4.5];
const LINHAS_OU_CORNERS_EV = [7.5, 8.5, 9.5, 10.5, 11.5]; // total do jogo — mesmas linhas de LINHAS_OU_CORNERS
const LINHAS_OU_CORNERS_TIME_EV = [3.5, 4.5, 5.5, 6.5]; // por time — mesmas linhas do card "Escanteios por time"

function mercadosComparaveis(mercadosGols, mercadosCorners) {
  const saida = {};
  if (mercadosGols) {
    saida['1X2'] = mercadosGols['1X2'];
    saida.btts = mercadosGols.btts;
    for (const linha of LINHAS_OU_EV) saida[`over_under_${rotuloLinha(linha)}`] = mercadosGols[`over_under_${rotuloLinha(linha)}`];
  }
  if (mercadosCorners) {
    saida.corners_1x2 = mercadosCorners.corners_1X2;
    for (const linha of LINHAS_OU_CORNERS_EV) {
      saida[`corners_over_under_full_time_${rotuloLinha(linha)}`] = mercadosCorners[`corners_over_under_${rotuloLinha(linha)}`];
    }
    for (const linha of LINHAS_OU_CORNERS_TIME_EV) {
      saida[`corners_over_under_team_1_${rotuloLinha(linha)}`] = mercadosCorners[`corners_home_over_under_${rotuloLinha(linha)}`];
      saida[`corners_over_under_team_2_${rotuloLinha(linha)}`] = mercadosCorners[`corners_away_over_under_${rotuloLinha(linha)}`];
    }
  }
  return saida;
}

// Todas as strings de `market` acima, num array — usado pra filtrar a
// consulta em odds_market direto no Supabase (`.in('market', ...)`), o que
// já evita de longe o corte silencioso de 1000 linhas do PostgREST (partidas
// antigas/muito negociadas podem ter dezenas de milhares de linhas somando
// TODOS os mercados salvos — a paginação abaixo cobre o resto).
const MERCADOS_EV = [
  '1X2', 'btts', ...LINHAS_OU_EV.map((l) => `over_under_${rotuloLinha(l)}`),
  'corners_1x2', ...LINHAS_OU_CORNERS_EV.map((l) => `corners_over_under_full_time_${rotuloLinha(l)}`),
  ...LINHAS_OU_CORNERS_TIME_EV.map((l) => `corners_over_under_team_1_${rotuloLinha(l)}`),
  ...LINHAS_OU_CORNERS_TIME_EV.map((l) => `corners_over_under_team_2_${rotuloLinha(l)}`),
];

function formatarSnapshot(capturedAt) {
  return new Date(capturedAt).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function rotuloMercado(mercado) {
  if (mercado === '1X2') return '1X2 (gols)';
  if (mercado === 'btts') return 'Ambas Marcam';
  if (mercado === 'corners_1x2') return 'Escanteios 1X2';
  const ouGols = mercado.match(/^over_under_(\d+\.\d)$/);
  if (ouGols) return `O/U ${ouGols[1]} gols`;
  const ouCornersTotal = mercado.match(/^corners_over_under_full_time_(\d+\.\d)$/);
  if (ouCornersTotal) return `O/U ${ouCornersTotal[1]} escanteios (total)`;
  const ouCornersTime1 = mercado.match(/^corners_over_under_team_1_(\d+\.\d)$/);
  if (ouCornersTime1) return `O/U ${ouCornersTime1[1]} escanteios (mandante)`;
  const ouCornersTime2 = mercado.match(/^corners_over_under_team_2_(\d+\.\d)$/);
  if (ouCornersTime2) return `O/U ${ouCornersTime2[1]} escanteios (visitante)`;
  return mercado;
}

// Confere se uma seleção específica bateu com o resultado REAL da partida --
// só usado pra partida já finalizada (ver "Resultado" na Verificação de EV).
// `resultado` = { golsHome, golsAway, cornersHome, cornersAway } — corners
// ficam null quando a fonte de estatística não cobre a partida (ver
// buscarCornersReais), e nesse caso os mercados de escanteios retornam null
// (sem dado real pra avaliar), não `false`.
function avaliarSelecao(mercado, selecao, resultado) {
  const totalGols = resultado.golsHome + resultado.golsAway;
  if (mercado === '1X2') {
    const vencedor = resultado.golsHome > resultado.golsAway ? 'home' : resultado.golsHome < resultado.golsAway ? 'away' : 'draw';
    return selecao === vencedor;
  }
  if (mercado === 'btts') {
    const ambasMarcaram = resultado.golsHome > 0 && resultado.golsAway > 0;
    return (selecao === 'yes') === ambasMarcaram;
  }
  const ouGols = mercado.match(/^over_under_(\d+\.\d)$/);
  if (ouGols) return (selecao === 'over') === (totalGols > Number(ouGols[1]));

  if (resultado.cornersHome == null || resultado.cornersAway == null) return null;
  if (mercado === 'corners_1x2') {
    const vencedor = resultado.cornersHome > resultado.cornersAway ? 'home' : resultado.cornersHome < resultado.cornersAway ? 'away' : 'draw';
    return selecao === vencedor;
  }
  const totalCorners = resultado.cornersHome + resultado.cornersAway;
  const ouCornersTotal = mercado.match(/^corners_over_under_full_time_(\d+\.\d)$/);
  if (ouCornersTotal) return (selecao === 'over') === (totalCorners > Number(ouCornersTotal[1]));
  const ouCornersTime1 = mercado.match(/^corners_over_under_team_1_(\d+\.\d)$/);
  if (ouCornersTime1) return (selecao === 'over') === (resultado.cornersHome > Number(ouCornersTime1[1]));
  const ouCornersTime2 = mercado.match(/^corners_over_under_team_2_(\d+\.\d)$/);
  if (ouCornersTime2) return (selecao === 'over') === (resultado.cornersAway > Number(ouCornersTime2[1]));
  return null;
}

// Escanteios reais (casa/fora) da partida finalizada -- mesmo fallback já
// usado no painel de cobertura de estatísticas (PR #328): prioriza
// match_stats (Understat/fbref/football-data.co.uk, 5 ligas europeias +
// Brasileirão via football-data.co.uk), cai pra match_stats_fotmob (FotMob,
// cobertura mais ampla) quando a primeira fonte não tem a partida.
async function buscarCornersReais(matchId, homeTeamId, awayTeamId) {
  const [{ data: ms }, { data: msf }] = await Promise.all([
    supabase.from('match_stats').select('team_id, corners').eq('match_id', matchId),
    supabase.from('match_stats_fotmob').select('team_id, corners').eq('match_id', matchId),
  ]);
  const cornersDoTime = (teamId) => {
    const doMs = ms?.find((r) => r.team_id === teamId)?.corners;
    if (doMs != null) return doMs;
    return msf?.find((r) => r.team_id === teamId)?.corners ?? null;
  };
  return { cornersHome: cornersDoTime(homeTeamId), cornersAway: cornersDoTime(awayTeamId) };
}

const LINHAS_OU_GOLS = [0.5, 1.5, 2.5, 3.5, 4.5];
const LINHAS_HANDICAP = [-1.5, -1, -0.5, 0, 0.5, 1, 1.5];
const LINHAS_OU_CORNERS = [7.5, 8.5, 9.5, 10.5, 11.5];
const N_PLACARES_EXATOS = 12;
// Tetos só da GRADE visual (matriz de mercados usa o teto padrão, mais alto,
// pra não perder precisão nas caudas -- ver GradeMatriz mais abaixo).
const MAX_GOLS_GRADE = 7;
const MAX_CORNERS_GRADE = 14;

function Escudo({ url, tamanho = 20 }) {
  return url
    ? <img src={url} alt="" className="object-contain shrink-0" style={{ width: tamanho, height: tamanho }} />
    : <Shield size={tamanho * 0.8} className="text-slate-700 shrink-0" />;
}

function fmtPct(v) { return v == null ? '—' : `${(v * 100).toFixed(1)}%`; }
function fmtNum(v, casas = 3) { return v == null ? '—' : Number(v).toFixed(casas); }

// Número pra célula de CSV -- diferente de fmtNum (que usa '—' pra tela),
// aqui vazio (célula em branco) é a convenção certa pra dado ausente numa
// planilha, não o traço.
function numCSV(v, casas = 3) { return v == null ? '' : Number(v).toFixed(casas); }

// Nome de arquivo seguro (sem acento/espaço/caractere especial) -- nomes de
// time entram direto no nome do CSV exportado.
function sanitizarNomeArquivo(s) {
  return String(s || '')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-zA-Z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '') || 'jogo';
}

// Exportação CSV client-side -- mesmo padrão já usado em XiModeloStats.jsx
// (Blob + BOM UTF-8 + <a download>, sem lib externa), reaproveitado aqui
// pra exportar chutes/gols/xG por jogador e a verificação de EV/stake, pro
// usuário cruzar os números fora do app (pedido explícito).
function exportarCSV(linhas, colunas, nomeArquivo) {
  const csvEscape = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const cabecalho = colunas.map((c) => csvEscape(c.header)).join(',');
  const corpo = linhas.map((l) => colunas.map((c) => csvEscape(c.get(l))).join(','));
  const csv = [cabecalho, ...corpo].join('\n');
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = nomeArquivo;
  a.click();
  URL.revokeObjectURL(url);
}

function BotaoExportarCSV({ onClick, disabled }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-lg bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-slate-300 hover:text-white transition-colors shrink-0"
    >
      <Download size={12} /> Exportar CSV
    </button>
  );
}

function CardParametro({ label, valor }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-3">
      <div className="text-[10px] text-slate-500 uppercase tracking-wider">{label}</div>
      <div className="text-lg font-mono text-slate-100 mt-0.5">{valor}</div>
    </div>
  );
}

function Secao({ titulo, icone: Icone, children }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5">
      <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-2">
        {Icone && <Icone size={15} className="text-emerald-400" />} {titulo}
      </h3>
      {children}
    </div>
  );
}

// Grade de calor da matriz conjunta (placar ou escanteios casa×fora) --
// mesma matriz que já alimenta os mercados acima (1X2/placar exato ou
// corners_1X2/faixas), só que mostrada célula a célula em vez de agregada.
function GradeMatriz({ titulo, matriz, rotuloCasa, rotuloFora }) {
  if (!matriz?.length) return null;
  const n = matriz.length;
  const max = Math.max(...matriz.flat());
  return (
    <Secao titulo={titulo} icone={Target}>
      <div className="overflow-x-auto">
        <table className="text-[11px] font-mono border-separate" style={{ borderSpacing: 2 }}>
          <thead>
            <tr>
              <th className="p-1 text-slate-600 text-left align-bottom">
                <div className="leading-tight">{rotuloCasa}<br />↓ / {rotuloFora} →</div>
              </th>
              {Array.from({ length: n }, (_, j) => (
                <th key={j} className="p-1 text-slate-500 font-normal">{j}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matriz.map((linha, i) => (
              <tr key={i}>
                <td className="p-1 text-slate-500 text-right">{i}</td>
                {linha.map((p, j) => (
                  <td
                    key={j}
                    className="p-1 text-center rounded text-slate-200"
                    style={{ background: max > 0 ? `rgba(16,185,129,${(p / max) * 0.7})` : undefined }}
                    title={`${rotuloCasa}=${i}, ${rotuloFora}=${j}: ${(p * 100).toFixed(2)}%`}
                  >
                    {(p * 100).toFixed(1)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Secao>
  );
}

function LinhaBinaria({ rotulo, over, under, rotuloOver = 'Over', rotuloUnder = 'Under' }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-slate-800 last:border-0 text-sm">
      <span className="text-slate-400 w-20 shrink-0">{rotulo}</span>
      <div className="flex gap-4 font-mono">
        <span className="text-emerald-400 w-24 text-right">{rotuloOver} {fmtPct(over)}</span>
        <span className="text-orange-400 w-24 text-right">{rotuloUnder} {fmtPct(under)}</span>
      </div>
    </div>
  );
}

// "Marcar a qualquer momento" a partir de um lambda de Poisson --
// P(gols>=1) = 1 - e^(-lambda), mesma fórmula já usada pra 1X2/O-U de time
// (poissonCDF cobre P(X<=k), aqui é o caso degenerado P(X=0) invertido).
function probMarcar(lambdaGols) { return lambdaGols == null ? null : 1 - Math.exp(-Math.max(lambdaGols, 0)); }

// ---------------------------------------------------------------------------
// Odds justas individuais (chutes ao gol / gols, linhas +1/+2/+3) + import
// via OCR de odds reais de casa de apostas pra comparação lado a lado.
// ---------------------------------------------------------------------------

// P(X >= k) via Poisson -- generaliza probMarcar (P(X>=1)) pras linhas +2/+3.
function probPeloMenos(lambdaPoisson, k) {
  return lambdaPoisson == null ? null : 1 - poissonCDF(Math.max(lambdaPoisson, 0), k - 1);
}
// Odds justa = 1/probabilidade, SEM margem -- mesma convenção de
// `model_predictions.fair_odds` (coluna gerada no banco), só replicada aqui
// pro lado do cliente porque esses λ de jogador nunca passam por essa tabela
// (ver plano da sessão: "sem persistir 1 linha por linha de aposta").
function oddsJusta(p) { return p != null && p > 1e-9 ? 1 / p : null; }
function fmtOdds(v) { return v == null ? '—' : v.toFixed(2); }

// Melhor (maior) odd entre as casas importadas pra um campo -- mesmo
// critério já estabelecido no projeto pra "melhor odd entre casas" (ver
// melhorOddPorChave em api/model-maintenance.js, usado na carteira, e o
// reduce equivalente em ModelBenchmarking.jsx): é a odd que de fato dá
// mais valor pro apostador, não a mais recente nem a de uma casa fixa.
function melhorOddDoCampo(porCasa) {
  if (!porCasa) return null;
  let melhor = null;
  for (const [casa, odd] of Object.entries(porCasa)) {
    if (odd != null && (!melhor || odd > melhor.odd)) melhor = { odd, casa };
  }
  return melhor;
}

// Prompt de OCR pra mercados de JOGADOR (chutes no alvo + marcador de gol) --
// deliberadamente separado de OCR_ODDS_PROMPT em AnaliseEvento.jsx, que é
// só de mercados de TIME e explicitamente ignora mercado de jogador.
const OCR_ODDS_JOGADOR_PROMPT = `Você é um extrator de odds de mercados de JOGADOR (chutes ao gol / marcador de gol) de screenshots de casas de apostas (Betano, Bet365, etc.) de uma partida de futebol.
A imagem mostra mercados de jogador individual -- "Chutes no alvo" (mais/menos de X por jogador) e/ou "Marcador de gol" (a qualquer momento / 2 ou mais / hat-trick). Extraia TODOS os jogadores visíveis e responda APENAS com um JSON válido, sem markdown, sem explicações, exatamente neste formato:
{
  "casa_de_apostas": "NomeDaCasa",
  "jogadores": [
    {
      "nome": "Nome do jogador exatamente como aparece na imagem",
      "chutes_no_alvo_mais_0_5": null,
      "chutes_no_alvo_mais_1_5": null,
      "chutes_no_alvo_mais_2_5": null,
      "marcar_1_mais": null,
      "marcar_2_mais": null,
      "marcar_3_mais": null
    }
  ]
}

Regras:
- "chutes_no_alvo_mais_X": odd de "mais de X chutes no alvo" (ignore a odd de "menos de"/"under"). Só preencha as linhas realmente visíveis, deixe null as que não aparecerem pra aquele jogador.
- "marcar_1_mais": odd de "marca a qualquer momento" / "marcador de gol" / "to score" (1 ou mais gols).
- "marcar_2_mais": odd de "marca 2 ou mais gols" / "dobradinha" / "brace".
- "marcar_3_mais": odd de "hat-trick" / "marca 3 ou mais gols".
- Se um mercado não estiver visível pra um jogador, deixe null -- não invente valor.
- Odds são números decimais como 1.87, 3.30, 5.25.
- Escreva o nome do jogador exatamente como aparece na imagem (não traduza, não abrevie, não corrija grafia).`;

// Prompt separado (não reusa OCR_ODDS_JOGADOR_PROMPT) porque bookmakers
// normalmente mostram "Total de chutes" (qualquer chute, não só no alvo) numa
// página de mercado própria, separada de "Chutes no alvo"/"Marcador de gol".
const OCR_ODDS_CHUTES_PROMPT = `Você é um extrator de odds do mercado de TOTAL DE CHUTES por jogador (qualquer chute, não só no alvo) de screenshots de casas de apostas (Betano, Bet365, etc.) de uma partida de futebol.
A imagem mostra o mercado "Total de chutes" (mais/menos de X chutes) por jogador -- pode ter várias linhas por jogador (mais de 0.5, 1.5, 2.5, ..., até 9.5 ou mais). Extraia TODOS os jogadores e linhas visíveis e responda APENAS com um JSON válido, sem markdown, sem explicações, exatamente neste formato:
{
  "casa_de_apostas": "NomeDaCasa",
  "jogadores": [
    {
      "nome": "Nome do jogador exatamente como aparece na imagem",
      "chutes_mais_0_5": null,
      "chutes_mais_1_5": null,
      "chutes_mais_2_5": null,
      "chutes_mais_3_5": null,
      "chutes_mais_4_5": null,
      "chutes_mais_5_5": null,
      "chutes_mais_6_5": null,
      "chutes_mais_7_5": null,
      "chutes_mais_8_5": null,
      "chutes_mais_9_5": null
    }
  ]
}

Regras:
- "chutes_mais_X": odd de "mais de X chutes" TOTAL (qualquer chute, no alvo ou não -- ignore a odd de "menos de"/"under"). Só preencha as linhas realmente visíveis, deixe null as que não aparecerem pra aquele jogador.
- Se um jogador não tiver nenhuma linha visível, não inclua ele no array.
- Odds são números decimais como 1.87, 3.30, 5.25.
- Escreva o nome do jogador exatamente como aparece na imagem (não traduza, não abrevie, não corrija grafia).`;

// oddsImportadas[player_id][campo] guarda um MAPA por casa de apostas
// ({ casa: odd }), não um valor único -- é o que permite importar a mesma
// partida várias vezes (OCR de casas diferentes, ou JSON colado) sem uma
// sobrescrever a outra; `melhorOddDoCampo` escolhe a melhor entre elas na
// hora de renderizar. Funde nível a nível (jogador -> campo -> casa) --
// nunca substitui o objeto do jogador nem o do campo inteiro, senão
// importar uma imagem/fonte apagaria as casas que outra já tinha
// importado pro mesmo jogador/linha. Também nunca escreve `null` por cima
// de uma casa já importada -- uma imagem que não mostra uma linha não deve
// apagar a leitura anterior dessa linha/casa noutra imagem.
function mesclarOddsImportadas(atual, novos) {
  const fundido = { ...atual };
  for (const [playerId, campos] of Object.entries(novos)) {
    const existente = fundido[playerId] || {};
    const mesclado = { ...existente };
    for (const [campo, porCasa] of Object.entries(campos)) {
      const limpas = Object.fromEntries(Object.entries(porCasa).filter(([, v]) => v != null));
      mesclado[campo] = { ...(existente[campo] || {}), ...limpas };
    }
    fundido[playerId] = mesclado;
  }
  return fundido;
}

function normalizarNomeJogador(s) {
  return String(s || '')
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/[^a-z0-9 ]/g, '').trim();
}

// Casa o nome livre extraído pela OCR com um jogador de `estimativas` --
// exato primeiro; senão por substring nos dois sentidos (bookmaker às vezes
// mostra só o sobrenome, ou só o primeiro nome). Mais de um candidato por
// substring é ambiguidade real (dois jogadores com sobrenome parecido) --
// fica sem match, é preferível a arriscar atribuir a odd ao jogador errado.
function casarJogadorPorNome(nomeImportado, jogadores) {
  const alvo = normalizarNomeJogador(nomeImportado);
  if (!alvo) return null;
  const exato = jogadores.find((j) => normalizarNomeJogador(j.players?.name) === alvo);
  if (exato) return exato;
  const candidatos = jogadores.filter((j) => {
    const nome = normalizarNomeJogador(j.players?.name);
    return nome && (nome.includes(alvo) || alvo.includes(nome));
  });
  return candidatos.length === 1 ? candidatos[0] : null;
}

// Odds justa (principal) + MELHOR odds real importada entre as casas
// disponíveis pra essa linha (secundária, menor, embaixo, com o nome da
// casa -- ver melhorOddDoCampo) -- mesmo padrão visual de
// CelulaComHistorico (valor principal + linha auxiliar), aqui colorindo
// verde/vermelho conforme a odd real do mercado está acima (valor pro
// apostador) ou abaixo (sem valor) da odds justa do modelo.
function CelulaOdds({ fair, real, casa }) {
  // `fair == null` (λ ausente pra esse jogador/mercado) precisa ficar neutro
  // -- sem essa guarda, `real >= null` coage null pra 0 e qualquer odd real
  // positiva compararia como "acima da justa" (verde), o que é falso.
  const cor = real == null || fair == null ? 'text-slate-300' : real >= fair ? 'text-emerald-400' : 'text-red-400';
  return (
    <td className="py-1.5 px-2 text-right font-mono">
      <div className={cor}>{fmtOdds(fair)}</div>
      {real != null && (
        <div className="text-[9px] text-slate-500 font-normal whitespace-nowrap">
          real: {fmtOdds(real)}{casa ? ` (${casa})` : ''}
        </div>
      )}
    </td>
  );
}

const N_PADRAO_ODDS_JUSTAS_JOGADOR = 4;

// Config de mercados pra TabelaOddsJustasIndividual -- cada mercado sabe seu
// próprio título, de qual λ deriva (via probPeloMenos), quantas linhas
// mostrar e como mapear uma linha pra chave de `oddsImportadas` (os nomes
// de campo vêm dos prompts de OCR, ver OCR_ODDS_JOGADOR_PROMPT/
// OCR_ODDS_CHUTES_PROMPT -- não seguem uma fórmula única entre mercados,
// "chutes ao gol"/"chutes" usam a convenção "mais de X.5", "gols" usa
// "marcar N ou mais").
const MERCADOS_CHUTES_GOLS = [
  { titulo: 'Chutes ao gol', lambdaKey: 'lambda_chutes_no_alvo_jogo', linhas: [1, 2, 3], chaveReal: (linha) => `chutes_no_alvo_mais_${linha - 1}_5` },
  { titulo: 'Gols', lambdaKey: 'lambda_gols_jogo_direto', linhas: [1, 2, 3], chaveReal: (linha) => `marcar_${linha}_mais` },
];

// Chutes totais (não só no alvo) -- linhas +1 até +10, pedido explícito do
// usuário ("é extensa, por tomar uma tabela à parte"): fica em Secao/tabela
// separada da de chutes-ao-gol/gols, mesmo componente generalizado.
const LINHAS_CHUTES_TOTAIS = Array.from({ length: 10 }, (_, i) => i + 1);
const MERCADOS_CHUTES_TOTAIS = [
  { titulo: 'Chutes (total)', lambdaKey: 'lambda_chutes_jogo', linhas: LINHAS_CHUTES_TOTAIS, chaveReal: (linha) => `chutes_mais_${linha - 1}_5` },
];

// Todos os mercados de jogador com odds justas derivadas de um λ (chutes ao
// gol, gols, chutes total) -- reusado tanto pelo import de JSON colado
// (mapeia o "mercado" do JSON pro config certo) quanto pela comparação de
// EV multi-casas (itera os 3 juntos numa lista só).
const MERCADOS_EV_JOGADOR = [...MERCADOS_CHUTES_GOLS, ...MERCADOS_CHUTES_TOTAIS];

// Mapa "mercado" (string livre, como vem no JSON colado pelo usuário) ->
// config já usado pelas tabelas de odds justas -- normaliza antes de
// comparar (case/espaços) pra não exigir que o usuário digite exatamente
// igual ao título mostrado na UI.
const MAPA_MERCADO_JSON = {
  chutes: MERCADOS_CHUTES_TOTAIS[0],
  'chutes total': MERCADOS_CHUTES_TOTAIS[0],
  'chutes (total)': MERCADOS_CHUTES_TOTAIS[0],
  'chutes ao gol': MERCADOS_CHUTES_GOLS[0],
  gols: MERCADOS_CHUTES_GOLS[1],
};
function normalizarMercadoJson(s) {
  return String(s || '').toLowerCase().trim().replace(/\s+/g, ' ');
}

// Importa odds coladas em JSON estruturado -- caminho alternativo ao OCR de
// imagem, pro formato { mercado, partida, equipes: { nomeTime: [{ jogador,
// odds: { "N+": odd } }] } } (pedido do usuário). Função pura (testável
// isolada) -- não precisa casar o nome do time (só usado como agrupamento
// de origem no JSON), casa cada jogador direto contra a lista completa de
// `jogadores` (os dois times), mesmo critério de casarJogadorPorNome.
function parseOddsJson(jsonTexto, casa, jogadores) {
  let payload;
  try {
    payload = JSON.parse(jsonTexto);
  } catch {
    return { erro: 'JSON inválido — confira a formatação.' };
  }
  if (!casa?.trim()) {
    return { erro: 'Informe a casa de apostas antes de importar.' };
  }
  const config = MAPA_MERCADO_JSON[normalizarMercadoJson(payload?.mercado)];
  if (!config) {
    const aceitos = [...new Set(Object.values(MAPA_MERCADO_JSON).map((c) => c.titulo))].join(', ');
    return { erro: `Mercado "${payload?.mercado ?? ''}" não reconhecido. Use um de: ${aceitos}.` };
  }
  const equipes = payload?.equipes || {};
  const jogadoresJson = Object.values(equipes).flat().filter(Boolean);
  const novos = {};
  let naoCasados = 0;
  for (const j of jogadoresJson) {
    const match = casarJogadorPorNome(j.jogador, jogadores);
    if (!match) { naoCasados += 1; continue; }
    const campos = {};
    for (const [linhaTexto, odd] of Object.entries(j.odds || {})) {
      const linha = parseInt(linhaTexto, 10);
      if (!Number.isFinite(linha) || !config.linhas.includes(linha) || odd == null) continue;
      campos[config.chaveReal(linha)] = { [casa]: odd };
    }
    if (Object.keys(campos).length > 0) novos[match.player_id] = campos;
  }
  const nCasados = Object.keys(novos).length;
  if (nCasados === 0 && naoCasados === 0) {
    return { erro: 'Nenhum jogador com odds encontrado nesse JSON.' };
  }
  return {
    novos,
    mensagem: `${nCasados} jogador(es) importado(s)${naoCasados ? `, ${naoCasados} não reconhecido(s) (nome não bateu com o elenco)` : ''}.`,
  };
}

// Colunas de export CSV da comparação de EV multi-casas -- não depende de
// closure nenhuma (linhas já vêm com todos os campos prontos), fica em
// escopo de módulo como as outras listas de colunas de export do arquivo.
const COLUNAS_EXPORT_EV_JOGADOR = [
  { header: 'Jogador', get: (l) => l.jogador },
  { header: 'Time', get: (l) => l.time },
  { header: 'Mercado', get: (l) => l.mercado },
  { header: 'Linha', get: (l) => `+${l.linha}` },
  { header: 'Casa de apostas', get: (l) => l.casa },
  { header: 'Odd real', get: (l) => numCSV(l.oddReal, 2) },
  { header: 'Odd justa (modelo)', get: (l) => numCSV(l.oddJusta, 2) },
  { header: 'Prob. modelo', get: (l) => numCSV(l.pModelo, 4) },
  { header: 'Edge (pp)', get: (l) => numCSV(l.edge * 100, 2) },
  { header: 'EV (%)', get: (l) => numCSV(l.ev * 100, 2) },
];

// Tabela separada da principal (pedido explícito do usuário) -- só odds
// justas (e a real importada quando existir) pros mercados passados em
// `mercados`, sem os λ/históricos que já estão na tabela principal. Top 4
// por time por padrão (ranqueado por `chaveOrdenacao`), com botão pra
// expandir e ver o elenco inteiro. Generalizado (não hardcoded pra 2
// mercados de 3 linhas) pra reusar entre "chutes ao gol/gols" (+1/+2/+3) e
// "chutes total" (+1..+10), sem duplicar a lógica de ranking/expandir/render.
function TabelaOddsJustasIndividual({ titulo, linhas, oddsImportadas, mercados, chaveOrdenacao = 'lambda_gols_jogo_direto' }) {
  const [expandido, setExpandido] = useState(false);
  if (linhas.length === 0) return null;

  const ordenados = [...linhas].sort((a, b) => (b[chaveOrdenacao] ?? -1) - (a[chaveOrdenacao] ?? -1));
  const visiveis = expandido ? ordenados : ordenados.slice(0, N_PADRAO_ODDS_JUSTAS_JOGADOR);

  return (
    <div className="mb-4 last:mb-0">
      <div className="flex items-center justify-between mb-2">
        <p className="text-[10px] text-slate-500 uppercase tracking-wider">{titulo}</p>
        {ordenados.length > N_PADRAO_ODDS_JUSTAS_JOGADOR && (
          <button
            type="button"
            onClick={() => setExpandido((v) => !v)}
            className="text-[10px] text-emerald-400 hover:text-emerald-300 font-bold"
          >
            {expandido ? 'Mostrar menos' : `Ver todos (${ordenados.length})`}
          </button>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-slate-500 text-left">
              <th className="py-1 px-2 font-normal text-left" rowSpan={2}>Jogador</th>
              <th className="py-1 px-2 font-normal text-left" rowSpan={2}>Pos.</th>
              {mercados.map((m) => (
                <th key={m.titulo} colSpan={m.linhas.length} className="py-1 px-2 font-normal text-center border-l border-slate-800">{m.titulo}</th>
              ))}
            </tr>
            <tr className="text-slate-600 text-[10px]">
              {mercados.map((m) => m.linhas.map((linha, i) => (
                <th key={`${m.titulo}-${linha}`} className={`py-0.5 px-2 text-right ${i === 0 ? 'border-l border-slate-800' : ''}`}>+{linha}</th>
              )))}
            </tr>
          </thead>
          <tbody>
            {visiveis.map((l) => {
              const posicao = POSICAO_FINA_CURTA[l.posicao_detalhe] || POSICAO_CURTA[l.players?.usual_position_id] || '—';
              const importado = oddsImportadas[l.player_id] || {};
              return (
                <tr key={l.player_id} className="border-t border-slate-800">
                  <td className="py-1.5 pr-2 text-slate-200 font-semibold whitespace-nowrap">{l.players?.name || `Jogador #${l.player_id}`}</td>
                  <td className="py-1.5 px-2 text-slate-400 font-mono text-[10px]">{posicao}</td>
                  {mercados.map((m) => m.linhas.map((linha) => {
                    const melhor = melhorOddDoCampo(importado[m.chaveReal(linha)]);
                    return (
                      <CelulaOdds
                        key={`${m.titulo}-${linha}`}
                        fair={oddsJusta(probPeloMenos(l[m.lambdaKey], linha))}
                        real={melhor?.odd}
                        casa={melhor?.casa}
                      />
                    );
                  }))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const ROTULO_FONTE_TITULAR = {
  real: { texto: 'Escalação real', descricao: 'Titularidade oficial confirmada perto do apito (match_lineup_fotmob) — minutos esperados determinísticos por papel.' },
  previsto: { texto: 'XI previsto', descricao: 'Titularidade estimada dias/horas antes (xi_previsto) — minutos esperados misturam prob. de titularidade, carrega mais incerteza.' },
};

// Posição grossa (players.usual_position_id: 0=goleiro/1=defesa/2=meio/
//3=ataque) -- mesmo bucket já usado em AnaliseEstatisticaJogo.jsx
// (POSICAO_LABEL). Não temos posição fina (ZAG/VOL/LD/LE/etc.) de forma
// confiável -- exigiria decodificar match_lineup_fotmob.position_id (grade
// de formação do FotMob, não documentada), fora de escopo por ora.
const POSICAO_CURTA = { 0: 'GOL', 1: 'DEF', 2: 'MEI', 3: 'ATA' };

// Posição fina (código FotMob, fonte player_availability_fotmob.
// posicao_detalhe -- mesmo dado já exposto em AnaliseEstatisticaJogo.jsx
// pro XI previsto, PR #395) -- usada quando disponível (~80-85% do elenco,
// ver PR #395), cai pro bucket grosso (POSICAO_CURTA) senão. Limitação real
// da fonte: não distingue lado do zagueiro (só "CB" genérico).
const POSICAO_FINA_CURTA = {
  GK: 'GOL', CB: 'ZAG', RB: 'LD', LB: 'LE', RWB: 'AD', LWB: 'AE',
  CDM: 'VOL', CM: 'MC', CAM: 'MEIA', RM: 'MD', LM: 'ME', RW: 'PD', LW: 'PE', ST: 'CA',
};

// Limiar pra separar Titular/Banco -- pra fonte_titular='real',
// prob_titular_usada já vem exatamente 1.0/0.0 (is_starter confirmado, ver
// rodar_jogador_mercados_previsto.py); pra 'previsto', é uma probabilidade
// contínua de xi_previsto, então o corte em 0.5 é uma aproximação ("mais
// provável titular que reserva"), não uma confirmação.
const LIMIAR_TITULAR = 0.5;

// Config de colunas: cada uma sabe extrair seu próprio valor de ordenação
// (`valorSort`) -- evita o header de sort e a lógica de sort divergirem.
// Colunas numéricas ordenam desc por padrão (pedido do usuário: "maior ->
// menor"); a coluna Jogador (texto) ordena A→Z por padrão.
const COLUNAS_JOGADOR_MERCADOS = [
  { chave: 'jogador', rotulo: 'Jogador', tipo: 'texto', valorSort: (l) => l.players?.name || '' },
  { chave: 'posicao', rotulo: 'Pos.', tipo: 'texto', valorSort: (l) => POSICAO_FINA_CURTA[l.posicao_detalhe] || POSICAO_CURTA[l.players?.usual_position_id] || '' },
  { chave: 'minutos', rotulo: 'Min. esp.', tipo: 'numero', valorSort: (l) => l.minutos_esperados ?? -1 },
  { chave: 'chutes', rotulo: 'Chutes (λ)', tipo: 'numero', valorSort: (l) => l.lambda_chutes_jogo ?? -1 },
  { chave: 'p15chutes', rotulo: 'P(>1.5 chutes)', tipo: 'numero', valorSort: (l) => 1 - poissonCDF(l.lambda_chutes_jogo ?? 0, 1) },
  { chave: 'chutesnoalvo', rotulo: 'Chutes ao gol (λ)', tipo: 'numero', valorSort: (l) => l.lambda_chutes_no_alvo_jogo ?? -1 },
  { chave: 'pchutenoalvo', rotulo: 'P(≥1 no alvo)', tipo: 'numero', valorSort: (l) => probMarcar(l.lambda_chutes_no_alvo_jogo) ?? -1 },
  { chave: 'thinning', rotulo: 'Marcar (thinning)', tipo: 'numero', valorSort: (l) => probMarcar(l.lambda_gols_jogo_thinning) ?? -1 },
  { chave: 'direto', rotulo: 'Marcar (direto)', tipo: 'numero', valorSort: (l) => probMarcar(l.lambda_gols_jogo_direto) ?? -1 },
  { chave: 'xg', rotulo: 'xG esp.', tipo: 'numero', valorSort: (l) => l.lambda_xg_jogo ?? -1 },
];

// Chutes/gols/xG por jogador (player_match_estimates) -- guarda as duas
// previsões lado a lado quando existem (fonte_titular='previsto', gerada
// dias/horas antes usando o XI previsto, e 'real', gerada perto do apito
// assim que a escalação oficial é capturada -- ver
// scripts/rodar_jogador_mercados_previsto.py). Nenhuma sobrescreve a
// outra no banco -- aqui vira uma aba por fonte, não linhas duplicadas.
// Colunas de export ficam fora do componente (menos a 1a, "Time", que
// depende de qual time é mandante -- resolvida via closure dentro do
// componente, ver `colunasExportJogador` abaixo) pra não recriar toda a
// lista de getters a cada render.
const COLUNAS_EXPORT_JOGADOR_MERCADOS_BASE = [
  { header: 'Jogador', get: (l) => l.players?.name || `Jogador #${l.player_id}` },
  { header: 'Posição (fina)', get: (l) => POSICAO_FINA_CURTA[l.posicao_detalhe] || '' },
  { header: 'Posição (grossa)', get: (l) => POSICAO_CURTA[l.players?.usual_position_id] || '' },
  { header: 'Fonte', get: (l) => ROTULO_FONTE_TITULAR[l.fonte_titular]?.texto || l.fonte_titular || '' },
  { header: 'Papel', get: (l) => ((l.prob_titular_usada ?? 0) >= LIMIAR_TITULAR ? 'Titular' : 'Banco') },
  { header: 'Prob. titular usada', get: (l) => numCSV(l.prob_titular_usada) },
  { header: 'Min. esperados', get: (l) => numCSV(l.minutos_esperados, 1) },
  { header: 'Chutes (λ)', get: (l) => numCSV(l.lambda_chutes_jogo) },
  { header: 'Chutes/90 hist.', get: (l) => numCSV(l.chutes_90_bayesiano) },
  { header: 'Chutes/jogo hist.', get: (l) => numCSV(l.chutes_por_jogo) },
  { header: 'Chutes ao gol (λ)', get: (l) => numCSV(l.lambda_chutes_no_alvo_jogo) },
  { header: 'Chutes ao gol/90 hist.', get: (l) => numCSV(l.chutes_no_alvo_90_bayesiano) },
  { header: 'Chutes ao gol/jogo hist.', get: (l) => numCSV(l.chutes_no_alvo_por_jogo) },
  { header: 'Gols thinning (λ)', get: (l) => numCSV(l.lambda_gols_jogo_thinning) },
  { header: 'Gols direto (λ)', get: (l) => numCSV(l.lambda_gols_jogo_direto) },
  { header: 'Taxa conversão bayesiana', get: (l) => numCSV(l.taxa_conversao_bayesiana) },
  { header: 'Gols/90 hist.', get: (l) => numCSV(l.gols_90_bayesiano) },
  { header: 'Gols/jogo hist.', get: (l) => numCSV(l.gols_por_jogo) },
  { header: 'xG esp. (λ)', get: (l) => numCSV(l.lambda_xg_jogo) },
  { header: 'xG/90 hist.', get: (l) => numCSV(l.xg_90_bayesiano) },
  { header: 'xG/jogo hist.', get: (l) => numCSV(l.xg_por_jogo) },
];

function SecaoJogadorMercados({ estimativas, homeTeamId, homeNome, awayNome, matchDate }) {
  const fontesDisponiveis = useMemo(
    () => new Set((estimativas || []).map((e) => e.fonte_titular)),
    [estimativas]
  );
  // Prioriza 'real' quando existe (escalação já confirmada, menos incerteza
  // que 'previsto' -- ver ROTULO_FONTE_TITULAR.descricao).
  const [fonteSelecionada, setFonteSelecionada] = useState(null);
  const fonteAtiva = fonteSelecionada && fontesDisponiveis.has(fonteSelecionada)
    ? fonteSelecionada
    : (fontesDisponiveis.has('real') ? 'real' : 'previsto');

  // Ordenação clicável nos headers -- default Chutes(λ) desc (mesmo
  // critério que já era o default fixo antes desta mudança).
  const [sort, setSort] = useState({ chave: 'chutes', direcao: 'desc' });
  const colunaSort = COLUNAS_JOGADOR_MERCADOS.find((c) => c.chave === sort.chave) || COLUNAS_JOGADOR_MERCADOS[3];
  const alternarSort = (chave) => setSort((atual) => ({
    chave,
    direcao: atual.chave === chave && atual.direcao === 'desc' ? 'asc' : 'desc',
  }));

  // Odds reais de jogador importadas via OCR (local, não persistido -- mesmo
  // espírito de "sem persistir 1 linha por linha de aposta" já usado pros λ
  // derivados). Chave = player_id, casado por nome contra `estimativas` no
  // momento da importação (ver `casarJogadorPorNome`).
  const [oddsImportadas, setOddsImportadas] = useState({});
  const [ocrLoading, setOcrLoading] = useState(false);
  const [ocrMsg, setOcrMsg] = useState('');
  const [ocrErro, setOcrErro] = useState('');
  // Estado próprio pro import de chutes totais (botão/prompt separado) --
  // escreve no MESMO `oddsImportadas` (mesclarOddsImportadas evita colisão),
  // só o loading/mensagem de cada botão fica independente.
  const [ocrChutesLoading, setOcrChutesLoading] = useState(false);
  const [ocrChutesMsg, setOcrChutesMsg] = useState('');
  const [ocrChutesErro, setOcrChutesErro] = useState('');

  // Casa de apostas compartilhada entre os 3 caminhos de import (2 OCR + 1
  // JSON colado) -- pro OCR é só o fallback quando a imagem não mostra a
  // marca (o prompt já pede casa_de_apostas pra IA extrair, ver
  // OCR_ODDS_JOGADOR_PROMPT/OCR_ODDS_CHUTES_PROMPT); pro JSON colado é
  // obrigatório (o formato do exemplo do usuário não carrega esse campo).
  const [casaAtual, setCasaAtual] = useState('');
  const [pasteAberto, setPasteAberto] = useState(false);
  const [pasteTexto, setPasteTexto] = useState('');
  const [pasteMsg, setPasteMsg] = useState('');
  const [pasteErro, setPasteErro] = useState('');
  const [soEvPositivo, setSoEvPositivo] = useState(false);

  if (!estimativas?.length) return null;

  // Export cobre TODAS as linhas (as duas fontes, os dois times,
  // titular+banco) -- não só a aba/filtro ativo no momento -- pra deixar o
  // usuário fatiar livremente na planilha (pedido explícito: "avaliações
  // pessoais de cada jogo").
  const colunasExportJogador = [
    { header: 'Time', get: (l) => (l.team_id === homeTeamId ? homeNome : awayNome) || '' },
    ...COLUNAS_EXPORT_JOGADOR_MERCADOS_BASE,
  ];
  const nomeArquivoJogador = `chutes_gols_xg_${sanitizarNomeArquivo(homeNome)}_x_${sanitizarNomeArquivo(awayNome)}_${matchDate ? matchDate.slice(0, 10) : sanitizarNomeArquivo('')}.csv`;
  const exportarJogadorMercados = () => exportarCSV(estimativas, colunasExportJogador, nomeArquivoJogador);

  const linhasDaFonte = estimativas.filter((e) => e.fonte_titular === fonteAtiva);
  const porTime = {
    [homeTeamId]: linhasDaFonte.filter((e) => e.team_id === homeTeamId),
    outro: linhasDaFonte.filter((e) => e.team_id !== homeTeamId),
  };

  const linhasOrdenadas = (lista) => {
    const copia = [...lista];
    copia.sort((a, b) => {
      const va = colunaSort.valorSort(a);
      const vb = colunaSort.valorSort(b);
      const cmp = colunaSort.tipo === 'texto' ? String(va).localeCompare(String(vb)) : va - vb;
      return sort.direcao === 'desc' ? -cmp : cmp;
    });
    return copia;
  };

  // Célula com valor principal + histórico do próprio jogador em duas
  // visões (por 90min, com shrinkage bayesiano -- mesma feature usada como
  // entrada do modelo -- e por jogo, média crua sem normalizar por
  // minutos) numa linha menor abaixo -- deixa visível se a previsão está
  // alinhada com o que o jogador costuma fazer, sem inflar a tabela com
  // colunas extras pra cada estatística histórica.
  const CelulaComHistorico = ({ valor, historico90, sufixo90, historicoJogo, sufixoJogo, classe }) => (
    <td className={`py-1.5 px-2 text-right font-mono ${classe}`}>
      <div>{valor}</div>
      {(historico90 != null || historicoJogo != null) && (
        <div className="text-[9px] text-slate-500 font-normal whitespace-nowrap">
          {historico90 != null && <>{fmtNum(historico90, 2)}{sufixo90}</>}
          {historico90 != null && historicoJogo != null && ' · '}
          {historicoJogo != null && <>{fmtNum(historicoJogo, 2)}{sufixoJogo}</>}
        </div>
      )}
    </td>
  );

  const LinhaJogador = ({ l }) => {
    const posicao = POSICAO_FINA_CURTA[l.posicao_detalhe] || POSICAO_CURTA[l.players?.usual_position_id] || '—';
    return (
      <tr className="border-t border-slate-800">
        <td className="py-1.5 pr-2 text-slate-200 font-semibold whitespace-nowrap">{l.players?.name || `Jogador #${l.player_id}`}</td>
        <td className="py-1.5 px-2 text-slate-400 font-mono text-[10px]">{posicao}</td>
        <td className="py-1.5 px-2 text-right font-mono text-slate-300">{fmtNum(l.minutos_esperados, 0)}</td>
        <CelulaComHistorico
          classe="text-slate-300" valor={fmtNum(l.lambda_chutes_jogo, 2)}
          historico90={l.chutes_90_bayesiano} sufixo90="/90" historicoJogo={l.chutes_por_jogo} sufixoJogo="/jogo"
        />
        <td className="py-1.5 px-2 text-right font-mono text-emerald-400">{fmtPct(1 - poissonCDF(l.lambda_chutes_jogo ?? 0, 1))}</td>
        <CelulaComHistorico
          classe="text-sky-400" valor={fmtNum(l.lambda_chutes_no_alvo_jogo, 2)}
          historico90={l.chutes_no_alvo_90_bayesiano} sufixo90="/90" historicoJogo={l.chutes_no_alvo_por_jogo} sufixoJogo="/jogo"
        />
        <td className="py-1.5 px-2 text-right font-mono text-sky-400">{fmtPct(probMarcar(l.lambda_chutes_no_alvo_jogo))}</td>
        <CelulaComHistorico
          classe="text-emerald-400" valor={fmtPct(probMarcar(l.lambda_gols_jogo_thinning))}
          historico90={l.gols_90_bayesiano} sufixo90=" g/90" historicoJogo={l.gols_por_jogo} sufixoJogo=" g/jogo"
        />
        <td className="py-1.5 px-2 text-right font-mono text-slate-400">{fmtPct(probMarcar(l.lambda_gols_jogo_direto))}</td>
        <CelulaComHistorico
          classe="text-slate-300" valor={fmtNum(l.lambda_xg_jogo, 2)}
          historico90={l.xg_90_bayesiano} sufixo90="/90" historicoJogo={l.xg_por_jogo} sufixoJogo="/jogo"
        />
      </tr>
    );
  };

  const CabecalhoColunas = () => (
    <tr className="text-slate-500 text-left">
      {COLUNAS_JOGADOR_MERCADOS.map((col) => (
        <th
          key={col.chave}
          onClick={() => alternarSort(col.chave)}
          title="Clique pra ordenar"
          className={`py-1 px-2 font-normal cursor-pointer select-none hover:text-slate-300 whitespace-nowrap ${col.tipo === 'numero' ? 'text-right' : 'text-left'}`}
        >
          {col.rotulo}{sort.chave === col.chave && (sort.direcao === 'desc' ? ' ▼' : ' ▲')}
        </th>
      ))}
    </tr>
  );

  const Tabela = ({ titulo, linhas }) => {
    const titulares = linhasOrdenadas(linhas.filter((l) => (l.prob_titular_usada ?? 0) >= LIMIAR_TITULAR));
    const banco = linhasOrdenadas(linhas.filter((l) => (l.prob_titular_usada ?? 0) < LIMIAR_TITULAR));
    return (
      <div className="mb-4 last:mb-0">
        <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-2">{titulo}</p>
        {linhas.length === 0 ? (
          <p className="text-[11px] text-slate-600 italic">Sem previsão para esta fonte.</p>
        ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><CabecalhoColunas /></thead>
            {titulares.length > 0 && (
              <tbody>
                <tr><td colSpan={COLUNAS_JOGADOR_MERCADOS.length} className="pt-2 pb-1 text-[10px] text-emerald-500 font-bold uppercase tracking-wider">Titular</td></tr>
                {titulares.map((l) => <LinhaJogador key={l.player_id} l={l} />)}
              </tbody>
            )}
            {banco.length > 0 && (
              <tbody>
                <tr><td colSpan={COLUNAS_JOGADOR_MERCADOS.length} className="pt-2 pb-1 text-[10px] text-slate-500 font-bold uppercase tracking-wider">Banco</td></tr>
                {banco.map((l) => <LinhaJogador key={l.player_id} l={l} />)}
              </tbody>
            )}
          </table>
        </div>
        )}
      </div>
    );
  };

  // Lê a imagem, extrai jogadores+odds via OCR, casa cada um por nome contra
  // `estimativas` (as duas fontes/times, então funciona pra qualquer
  // screenshot) e funde no estado local -- não substitui importações
  // anteriores de outros jogadores, só atualiza quem apareceu nesta imagem.
  const handleOcrOddsJogador = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setOcrLoading(true); setOcrErro(''); setOcrMsg('');
    try {
      const parsed = await extractJsonFromImage(file, OCR_ODDS_JOGADOR_PROMPT);
      const casa = (parsed?.casa_de_apostas || casaAtual || 'Casa (OCR)').trim() || 'Casa (OCR)';
      const jogadoresImagem = parsed?.jogadores || [];
      const novos = {};
      let naoCasados = 0;
      for (const j of jogadoresImagem) {
        const match = casarJogadorPorNome(j.nome, estimativas);
        if (!match) { naoCasados += 1; continue; }
        const camposValor = {
          chutes_no_alvo_mais_0_5: j.chutes_no_alvo_mais_0_5 ?? null,
          chutes_no_alvo_mais_1_5: j.chutes_no_alvo_mais_1_5 ?? null,
          chutes_no_alvo_mais_2_5: j.chutes_no_alvo_mais_2_5 ?? null,
          marcar_1_mais: j.marcar_1_mais ?? null,
          marcar_2_mais: j.marcar_2_mais ?? null,
          marcar_3_mais: j.marcar_3_mais ?? null,
        };
        novos[match.player_id] = Object.fromEntries(
          Object.entries(camposValor).map(([campo, valor]) => [campo, { [casa]: valor }])
        );
      }
      setOddsImportadas((atual) => mesclarOddsImportadas(atual, novos));
      const nCasados = Object.keys(novos).length;
      if (nCasados === 0 && naoCasados === 0) {
        setOcrErro('Nenhum jogador com odds reconhecido nessa imagem.');
      } else {
        setOcrMsg(`${nCasados} jogador(es) importado(s)${naoCasados ? `, ${naoCasados} não reconhecido(s) (nome não bateu com o elenco)` : ''}.`);
      }
    } catch (err) {
      setOcrErro(err.message || 'Falha ao ler a imagem.');
    } finally {
      setOcrLoading(false);
    }
  };

  // Mesmo padrão de handleOcrOddsJogador, prompt/estado próprios (mercado
  // separado: chutes TOTAIS, não só no alvo) -- funde no mesmo
  // `oddsImportadas` via mesclarOddsImportadas, sem apagar o que a outra
  // fonte já importou pro mesmo jogador.
  const handleOcrOddsChutes = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setOcrChutesLoading(true); setOcrChutesErro(''); setOcrChutesMsg('');
    try {
      const parsed = await extractJsonFromImage(file, OCR_ODDS_CHUTES_PROMPT);
      const casa = (parsed?.casa_de_apostas || casaAtual || 'Casa (OCR)').trim() || 'Casa (OCR)';
      const jogadoresImagem = parsed?.jogadores || [];
      const novos = {};
      let naoCasados = 0;
      for (const j of jogadoresImagem) {
        const match = casarJogadorPorNome(j.nome, estimativas);
        if (!match) { naoCasados += 1; continue; }
        const campos = {};
        for (const linha of LINHAS_CHUTES_TOTAIS) {
          const chave = `chutes_mais_${linha - 1}_5`;
          campos[chave] = { [casa]: j[chave] ?? null };
        }
        novos[match.player_id] = campos;
      }
      setOddsImportadas((atual) => mesclarOddsImportadas(atual, novos));
      const nCasados = Object.keys(novos).length;
      if (nCasados === 0 && naoCasados === 0) {
        setOcrChutesErro('Nenhum jogador com odds reconhecido nessa imagem.');
      } else {
        setOcrChutesMsg(`${nCasados} jogador(es) importado(s)${naoCasados ? `, ${naoCasados} não reconhecido(s) (nome não bateu com o elenco)` : ''}.`);
      }
    } catch (err) {
      setOcrChutesErro(err.message || 'Falha ao ler a imagem.');
    } finally {
      setOcrChutesLoading(false);
    }
  };

  // Import por JSON colado -- síncrono (parseOddsJson é função pura, sem
  // I/O), mesmo destino (oddsImportadas) e mesma UX de sucesso/erro dos
  // handlers de OCR acima.
  const handleImportarJson = () => {
    setPasteErro(''); setPasteMsg('');
    const resultado = parseOddsJson(pasteTexto, casaAtual, estimativas);
    if (resultado.erro) { setPasteErro(resultado.erro); return; }
    setOddsImportadas((atual) => mesclarOddsImportadas(atual, resultado.novos));
    setPasteMsg(resultado.mensagem);
    setPasteTexto('');
  };

  // Comparação de EV multi-casas: uma linha por (jogador, mercado, linha,
  // casa) que tem odd importada -- diferente das 3 tabelas acima (que só
  // mostram a MELHOR odd por célula), aqui cada casa vira sua própria linha
  // pra comparação/ranking, mesmo espírito da verificação de EV de TIME
  // (verificacaoEV, no componente pai) mas sem devig: mercados de jogador
  // hoje só trazem o lado "N+" (nunca o complementar "menos de"), então não
  // dá pra devigar por Odds Ratio como no nível de time (exige as duas
  // pernas do mercado) -- edge aqui é probabilidade implícita simples
  // (1/odd), com a margem da casa embutida, não a probabilidade "limpa".
  const comparacaoEVLinhas = [];
  for (const l of linhasDaFonte) {
    const nomeTime = l.team_id === homeTeamId ? homeNome : awayNome;
    const importado = oddsImportadas[l.player_id];
    if (!importado) continue;
    for (const mercado of MERCADOS_EV_JOGADOR) {
      for (const linha of mercado.linhas) {
        const pModelo = probPeloMenos(l[mercado.lambdaKey], linha);
        if (pModelo == null) continue;
        const porCasa = importado[mercado.chaveReal(linha)];
        if (!porCasa) continue;
        for (const [casa, oddReal] of Object.entries(porCasa)) {
          if (oddReal == null) continue;
          comparacaoEVLinhas.push({
            jogador: l.players?.name || `Jogador #${l.player_id}`,
            time: nomeTime || '',
            mercado: mercado.titulo,
            linha,
            casa,
            oddReal,
            oddJusta: oddsJusta(pModelo),
            pModelo,
            edge: pModelo - 1 / oddReal,
            ev: pModelo * oddReal - 1,
          });
        }
      }
    }
  }
  comparacaoEVLinhas.sort((a, b) => b.edge - a.edge);
  const comparacaoEVVisivel = soEvPositivo ? comparacaoEVLinhas.filter((l) => l.edge > 0) : comparacaoEVLinhas;
  const nomeArquivoEVJogador = `ev_multicasas_jogador_${sanitizarNomeArquivo(homeNome)}_x_${sanitizarNomeArquivo(awayNome)}_${matchDate ? matchDate.slice(0, 10) : sanitizarNomeArquivo('')}.csv`;
  const exportarComparacaoEV = () => exportarCSV(comparacaoEVVisivel, COLUNAS_EXPORT_EV_JOGADOR, nomeArquivoEVJogador);

  return (
    <>
    <Secao titulo="Chutes & gols por jogador" icone={Target}>
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          {['real', 'previsto'].map((fonte) => {
            const disponivel = fontesDisponiveis.has(fonte);
            const ativo = fonte === fonteAtiva;
            return (
              <button
                key={fonte}
                type="button"
                disabled={!disponivel}
                onClick={() => setFonteSelecionada(fonte)}
                title={ROTULO_FONTE_TITULAR[fonte].descricao}
                className={`px-2 py-1 rounded text-[11px] font-bold transition-colors ${
                  ativo
                    ? 'bg-emerald-500/20 text-emerald-400'
                    : disponivel
                      ? 'bg-slate-800 text-slate-400 hover:text-slate-200'
                      : 'bg-slate-900 text-slate-700 cursor-not-allowed'
                }`}
              >
                {ROTULO_FONTE_TITULAR[fonte].texto}
              </button>
            );
          })}
        </div>
        <BotaoExportarCSV onClick={exportarJogadorMercados} disabled={estimativas.length === 0} />
      </div>
      <p className="text-[11px] text-slate-500 mb-3">
        λ de Poisson por jogador (<code className="text-slate-400">player_match_estimates</code>). "Marcar (thinning)" deriva do λ de chutes
        × taxa de conversão do próprio jogador; "Marcar (direto)" é um regressor treinado direto no alvo gols — as duas ficam lado a lado
        de propósito, sem vencedor fixo. "xG esp." é o xG esperado do jogador na partida (regressor CatBoost RMSE, não vira probabilidade
        derivada). Abaixo de cada λ: histórico do próprio jogador por 90min (com shrinkage bayesiano, mesma feature de entrada do modelo)
        e por jogo (média crua, sem normalizar por minutos). "Pos." usa a posição fina (lateral/volante/ponta/etc.) quando disponível,
        senão o bucket grosso (GOL/DEF/MEI/ATA). Clique num cabeçalho de coluna pra ordenar; "Titular"/"Banco" usa a titularidade
        confirmada (aba "Escalação real") ou a mais provável (aba "XI previsto", corte em 50%).
      </p>
      <p className="text-[11px] text-slate-500 mb-3">
        <strong className="text-slate-400">Tradução chutes → chutes ao gol → gols:</strong> "Chutes (λ)" conta{' '}
        <em>toda</em> finalização (fora, bloqueada, na trave, no alvo). "Chutes ao gol (λ)" é o subconjunto que segue em direção ao gol e
        termina em gol <em>ou</em> defesa do goleiro (exclui chute bloqueado por um defensor antes de chegar lá — não conta como "chute
        ao gol" porque não é o goleiro quem impede) — é o número que responde "qual a chance dele finalizar no alvo até o fim do jogo"
        (via "P(≥1 no alvo)", mesma lógica de "P(&gt;1.5 chutes)"). "Marcar" é o subconjunto final que vira gol de fato. Cada etapa é um
        afinamento de Poisson sobre a anterior (λ_chutes × taxa histórica do jogador naquela etapa) — não são 3 modelos treinados
        separados, é o mesmo λ de chutes "filtrado" estatisticamente.
      </p>
      <Tabela titulo={homeNome || 'Mandante'} linhas={porTime[homeTeamId] || []} />
      <Tabela titulo={awayNome || 'Visitante'} linhas={porTime.outro || []} />
    </Secao>

    <Secao titulo="Importar odds de casas de apostas" icone={Camera}>
      <p className="text-[11px] text-slate-500 mb-3">
        Casa de apostas usada como rótulo nas odds importadas abaixo (pelo OCR nas 2 seções seguintes ou pelo JSON colado aqui) — permite
        importar a mesma partida várias vezes, uma por casa, sem uma apagar a outra. As 3 tabelas de odds justas mostram sempre a{' '}
        <em>melhor</em> odd entre as casas importadas pra cada célula; a seção "Comparação de EV multi-casas" no fim lista cada casa
        separadamente, ordenada por edge. O OCR detecta a casa automaticamente quando o print mostra a marca; este campo é o
        repique/fallback pra quando não detecta, e é obrigatório pro JSON colado (o formato de import não carrega esse dado).
      </p>
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <input
          type="text"
          value={casaAtual}
          onChange={(e) => setCasaAtual(e.target.value)}
          placeholder="Casa de apostas (Bet365, Betano, Pinnacle...)"
          className="px-2.5 py-1.5 rounded-lg bg-slate-900 border border-slate-700 text-[11px] text-slate-200 placeholder:text-slate-600 w-72"
        />
        <button
          type="button"
          onClick={() => setPasteAberto((v) => !v)}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-bold bg-slate-700 hover:bg-slate-600 text-slate-200 transition-colors"
        >
          {pasteAberto ? 'Fechar' : 'Colar odds (JSON)'}
        </button>
      </div>
      {pasteAberto && (
        <div className="mb-3 p-3 rounded-lg bg-slate-900 border border-slate-700">
          <textarea
            value={pasteTexto}
            onChange={(e) => setPasteTexto(e.target.value)}
            placeholder='{"mercado": "Chutes", "partida": "...", "equipes": {"Time A": [{"jogador": "...", "odds": {"3+": 1.5}}]}}'
            rows={6}
            className="w-full px-2.5 py-2 rounded-lg bg-slate-950 border border-slate-800 text-[11px] font-mono text-slate-300 placeholder:text-slate-700"
          />
          <div className="flex items-center justify-between mt-2">
            <p className="text-[10px] text-slate-600">Mercados aceitos: Chutes, Chutes ao gol, Gols.</p>
            <button
              type="button"
              onClick={handleImportarJson}
              disabled={!pasteTexto.trim()}
              className="px-3 py-1.5 rounded-lg text-[11px] font-bold bg-purple-600 hover:bg-purple-500 disabled:opacity-40 disabled:cursor-not-allowed text-white transition-colors"
            >
              Importar
            </button>
          </div>
        </div>
      )}
      {(pasteErro || pasteMsg) && (
        <div className={`px-3 py-2 rounded-lg border text-[11px] flex items-start gap-2 ${
          pasteErro ? 'bg-red-950/30 border-red-500/40 text-red-400' : 'bg-emerald-950/30 border-emerald-500/40 text-emerald-400'
        }`}>
          {pasteErro ? <X size={13} className="mt-0.5 shrink-0" /> : <Check size={13} className="mt-0.5 shrink-0" />}
          <span>{pasteErro || pasteMsg}</span>
        </div>
      )}
    </Secao>

    <Secao titulo="Odds justas individuais (chutes ao gol / gols)" icone={Percent}>
      <div className="flex items-center justify-between gap-2 mb-3">
        <p className="text-[11px] text-slate-500">
          Odds justas (1/probabilidade, sem margem) nas linhas +1/+2/+3 de cada mercado, derivadas do mesmo λ já mostrado acima
          ("Chutes ao gol" via <code className="text-slate-400">lambda_chutes_no_alvo_jogo</code>; "Gols" via{' '}
          <code className="text-slate-400">lambda_gols_jogo_direto</code>, a variante com melhor log-loss no backtest real).
          Importe uma imagem de odds reais da casa (mercado "Chutes no alvo"/"Marcador de gol") pra comparar lado a lado — mostra a melhor
          odd entre as casas já importadas, verde quando está acima da justa (valor pro apostador), vermelho quando abaixo.
        </p>
        <label
          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-bold cursor-pointer transition-colors shrink-0 ${
            ocrLoading ? 'bg-slate-700 text-slate-400 cursor-wait' : 'bg-purple-600 hover:bg-purple-500 text-white'
          }`}
        >
          {ocrLoading ? <Loader2 size={14} className="animate-spin" /> : <Camera size={14} />}
          Importar odds (OCR)
          <input type="file" accept="image/*" capture="environment" className="hidden" disabled={ocrLoading} onChange={handleOcrOddsJogador} />
        </label>
      </div>
      {(ocrErro || ocrMsg) && (
        <div className={`mb-3 px-3 py-2 rounded-lg border text-[11px] flex items-start gap-2 ${
          ocrErro ? 'bg-red-950/30 border-red-500/40 text-red-400' : 'bg-emerald-950/30 border-emerald-500/40 text-emerald-400'
        }`}>
          {ocrErro ? <X size={13} className="mt-0.5 shrink-0" /> : <Check size={13} className="mt-0.5 shrink-0" />}
          <span>{ocrErro || ocrMsg}</span>
        </div>
      )}
      <TabelaOddsJustasIndividual
        titulo={homeNome || 'Mandante'} linhas={porTime[homeTeamId] || []} oddsImportadas={oddsImportadas} mercados={MERCADOS_CHUTES_GOLS}
      />
      <TabelaOddsJustasIndividual
        titulo={awayNome || 'Visitante'} linhas={porTime.outro || []} oddsImportadas={oddsImportadas} mercados={MERCADOS_CHUTES_GOLS}
      />
    </Secao>

    <Secao titulo="Odds justas — chutes (total)" icone={Target}>
      <div className="flex items-center justify-between gap-2 mb-3">
        <p className="text-[11px] text-slate-500">
          Mesma lógica da tabela acima, agora pro total de chutes do jogador (qualquer chute, não só no alvo — via{' '}
          <code className="text-slate-400">lambda_chutes_jogo</code>), linhas +1 até +10 — por ser um mercado bem mais extenso (10 linhas
          em vez de 3), fica numa tabela separada da de chutes-ao-gol/gols. Importe uma imagem do mercado "Total de chutes" da casa (ou
          cole um JSON com mercado "Chutes" na seção acima) pra comparar lado a lado, mesma coloração (verde = valor, vermelho = sem valor).
        </p>
        <label
          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-bold cursor-pointer transition-colors shrink-0 ${
            ocrChutesLoading ? 'bg-slate-700 text-slate-400 cursor-wait' : 'bg-purple-600 hover:bg-purple-500 text-white'
          }`}
        >
          {ocrChutesLoading ? <Loader2 size={14} className="animate-spin" /> : <Camera size={14} />}
          Importar odds (OCR)
          <input type="file" accept="image/*" capture="environment" className="hidden" disabled={ocrChutesLoading} onChange={handleOcrOddsChutes} />
        </label>
      </div>
      {(ocrChutesErro || ocrChutesMsg) && (
        <div className={`mb-3 px-3 py-2 rounded-lg border text-[11px] flex items-start gap-2 ${
          ocrChutesErro ? 'bg-red-950/30 border-red-500/40 text-red-400' : 'bg-emerald-950/30 border-emerald-500/40 text-emerald-400'
        }`}>
          {ocrChutesErro ? <X size={13} className="mt-0.5 shrink-0" /> : <Check size={13} className="mt-0.5 shrink-0" />}
          <span>{ocrChutesErro || ocrChutesMsg}</span>
        </div>
      )}
      <TabelaOddsJustasIndividual
        titulo={homeNome || 'Mandante'} linhas={porTime[homeTeamId] || []} oddsImportadas={oddsImportadas}
        mercados={MERCADOS_CHUTES_TOTAIS} chaveOrdenacao="lambda_chutes_jogo"
      />
      <TabelaOddsJustasIndividual
        titulo={awayNome || 'Visitante'} linhas={porTime.outro || []} oddsImportadas={oddsImportadas}
        mercados={MERCADOS_CHUTES_TOTAIS} chaveOrdenacao="lambda_chutes_jogo"
      />
    </Secao>

    <Secao titulo="Comparação de EV multi-casas (jogador)" icone={Scale}>
      <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
        <p className="text-[11px] text-slate-500 max-w-2xl">
          Uma linha por (jogador, mercado, linha, casa) com odd importada — diferente das tabelas acima (que só mostram a melhor odd por
          célula), aqui cada casa aparece separada, ordenado por edge decrescente. Edge é probabilidade implícita simples (1/odd real) sem
          devig — mercados de "N+" só trazem o lado "mais de" (não a perna complementar "menos de"), então não dá pra devigar por Odds
          Ratio como na verificação de EV de time acima; é uma aproximação com a margem da casa embutida, não a probabilidade "limpa" de
          mercado.
        </p>
        <div className="flex items-center gap-3 shrink-0">
          <label className="flex items-center gap-1.5 text-[11px] text-slate-400 cursor-pointer">
            <input type="checkbox" checked={soEvPositivo} onChange={(e) => setSoEvPositivo(e.target.checked)} />
            Só EV positivo
          </label>
          <BotaoExportarCSV onClick={exportarComparacaoEV} disabled={comparacaoEVVisivel.length === 0} />
        </div>
      </div>
      {comparacaoEVVisivel.length === 0 ? (
        <p className="text-[11px] text-slate-600 italic">
          {comparacaoEVLinhas.length === 0
            ? 'Nenhuma odd de jogador importada ainda (OCR ou JSON colado acima).'
            : 'Nenhuma linha com EV positivo entre as odds importadas — desmarque "Só EV positivo" pra ver todas.'}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-500 text-left">
                <th className="py-1 px-2 font-normal text-left">Jogador</th>
                <th className="py-1 px-2 font-normal text-left">Time</th>
                <th className="py-1 px-2 font-normal text-left">Mercado</th>
                <th className="py-1 px-2 font-normal text-right">Linha</th>
                <th className="py-1 px-2 font-normal text-left">Casa</th>
                <th className="py-1 px-2 font-normal text-right">Odd real</th>
                <th className="py-1 px-2 font-normal text-right">Odd justa</th>
                <th className="py-1 px-2 font-normal text-right">Prob. modelo</th>
                <th className="py-1 px-2 font-normal text-right">Edge (pp)</th>
                <th className="py-1 px-2 font-normal text-right">EV (%)</th>
              </tr>
            </thead>
            <tbody>
              {comparacaoEVVisivel.map((l, i) => {
                const edgePct = l.edge * 100;
                const corEdge = edgePct > 2 ? 'text-emerald-400' : edgePct < -2 ? 'text-red-400' : 'text-slate-300';
                return (
                  <tr key={`${l.jogador}-${l.mercado}-${l.linha}-${l.casa}-${i}`} className="border-t border-slate-800">
                    <td className="py-1.5 pr-2 text-slate-200 font-semibold whitespace-nowrap">{l.jogador}</td>
                    <td className="py-1.5 px-2 text-slate-400 whitespace-nowrap">{l.time}</td>
                    <td className="py-1.5 px-2 text-slate-400 whitespace-nowrap">{l.mercado}</td>
                    <td className="py-1.5 px-2 text-right font-mono text-slate-300">+{l.linha}</td>
                    <td className="py-1.5 px-2 text-slate-400 whitespace-nowrap">{l.casa}</td>
                    <td className="py-1.5 px-2 text-right font-mono text-slate-200">{fmtOdds(l.oddReal)}</td>
                    <td className="py-1.5 px-2 text-right font-mono text-slate-400">{fmtOdds(l.oddJusta)}</td>
                    <td className="py-1.5 px-2 text-right font-mono text-slate-400">{fmtPct(l.pModelo)}</td>
                    <td className={`py-1.5 px-2 text-right font-mono ${corEdge}`}>{edgePct.toFixed(1)}</td>
                    <td className={`py-1.5 px-2 text-right font-mono ${corEdge}`}>{(l.ev * 100).toFixed(1)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Secao>
    </>
  );
}

// Busca TODAS as linhas de odds_market da partida nos mercados em escopo,
// paginado de verdade (loop de `.range()` até vir página incompleta) -- não
// é frescura: uma partida negociada por muito tempo/muitos bookmakers pode
// somar dezenas de milhares de linhas mesmo já filtrando por mercado, então
// confiar numa consulta sem `.range()` reproduziria o corte silencioso de
// 1000 linhas do PostgREST já documentado em várias partes do projeto.
//
// Traz todo o histórico (não só `snapshot='closing'`) mesmo pra partida já
// finalizada: nem toda partida tem uma linha marcada como fechamento de
// verdade (a marcação depende do pipeline que capturou aquela odd), e nesse
// caso a última captura `pre_closing` antes do jogo é a melhor aproximação
// disponível -- o snapshot escolhido pelo usuário decide, "closing" é só
// mais um ponto na lista, destacado quando existe (ver snapshotsFechamento).
async function buscarOddsPaginado(matchId) {
  const TAMANHO_PAGINA = 1000;
  const resultado = [];
  let pagina = 0;
  while (true) {
    const { data, error } = await supabase
      .from('odds_market')
      .select('bookmaker, market, selection, odds, captured_at, snapshot')
      .eq('match_id', matchId)
      .in('market', MERCADOS_EV)
      .order('captured_at', { ascending: false })
      .range(pagina * TAMANHO_PAGINA, pagina * TAMANHO_PAGINA + TAMANHO_PAGINA - 1);
    if (error) throw error;
    resultado.push(...(data || []));
    if (!data || data.length < TAMANHO_PAGINA) break;
    pagina++;
  }
  return resultado;
}

// Colunas de export da verificação de EV/stake -- "Modelo" fica de fora
// daqui (é `modelSelecionado`, estado do componente) e é prependada no
// clique, mesmo padrão da coluna "Time" em COLUNAS_EXPORT_JOGADOR_MERCADOS_BASE.
const COLUNAS_EXPORT_EV_BASE = [
  { header: 'Casa de apostas', get: (l) => l.bookmaker },
  { header: 'Mercado', get: (l) => rotuloMercado(l.mercado) },
  { header: 'Seleção', get: (l) => l.selecao },
  { header: 'Odd real', get: (l) => numCSV(l.oddReal, 2) },
  { header: 'Prob. modelo (crua)', get: (l) => numCSV(l.pModelo, 4) },
  { header: 'Prob. modelo (calibrada)', get: (l) => (l.pCalibrado != null ? numCSV(l.pCalibrado, 4) : '') },
  { header: 'Método calibração', get: (l) => l.metodoCalibracao || '' },
  { header: 'Prob. mercado (devig)', get: (l) => numCSV(l.pMercado, 4) },
  { header: 'Edge (pp)', get: (l) => numCSV(l.edge * 100, 2) },
  { header: 'EV (%)', get: (l) => numCSV(l.ev * 100, 2) },
  { header: 'Stake Kelly 25% (%)', get: (l) => numCSV(l.kelly25 * 100, 2) },
  { header: 'Acertou', get: (l) => (l.acertou == null ? '' : l.acertou ? 'Sim' : 'Não') },
  { header: 'Retorno (% banca)', get: (l) => (l.retorno == null ? '' : numCSV(l.retorno * 100, 2)) },
];

export default function AnaliseAvancadaEvento() {
  const { matchId } = useParams();
  const [jogo, setJogo] = useState(null);
  const [estimativas, setEstimativas] = useState([]);
  const [modelSelecionado, setModelSelecionado] = useState(null);
  const [oddsRaw, setOddsRaw] = useState([]);
  const [snapshotSelecionado, setSnapshotSelecionado] = useState(''); // '' = mais recente de cada casa
  const [resultadoReal, setResultadoReal] = useState(null); // só preenchido pra partida finalizada
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [calibracaoRows, setCalibracaoRows] = useState([]);
  const [jogadorEstimativas, setJogadorEstimativas] = useState([]);

  useEffect(() => {
    if (!supabaseAtivo) return;
    let cancelado = false;
    (async () => {
      setCarregando(true);
      setErro('');
      try {
        const { data: j, error: erroJogo } = await supabase
          .from('matches')
          .select('id, match_date, status, home_goals, away_goals, leagues(name), home:teams!matches_home_team_id_fkey(id,name,crest_url), away:teams!matches_away_team_id_fkey(id,name,crest_url)')
          .eq('id', matchId)
          .single();
        if (cancelado) return;
        if (erroJogo || !j) { setErro('Jogo não encontrado.'); setCarregando(false); return; }

        const finalizada = j.status === 'finished';
        // Agendada OU finalizada: todo o histórico de captured_at, pro
        // seletor de snapshot -- fechamento (quando existe) aparece como
        // mais uma opção na lista, não é exigido. Qualquer outro status (ao
        // vivo/adiado/cancelado): não busca odds.
        const [{ data: est, error: erroEst }, odds, corners, { data: calib }, { data: jogadorEst }] = await Promise.all([
          supabase
            .from('model_match_estimates')
            .select('model_name, params')
            .eq('match_id', matchId)
            .not('params', 'is', null),
          (j.status === 'scheduled' || finalizada) ? buscarOddsPaginado(matchId) : Promise.resolve([]),
          finalizada ? buscarCornersReais(matchId, j.home?.id, j.away?.id) : Promise.resolve(null),
          // Calibração Platt/Isotonic já ajustada (model_calibration), mesmo
          // padrão de AnaliseEstatisticaJogo.jsx -- o edge/EV/Kelly abaixo
          // usa a probabilidade CALIBRADA quando existe pra essa combinação
          // (model_name, mercado, seleção), já que o modelo bruto do modelo
          // misto é sistematicamente overconfident nalguns mercados (achado
          // #6) e o Kelly (dimensionamento de aposta REAL sugerido aqui)
          // assume p confiável. Cobertura real hoje é só 1X2/btts/
          // over_under_2.5 (ver achado #22-adjacente) -- outras linhas/
          // escanteios ficam sem match e caem no fallback pra probabilidade
          // crua (ver `calibrarProbabilidade`), sem quebrar nada.
          (j.status === 'scheduled' || finalizada)
            ? supabase.from('model_calibration').select('model_name, market, selection, method, platt_coef, platt_intercept, isotonic_x, isotonic_y, log_loss_bruto, log_loss_calibrado, n_teste')
            : Promise.resolve({ data: [] }),
          // Chutes/gols por jogador (player_match_estimates) -- as linhas só
          // são GERADAS enquanto a partida está AGENDADA
          // (rodar_jogador_mercados_previsto.py só pontua fixtures
          // scheduled, nunca reprocessa o passado), mas ficam no banco pra
          // sempre depois disso -- nada as apaga quando a partida termina.
          // Buscar só em `scheduled` (sem o `|| finalizada` que as outras
          // consultas desta página já usam, ver acima) escondia a seção
          // inteira assim que a partida virava `finished`, mesmo com dado
          // real já persistido (achado real, partida 14987 e outras já
          // finalizadas). Traz as DUAS fontes quando existirem
          // (fonte_titular='previsto'/'real', nunca uma sobrescrevendo a
          // outra no banco -- ver migration) pra comparação lado a lado.
          // RLS de leitura pública, mesma consulta direta via supabase-js
          // de todo o resto desta página (sem função serverless nova).
          (j.status === 'scheduled' || finalizada)
            ? supabase
                .from('player_match_estimates')
                .select('team_id, player_id, fonte_titular, prob_titular_usada, minutos_esperados, taxa_conversao_bayesiana, taxa_no_alvo_bayesiana, chutes_90_bayesiano, gols_90_bayesiano, xg_90_bayesiano, chutes_no_alvo_90_bayesiano, chutes_por_jogo, gols_por_jogo, xg_por_jogo, chutes_no_alvo_por_jogo, posicao_detalhe, lambda_chutes_jogo, lambda_gols_jogo_thinning, lambda_gols_jogo_direto, lambda_xg_jogo, lambda_chutes_no_alvo_jogo, players(name, photo_url, usual_position_id)')
                .eq('match_id', matchId)
            : Promise.resolve({ data: [] }),
        ]);
        if (cancelado) return;
        if (erroEst) { setErro(erroEst.message); setCarregando(false); return; }

        setJogo(j);
        setOddsRaw(odds);
        setSnapshotSelecionado('');
        setResultadoReal(finalizada ? { golsHome: j.home_goals, golsAway: j.away_goals, ...corners } : null);
        setCalibracaoRows(calib || []);
        setJogadorEstimativas(jogadorEst || []);

        const validas = (est || []).filter((e) => lerParametrosPartida(e.params) != null);
        setEstimativas(validas);
        setModelSelecionado(validas[0]?.model_name ?? null);
        setCarregando(false);
      } catch (e) {
        if (!cancelado) { setErro(e.message); setCarregando(false); }
      }
    })();
    return () => { cancelado = true; };
  }, [matchId]);

  // Timestamps distintos observados em QUALQUER casa/mercado, mais recente
  // primeiro -- alimenta o seletor de snapshot. Cada casa sincroniza em
  // horários próprios (confirmado: Pinnacle e Betano nunca coincidem no
  // captured_at da mesma partida), então "escolher um snapshot" não filtra
  // por timestamp exato -- ver oddsPorBookmaker abaixo.
  const snapshotsDisponiveis = useMemo(
    () => [...new Set(oddsRaw.map((r) => r.captured_at))].sort((a, b) => new Date(b) - new Date(a)),
    [oddsRaw]
  );

  // Quais desses timestamps têm pelo menos uma linha marcada snapshot='closing'
  // -- só pra destacar na lista (rótulo "fechamento"), nem toda partida tem
  // uma (a marcação depende da fonte); sem ela, a última captura pre_closing
  // antes do jogo já serve como aproximação.
  const snapshotsFechamento = useMemo(
    () => new Set(oddsRaw.filter((r) => r.snapshot === 'closing').map((r) => r.captured_at)),
    [oddsRaw]
  );

  // Odd mais recente de cada (bookmaker, market, selection) NA DATA (ou
  // antes) do snapshot escolhido -- com snapshotSelecionado='' (padrão),
  // equivale a "mais recente de cada casa", sem exigir que todas as casas
  // tenham sincronizado no mesmo instante.
  const oddsPorBookmaker = useMemo(() => {
    const limite = snapshotSelecionado ? new Date(snapshotSelecionado).getTime() : Infinity;
    const porBookmaker = {};
    for (const r of oddsRaw) {
      if (new Date(r.captured_at).getTime() > limite) continue;
      if (!porBookmaker[r.bookmaker]) porBookmaker[r.bookmaker] = {};
      const chave = `${r.market}|${r.selection}`;
      // oddsRaw já vem ordenado captured_at desc -- a primeira ocorrência
      // dentro do limite é a mais recente até aquele ponto.
      if (!(chave in porBookmaker[r.bookmaker])) porBookmaker[r.bookmaker][chave] = r.odds;
    }
    return porBookmaker;
  }, [oddsRaw, snapshotSelecionado]);

  const estimativaAtiva = useMemo(
    () => estimativas.find((e) => e.model_name === modelSelecionado) || null,
    [estimativas, modelSelecionado]
  );

  const parametros = useMemo(
    () => (estimativaAtiva ? lerParametrosPartida(estimativaAtiva.params) : null),
    [estimativaAtiva]
  );

  const mercadosGols = useMemo(() => {
    if (!parametros) return null;
    const matriz = matrizPlacares(parametros.lambdaHome, parametros.lambdaAway, parametros.rho);
    return mercadosDeGols(matriz, { linhasOverUnder: LINHAS_OU_GOLS, linhasHandicap: LINHAS_HANDICAP });
  }, [parametros]);

  const mercadosCorners = useMemo(() => {
    if (!parametros?.cornersLambdaTotal || !parametros?.cornersDispR) return null;
    return mercadosDeEscanteios(
      parametros.cornersLambdaTotal, parametros.cornersDispR,
      parametros.cornersAlpha || 1, parametros.cornersBeta || 1,
      { linhasTotais: LINHAS_OU_CORNERS }
    );
  }, [parametros]);

  // Verificação de EV vs. cada mercado salvo (odds_market) -- útil tanto ANTES
  // (partida agendada, decisão de aposta de verdade) quanto DEPOIS (partida
  // finalizada, conferência de erros/acertos contra o resultado real) do
  // jogo. Pra cada casa de apostas com odds salvas (no snapshot escolhido, ou
  // a odd de fechamento se finalizada) nos mercados que o modelo também
  // precifica (gols E escanteios), devigamos (Odds Ratio) e comparamos com a
  // probabilidade do modelo -- edge = p_modelo - p_mercado devigada, EV usa a
  // odd REAL (não devigada, é o que se paga de fato), stake sugerida em
  // Kelly 1/4 (mesmo padrão de api/backtest-betting.js/SimulacaoCarteira.jsx).
  // Em partida finalizada, cada linha também ganha `acertou` (a seleção bateu
  // com o resultado real?) e `retorno` (lucro/prejuízo em % da banca SE a
  // stake de Kelly 25% sugerida tivesse sido apostada de verdade).
  const indiceCalibracao = useMemo(() => indexarCalibracao(calibracaoRows), [calibracaoRows]);

  const finalizada = jogo?.status === 'finished';
  const verificacaoEV = useMemo(() => {
    if (!(jogo?.status === 'scheduled' || finalizada) || (!mercadosGols && !mercadosCorners)) return [];
    const comparaveis = mercadosComparaveis(mercadosGols, mercadosCorners);
    const linhas = [];
    for (const [bookmaker, oddsChave] of Object.entries(oddsPorBookmaker)) {
      for (const [mercado, probsModelo] of Object.entries(comparaveis)) {
        if (!probsModelo) continue;
        const selecoes = Object.keys(probsModelo);
        const oddsSelecoes = {};
        for (const s of selecoes) {
          const odd = oddsChave[`${mercado}|${s}`];
          if (odd != null) oddsSelecoes[s] = odd;
        }
        if (Object.keys(oddsSelecoes).length !== selecoes.length) continue; // precisa de TODAS as pernas do mercado pra devigar
        const devigado = devigarOddsRatio(oddsSelecoes);
        for (const s of selecoes) {
          const pModelo = probsModelo[s];
          const oddReal = oddsSelecoes[s];
          const pMercado = devigado[s];
          // Edge/EV/Kelly usam a probabilidade CALIBRADA quando essa
          // combinação (model_name, mercado, seleção) já foi calibrada --
          // cai pra crua (pModelo) quando não tem (ver calibrarProbabilidade).
          const calibrado = modelSelecionado
            ? calibrarProbabilidade(pModelo, indiceCalibracao, modelSelecionado, mercado, s)
            : null;
          const pParaCalculo = calibrado?.probabilidade ?? pModelo;
          const edge = pParaCalculo - pMercado;
          const ev = pParaCalculo * oddReal - 1;
          const kelly25 = stakeKelly25(pParaCalculo, oddReal);
          const acertou = finalizada && resultadoReal ? avaliarSelecao(mercado, s, resultadoReal) : null;
          const retorno = finalizada && acertou != null && kelly25 > 0
            ? (acertou ? kelly25 * (oddReal - 1) : -kelly25)
            : null;
          linhas.push({
            bookmaker, mercado, selecao: s, oddReal, pModelo, pCalibrado: calibrado?.probabilidade ?? null,
            metodoCalibracao: calibrado?.metodo ?? null, pMercado, edge, ev, kelly25, acertou, retorno,
          });
        }
      }
    }
    return linhas.sort((a, b) => b.edge - a.edge);
  }, [jogo?.status, finalizada, mercadosGols, mercadosCorners, oddsPorBookmaker, resultadoReal, modelSelecionado, indiceCalibracao]);

  // Export cobre TODAS as linhas de verificacaoEV (todas as casas/mercados/
  // seleções, não só edge positivo) -- pedido explícito do usuário: "quero
  // ... todas as posições dos modelos para o jogo".
  const nomeArquivoEV = `ev_stake_${sanitizarNomeArquivo(jogo?.home?.name)}_x_${sanitizarNomeArquivo(jogo?.away?.name)}_${jogo?.match_date ? jogo.match_date.slice(0, 10) : ''}_${sanitizarNomeArquivo(modelSelecionado)}.csv`;
  const exportarVerificacaoEV = () => {
    const colunasExportEV = [{ header: 'Modelo', get: () => modelSelecionado || '' }, ...COLUNAS_EXPORT_EV_BASE];
    exportarCSV(verificacaoEV, colunasExportEV, nomeArquivoEV);
  };

  // Mesma matriz que já alimenta 1X2/placar exato acima, só que truncada
  // pra uma grade menor (0-7) que cabe na tela célula a célula.
  const gradePlacar = useMemo(() => {
    if (!parametros) return null;
    return matrizPlacares(parametros.lambdaHome, parametros.lambdaAway, parametros.rho, MAX_GOLS_GRADE);
  }, [parametros]);

  // Mesma decomposição (NB do total × Beta-Binomial do split) de
  // `mercadosCorners` acima, só que devolvendo a matriz casa×fora inteira
  // em vez de agregada em over/under -- truncada em 0-14 pela SOMA
  // (casa+fora <= 14), não por lado independente, então o canto superior
  // direito da grade fica zerado (não é bug: escanteios totais raramente
  // passam de ~15-16 na prática).
  const gradeCorners = useMemo(() => {
    if (!parametros?.cornersLambdaTotal || !parametros?.cornersDispR) return null;
    return distribuicaoConjuntaEscanteios(
      parametros.cornersLambdaTotal, parametros.cornersDispR,
      parametros.cornersAlpha || 1, parametros.cornersBeta || 1,
      MAX_CORNERS_GRADE
    );
  }, [parametros]);

  if (!supabaseAtivo) {
    return (
      <div className="max-w-4xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">Supabase não configurado.</p>
      </div>
    );
  }

  if (carregando) {
    return (
      <div className="max-w-4xl mx-auto flex items-center justify-center py-16 text-slate-500 gap-2 text-sm">
        <Loader2 className="animate-spin" size={20} /> Carregando...
      </div>
    );
  }

  if (erro || !jogo) {
    return (
      <div className="max-w-4xl mx-auto bg-slate-800 border border-red-500/30 rounded-2xl p-6 text-center">
        <AlertTriangle className="text-red-400 mx-auto mb-2" size={28} />
        <p className="text-slate-300">{erro || 'Jogo não encontrado.'}</p>
        <Link to="/eventos" className="text-emerald-400 text-sm hover:underline mt-3 inline-block">← Voltar pra Eventos</Link>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <Link to="/eventos" className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 text-sm w-fit">
          <ArrowLeft size={16} /> Voltar
        </Link>
        <Link
          to={`/historico/${jogo.id}`}
          className="flex items-center gap-1.5 bg-slate-700/40 hover:bg-slate-700 text-slate-300 text-xs font-bold px-3 py-1.5 rounded-lg transition-colors"
        >
          Forma recente & confronto direto
        </Link>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
        <p className="text-center text-xs text-slate-500 uppercase tracking-wider mb-1 flex items-center justify-center gap-1.5">
          <FlaskConical size={12} className="text-emerald-400" /> Análise Avançada — Modelo Misto
        </p>
        <p className="text-center text-xs text-slate-600 mb-3">
          {jogo.leagues?.name || 'Confronto'}
          {jogo.match_date && ` · ${new Date(jogo.match_date).toLocaleString('pt-BR', { dateStyle: 'long', timeStyle: 'short' })}`}
        </p>
        <div className="flex items-center justify-center gap-4">
          <div className="flex items-center gap-2">
            <Escudo url={jogo.home?.crest_url} tamanho={32} />
            <span className="font-bold text-slate-100">{jogo.home?.name}</span>
          </div>
          <span className="text-slate-500 font-mono">
            {jogo.status === 'finished' ? `${jogo.home_goals}-${jogo.away_goals}` : 'vs'}
          </span>
          <div className="flex items-center gap-2">
            <span className="font-bold text-slate-100">{jogo.away?.name}</span>
            <Escudo url={jogo.away?.crest_url} tamanho={32} />
          </div>
        </div>
      </div>

      {estimativas.length === 0 ? (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-8 text-center">
          <FlaskConical className="text-slate-600 mx-auto mb-3" size={32} />
          <p className="text-slate-300 font-semibold">Nenhuma estimativa do modelo misto para esta partida ainda.</p>
          <p className="text-slate-500 text-sm mt-2 max-w-md mx-auto">
            O modelo misto (λ estimado por ML + Dixon-Coles/Binomial Negativa) só cobre partidas
            que passaram pelo pipeline de treino (<code className="text-slate-400">scripts/treinar_modelo_hibrido.py</code> ou
            uma config de Treino Customizado com algoritmo <code className="text-slate-400">hibrido_parametrico</code>).
            Essa partida pode estar fora do escopo de liga/temporada treinado, ou o treino ainda não rodou.
          </p>
        </div>
      ) : (
        <>
          {estimativas.length > 1 && (
            <div className="flex gap-1 mb-4 bg-slate-800 border border-slate-700 rounded-2xl p-1.5 w-fit flex-wrap">
              {estimativas.map((e) => (
                <button
                  key={e.model_name}
                  onClick={() => setModelSelecionado(e.model_name)}
                  className={`px-3.5 py-2 rounded-xl text-sm font-bold transition-colors ${
                    modelSelecionado === e.model_name ? 'bg-emerald-500/20 text-emerald-400' : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {e.model_name}
                </button>
              ))}
            </div>
          )}

          <Secao titulo="Parâmetros do modelo (direto do banco)" icone={FlaskConical}>
            <p className="text-[11px] text-slate-500 mb-3">
              Modelo: <span className="text-slate-300 font-mono">{estimativaAtiva?.model_name}</span> — leitura de
              {' '}<code className="text-slate-400">model_match_estimates.params</code>, sem edição manual.
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <CardParametro label="xG estimado — mandante" valor={fmtNum(parametros?.lambdaHome, 2)} />
              <CardParametro label="xG estimado — visitante" valor={fmtNum(parametros?.lambdaAway, 2)} />
              <CardParametro label="ρ (Dixon-Coles)" valor={fmtNum(parametros?.rho, 4)} />
              {parametros?.cornersLambdaTotal != null && (
                <CardParametro label="λ escanteios (total)" valor={fmtNum(parametros.cornersLambdaTotal, 2)} />
              )}
              {parametros?.cornersDispR != null && (
                <CardParametro label="Dispersão r (escanteios)" valor={fmtNum(parametros.cornersDispR, 1)} />
              )}
              {parametros?.cornersAlpha != null && (
                <CardParametro label="α (split casa/fora)" valor={fmtNum(parametros.cornersAlpha, 2)} />
              )}
              {parametros?.cornersBeta != null && (
                <CardParametro label="β (split casa/fora)" valor={fmtNum(parametros.cornersBeta, 2)} />
              )}
            </div>
          </Secao>

          {mercadosGols && (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-4">
                <div className="bg-slate-900 border-t-4 border-emerald-500 border border-slate-700 rounded-xl p-5 flex flex-col items-center text-center">
                  <span className="text-slate-400 text-[10px] uppercase tracking-wider mb-2 line-clamp-1">{jogo.home?.name}</span>
                  <span className="text-2xl font-black text-white">{fmtPct(mercadosGols['1X2'].home)}</span>
                </div>
                <div className="bg-slate-900 border-t-4 border-slate-500 border border-slate-700 rounded-xl p-5 flex flex-col items-center text-center">
                  <span className="text-slate-400 text-[10px] uppercase tracking-wider mb-2">Empate</span>
                  <span className="text-2xl font-black text-white">{fmtPct(mercadosGols['1X2'].draw)}</span>
                </div>
                <div className="bg-slate-900 border-t-4 border-orange-500 border border-slate-700 rounded-xl p-5 flex flex-col items-center text-center">
                  <span className="text-slate-400 text-[10px] uppercase tracking-wider mb-2 line-clamp-1">{jogo.away?.name}</span>
                  <span className="text-2xl font-black text-white">{fmtPct(mercadosGols['1X2'].away)}</span>
                </div>
              </div>

              {(jogo.status === 'scheduled' || finalizada) && (
                <div className="mt-4">
                  <Secao
                    titulo={finalizada ? 'Verificação de EV vs. mercado — erros e acertos' : 'Verificação de EV vs. mercado'}
                    icone={Scale}
                  >
                    <div className="flex justify-end mb-3">
                      <BotaoExportarCSV onClick={exportarVerificacaoEV} disabled={verificacaoEV.length === 0} />
                    </div>
                    {finalizada && (
                      <p className="text-[11px] text-slate-500 mb-3">
                        Comparação contra o resultado real da partida — não é mais uma decisão de aposta, é
                        conferência do que teria acontecido. Escolha o snapshot mais próximo do apito inicial abaixo
                        (o de fechamento, quando existe, vem marcado); sem fechamento salvo, a última captura prévia
                        antes do jogo já serve pra esse propósito.
                      </p>
                    )}
                    {snapshotsDisponiveis.length > 0 && (
                      <div className="flex items-center gap-2 mb-3">
                        <label className="text-[10px] text-slate-500 uppercase font-bold shrink-0">Snapshot</label>
                        <select
                          value={snapshotSelecionado}
                          onChange={(e) => setSnapshotSelecionado(e.target.value)}
                          className="bg-slate-900 border border-slate-700 rounded-md px-2 py-1.5 text-xs text-slate-200"
                        >
                          <option value="">Mais recente de cada casa</option>
                          {snapshotsDisponiveis.map((ts) => (
                            <option key={ts} value={ts}>
                              {formatarSnapshot(ts)}{snapshotsFechamento.has(ts) ? ' (fechamento)' : ''}
                            </option>
                          ))}
                        </select>
                        <span className="text-[10px] text-slate-600">
                          "Mais recente de cada casa" não exige sincronismo entre bookmakers — cada um sincroniza no
                          seu próprio horário; um snapshot escolhido mostra o que estava salvo até aquele instante.
                        </span>
                      </div>
                    )}
                    {verificacaoEV.length === 0 ? (
                      <p className="text-sm text-slate-600">
                        Nenhuma casa de apostas com odds salvas nos mercados que o modelo precifica (1X2, Over/Under
                        de gols, Ambas Marcam, 1X2/Over-Under de escanteios) pra esta partida
                        {snapshotSelecionado ? ' nesse snapshot' : ''}
                        {finalizada
                          ? '.'
                          : <> ainda — importe odds pela tela de rodada, {snapshotSelecionado ? 'escolha outro snapshot' : 'aguarde o próximo sync'}.</>}
                      </p>
                    ) : (
                      <>
                        {finalizada && (() => {
                          const avaliadas = verificacaoEV.filter((l) => l.edge > 0.02 && l.acertou != null);
                          const acertos = avaliadas.filter((l) => l.acertou).length;
                          return avaliadas.length > 0 ? (
                            <p className="text-sm mb-3">
                              Entre as seleções com edge {'>'} 2pp (as que teriam recebido stake de Kelly):{' '}
                              <span className="font-bold text-emerald-400">{acertos} acerto{acertos !== 1 ? 's' : ''}</span>
                              {' '}·{' '}
                              <span className="font-bold text-red-400">{avaliadas.length - acertos} erro{avaliadas.length - acertos !== 1 ? 's' : ''}</span>
                            </p>
                          ) : null;
                        })()}
                        <div className="overflow-x-auto">
                          <table className="w-full text-sm">
                            <thead>
                              <tr className="text-slate-500 text-xs uppercase">
                                <th className="text-left pb-2">Casa</th>
                                <th className="text-left pb-2">Mercado</th>
                                <th className="text-right pb-2">Odd real</th>
                                <th className="text-right pb-2">Prob. modelo{verificacaoEV.some((l) => l.pCalibrado != null) ? '*' : ''}</th>
                                <th className="text-right pb-2">Prob. mercado (devig)</th>
                                <th className="text-right pb-2">Edge</th>
                                <th className="text-right pb-2">EV</th>
                                <th className="text-right pb-2">Stake Kelly 25%</th>
                                {finalizada && <th className="text-right pb-2">Resultado</th>}
                                {finalizada && <th className="text-right pb-2">Retorno</th>}
                              </tr>
                            </thead>
                            <tbody>
                              {verificacaoEV.map((l, i) => (
                                <tr key={i} className="border-t border-slate-800">
                                  <td className="py-1.5 text-slate-300 capitalize">{l.bookmaker.replace(/_/g, ' ')}</td>
                                  <td className="py-1.5 text-slate-400 text-xs">{rotuloMercado(l.mercado)} · <span className="uppercase">{l.selecao}</span></td>
                                  <td className="py-1.5 text-right font-mono text-slate-200">{l.oddReal.toFixed(2)}</td>
                                  <td
                                    className="py-1.5 text-right font-mono text-slate-300"
                                    title={l.pCalibrado != null
                                      ? `Calibrado (${l.metodoCalibracao === 'platt' ? 'Platt Scaling' : 'Isotonic Regression'}) -- cru: ${toPct(l.pModelo)}`
                                      : undefined}
                                  >
                                    {toPct(l.pCalibrado ?? l.pModelo)}{l.pCalibrado != null && <span className="text-emerald-500">*</span>}
                                  </td>
                                  <td className="py-1.5 text-right font-mono text-slate-500">{toPct(l.pMercado)}</td>
                                  <td className={`py-1.5 text-right font-mono font-bold ${l.edge > 0.02 ? 'text-emerald-400' : l.edge < -0.02 ? 'text-red-400' : 'text-slate-400'}`}>
                                    {l.edge > 0 ? '+' : ''}{(l.edge * 100).toFixed(1)}pp
                                  </td>
                                  <td className={`py-1.5 text-right font-mono ${l.ev > 0 ? 'text-emerald-400' : 'text-slate-500'}`}>
                                    {l.ev > 0 ? '+' : ''}{(l.ev * 100).toFixed(1)}%
                                  </td>
                                  <td className="py-1.5 text-right font-mono text-slate-300">{l.kelly25 > 0 ? `${(l.kelly25 * 100).toFixed(2)}%` : '—'}</td>
                                  {finalizada && (
                                    <td className="py-1.5 text-right font-bold">
                                      {l.acertou == null ? <span className="text-slate-700">—</span>
                                        : l.acertou ? <span className="text-emerald-400">✓ Acertou</span>
                                        : <span className="text-red-400">✗ Errou</span>}
                                    </td>
                                  )}
                                  {finalizada && (
                                    <td className={`py-1.5 text-right font-mono ${l.retorno == null ? 'text-slate-700' : l.retorno > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                      {l.retorno == null ? '—' : `${l.retorno > 0 ? '+' : ''}${(l.retorno * 100).toFixed(2)}%`}
                                    </td>
                                  )}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <p className="text-[11px] text-slate-600 mt-3 leading-relaxed">
                          Edge = probabilidade do modelo − probabilidade do mercado (devigada, Odds Ratio). EV usa a
                          odd REAL (com margem da casa), não a devigada. Stake Kelly 25% = ¼ do critério de Kelly
                          sobre a odd real, com teto de 25% da banca por aposta — mesma matemática de
                          `api/backtest-betting.js`.
                          {verificacaoEV.some((l) => l.pCalibrado != null) && ' * Probabilidade calibrada (Platt Scaling/Isotonic Regression, model_calibration) -- Edge/EV/Stake usam o valor calibrado quando existe pra essa combinação de modelo/mercado/seleção; sem calibração ainda disponível, cai pra probabilidade crua.'}
                          {finalizada && ' Retorno = lucro/prejuízo em % da banca SE a stake de Kelly 25% sugerida tivesse sido apostada de verdade (só calculado quando havia stake > 0).'}
                          {' '}<strong className="text-slate-500">Contexto importante antes de
                          usar isso pra apostar de verdade:</strong> a avaliação pareada do modelo misto contra o
                          mercado (Pinnacle devigada) mostrou o mercado batendo o modelo em log-loss nos 3 mercados
                          testados (1X2, Over/Under 2.5, BTTS), com IC 95% não cruzando zero — um edge positivo
                          isolado aqui (ou um punhado de acertos numa partida só) pode ser ruído de amostra, não
                          vantagem real comprovada.
                        </p>
                      </>
                    )}
                  </Secao>
                </div>
              )}

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                <Secao titulo="Dupla chance & BTTS" icone={Percent}>
                  <div className="grid grid-cols-3 gap-2 mb-3">
                    <CardParametro label="1X" valor={fmtPct(mercadosGols.dupla_chance['1X'])} />
                    <CardParametro label="12" valor={fmtPct(mercadosGols.dupla_chance['12'])} />
                    <CardParametro label="X2" valor={fmtPct(mercadosGols.dupla_chance.X2)} />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <CardParametro label="Ambas marcam — Sim" valor={fmtPct(mercadosGols.btts.yes)} />
                    <CardParametro label="Ambas marcam — Não" valor={fmtPct(mercadosGols.btts.no)} />
                  </div>
                </Secao>

                <Secao titulo="Faixa de gols" icone={Target}>
                  <div className="grid grid-cols-2 gap-2">
                    {Object.entries(mercadosGols.faixa_gols).map(([faixa, p]) => (
                      <CardParametro key={faixa} label={`${faixa} gols`} valor={fmtPct(p)} />
                    ))}
                  </div>
                </Secao>
              </div>

              <Secao titulo="Over / Under gols" icone={TrendingUp}>
                {LINHAS_OU_GOLS.map((linha) => {
                  const m = mercadosGols[`over_under_${rotuloLinha(linha)}`];
                  return <LinhaBinaria key={linha} rotulo={rotuloLinha(linha)} over={m?.over} under={m?.under} />;
                })}
              </Secao>

              <Secao titulo="Handicap asiático (mandante)" icone={Target}>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-slate-500 text-xs uppercase">
                        <th className="text-left pb-2">Linha</th>
                        <th className="text-right pb-2">{jogo.home?.name}</th>
                        <th className="text-right pb-2">Push</th>
                        <th className="text-right pb-2">{jogo.away?.name}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {LINHAS_HANDICAP.map((linha) => {
                        const h = mercadosGols[`handicap_${rotuloLinha(linha)}`];
                        return (
                          <tr key={linha} className="border-t border-slate-800">
                            <td className="py-1.5 text-slate-300 font-mono">{linha > 0 ? `+${rotuloLinha(linha)}` : rotuloLinha(linha)}</td>
                            <td className="py-1.5 text-right font-mono text-emerald-400">{fmtPct(h?.home)}</td>
                            <td className="py-1.5 text-right font-mono text-slate-500">{h?.push ? fmtPct(h.push) : '—'}</td>
                            <td className="py-1.5 text-right font-mono text-orange-400">{fmtPct(h?.away)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Secao>

              <Secao titulo="Placar exato (mais prováveis)" icone={Target}>
                <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
                  {Object.entries(mercadosGols.placar_exato).slice(0, N_PLACARES_EXATOS).map(([placar, p]) => (
                    <CardParametro key={placar} label={placar} valor={fmtPct(p)} />
                  ))}
                </div>
              </Secao>

              <GradeMatriz
                titulo={`Matriz de placar (${jogo.home?.name || 'casa'} × ${jogo.away?.name || 'fora'}, 0-${MAX_GOLS_GRADE})`}
                matriz={gradePlacar}
                rotuloCasa={jogo.home?.name?.slice(0, 12) || 'Casa'}
                rotuloFora={jogo.away?.name?.slice(0, 12) || 'Fora'}
              />
            </>
          )}

          {mercadosCorners && (
            <>
              <Secao titulo="Escanteios — Over / Under (total)" icone={TrendingUp}>
                {LINHAS_OU_CORNERS.map((linha) => {
                  const m = mercadosCorners[`corners_over_under_${rotuloLinha(linha)}`];
                  return <LinhaBinaria key={linha} rotulo={rotuloLinha(linha)} over={m?.over} under={m?.under} />;
                })}
              </Secao>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                <Secao titulo="Escanteios — faixa & 1X2" icone={Target}>
                  <div className="grid grid-cols-2 gap-2 mb-3">
                    {Object.entries(mercadosCorners.faixa_corners).map(([faixa, p]) => (
                      <CardParametro key={faixa} label={`${faixa} escanteios`} valor={fmtPct(p)} />
                    ))}
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <CardParametro label="Mais escanteios: casa" valor={fmtPct(mercadosCorners.corners_1X2.home)} />
                    <CardParametro label="Empate" valor={fmtPct(mercadosCorners.corners_1X2.draw)} />
                    <CardParametro label="Mais escanteios: fora" valor={fmtPct(mercadosCorners.corners_1X2.away)} />
                  </div>
                </Secao>

                <Secao titulo="Escanteios por time" icone={Percent}>
                  {[3.5, 4.5, 5.5, 6.5].map((linha) => (
                    <div key={linha} className="mb-3 last:mb-0">
                      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Linha {rotuloLinha(linha)}</p>
                      <LinhaBinaria
                        rotulo={jogo.home?.name?.slice(0, 10) || 'Casa'}
                        over={mercadosCorners[`corners_home_over_under_${rotuloLinha(linha)}`]?.over}
                        under={mercadosCorners[`corners_home_over_under_${rotuloLinha(linha)}`]?.under}
                      />
                      <LinhaBinaria
                        rotulo={jogo.away?.name?.slice(0, 10) || 'Fora'}
                        over={mercadosCorners[`corners_away_over_under_${rotuloLinha(linha)}`]?.over}
                        under={mercadosCorners[`corners_away_over_under_${rotuloLinha(linha)}`]?.under}
                      />
                    </div>
                  ))}
                </Secao>
              </div>

              <GradeMatriz
                titulo={`Matriz de escanteios (${jogo.home?.name || 'casa'} × ${jogo.away?.name || 'fora'}, 0-${MAX_CORNERS_GRADE})`}
                matriz={gradeCorners}
                rotuloCasa={jogo.home?.name?.slice(0, 12) || 'Casa'}
                rotuloFora={jogo.away?.name?.slice(0, 12) || 'Fora'}
              />
            </>
          )}
        </>
      )}

      {jogadorEstimativas.length > 0 && (
        <div className="mt-4">
          <SecaoJogadorMercados
            estimativas={jogadorEstimativas}
            homeTeamId={jogo.home?.id}
            homeNome={jogo.home?.name}
            awayNome={jogo.away?.name}
            matchDate={jogo.match_date}
          />
        </div>
      )}
    </div>
  );
}
