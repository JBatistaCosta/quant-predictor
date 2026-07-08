import React, { useState, useMemo, useRef, useEffect } from 'react';
import { Search, Filter, ChevronUp, ChevronDown, Calculator, BarChart3, ShieldCheck, Target, Zap, AlertTriangle, Crosshair, Activity, Flag, Scale, FileJson, Check, X, Camera, Loader2, PlayCircle, DollarSign, ScanLine, TrendingUp, Grid3x3 } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { selecoesData } from '../data/selecoes';
import {
  factorial, poisson, poissonCDF, DIXON_COLES_RHO, dixonColesTau,
  LIGA_MEDIA_MANDANTE, LIGA_MEDIA_VISITANTE, LIGA_MEDIA_GERAL, GAMMA_MANDANTE, GAMMA_VISITANTE,
  getPoissonRandom,
} from '../utils/poisson';
import { XT_GRID } from '../utils/xt';
import { toNumber, toOdd, toPct, getEloColor, heatColor } from '../utils/format';

// --- Funções Matemáticas Auxiliares (Poisson) ---

// --- Correção de Dixon-Coles (1997) para placares baixos ---
// rho calibrado com 314 jogos reais (StatsBomb: Copas 2022/2018, Euro 2024/2020,
// Copa América 2024, Copa Africana 2023) — ver calibration/dixon_coles_calibration.json
// para os dados brutos e calibration/calibrate_dixon_coles.py para o método.
// Simplificação do modelo original: usa lambda/mu médios da amostra inteira (não por
// time), então captura o efeito médio de correlação de placar baixo no futebol em
// geral, não uma correção específica de cada confronto.

// --- MÉDIAS DA LIGA (mesma calibração real, 314 jogos, 6 torneios) ---
// Usadas pra combinar xG próprio com xGA do adversário de forma MULTIPLICATIVA
// (Dixon-Coles), em vez de uma média simples, que "dilui" ataques/defesas muito
// fora da média. Também dá, de graça, um termo real de vantagem de mando de campo.

// Grade de Expected Threat (xT, Karun Singh 2018) — calibrada com 313 jogos reais
// (StatsBomb: Copas 2022/2018, Euro 2024/2020, Copa América 2024, Copa Africana 2023),
// 587 mil eventos posicionais. Só exibida como referência visual — não entra em
// nenhum cálculo do app (ver calibration/xt_calibration.json e xt_README.md).

// Gerador aleatório de Poisson (algoritmo de Knuth) para o Monte Carlo

// Converte um valor de input (texto, possivelmente vazio ou parcial como "-" ou ".")
// em número seguro para cálculo. Texto vazio/inválido vira 0 SÓ na hora de calcular —
// nunca no campo em si, para não "empurrar" um 0 de volta na caixa enquanto o usuário digita.

// --- PROMPT 1: extração de ESTATÍSTICAS (tabela "Estatística média") ---
const OCR_STATS_PROMPT = `Você é um extrator de dados de screenshots de apps de estatísticas de futebol (como Betano/SofaScore).
A imagem mostra uma tabela "Estatística média" com duas colunas de valores: a coluna da ESQUERDA pertence à equipe mandante (primeira bandeira, à esquerda no topo) e a coluna da DIREITA pertence à equipe visitante.

Extraia os dados e responda APENAS com um JSON válido, sem markdown, sem explicações, exatamente neste formato:
{
  "confronto": {
    "equipe_mandante": "NomeDaEquipe1",
    "equipe_visitante": "NomeDaEquipe2"
  },
  "estatistica_media": {
    "mandante": { "metricas": { "xg_gols_esperados": 0.0, "xga_xg_sofridos": 0.0, "chutes": 0.0, "chutes_no_gol": 0.0, "escanteios": 0.0 } },
    "visitante": { "metricas": { "xg_gols_esperados": 0.0, "xga_xg_sofridos": 0.0, "chutes": 0.0, "chutes_no_gol": 0.0, "escanteios": 0.0 } }
  }
}

Regras:
- "Gols esperados (xG)" -> xg_gols_esperados; "xG sofridos" -> xga_xg_sofridos; "Chutes" -> chutes; "Chutes no gol" -> chutes_no_gol; "Escanteios" -> escanteios
- Escreva os nomes das seleções em português (ex: "Austrália", "Egito", "Brasil").
- Se algum valor não estiver visível, use null.`;

// --- PROMPT 2: extração de ODDS da casa de apostas (página completa de mercados) ---
const OCR_ODDS_PROMPT = `Você é um extrator de odds de screenshots de casas de apostas (Betano, Bet365, etc.) de uma partida de futebol.
A imagem mostra vários mercados com suas odds decimais. Extraia o máximo de mercados visíveis e responda APENAS com um JSON válido, sem markdown, sem explicações, exatamente neste formato:
{
  "confronto": {
    "equipe_mandante": "NomeDaEquipe1",
    "equipe_visitante": "NomeDaEquipe2"
  },
  "odds": {
    "resultado_final": { "casa": null, "empate": null, "fora": null },
    "dupla_chance": { "casa_ou_empate": null, "casa_ou_fora": null, "empate_ou_fora": null },
    "ambas_marcam": { "sim": null, "nao": null },
    "total_gols": [
      { "linha": 1.5, "mais": null, "menos": null },
      { "linha": 2.5, "mais": null, "menos": null },
      { "linha": 3.5, "mais": null, "menos": null }
    ],
    "total_escanteios": [
      { "linha": 8.5, "mais": null, "menos": null }
    ],
    "resultado_correto": [
      { "placar": "1-0", "odd": null },
      { "placar": "2-0", "odd": null },
      { "placar": "0-0", "odd": null },
      { "placar": "1-1", "odd": null },
      { "placar": "0-1", "odd": null },
      { "placar": "0-2", "odd": null }
    ],
    "handicap_resultado_final": [
      { "linha": -1, "casa": null, "empate": null, "fora": null },
      { "linha": -2, "casa": null, "empate": null, "fora": null },
      { "linha": 1, "casa": null, "empate": null, "fora": null }
    ]
  }
}

Regras:
- "Resultado Final" / "1X2": casa = equipe mandante (esquerda), fora = equipe visitante.
- "Total de Gols": para cada linha visível (Mais de 1.5 / Menos de 1.5 etc.), crie um objeto {"linha", "mais", "menos"}. Inclua todas as linhas visíveis.
- "Escanteios" / "Total de Cantos": mesmo formato em total_escanteios.
- "Resultado Correto" / "Placar Exato": formato "golsMandante-golsVisitante" (ex: "2-1" = mandante fez 2, visitante fez 1). Inclua TODOS os placares visíveis na tabela, mesmo os de odd muito alta.
- "Handicap - Resultado Final" (tabela com 3 colunas: Mandante / Empate / Visitante para cada linha): a "linha" é SEMPRE relativa à equipe MANDANTE. Linha negativa (ex: -1) significa que a mandante começa "devendo" esse número de golos (precisa vencer por mais que |linha| golos para a coluna "casa" ganhar; vencer por exatamente |linha| golos ganha a coluna "empate"). Linha positiva (ex: +1) significa que a mandante já começa com essa vantagem de golos. Copie a linha exatamente como aparece na tabela (com o sinal), e os 3 valores de odd de cada linha (casa/empate/fora) na mesma ordem das colunas.
- "Ambas Marcam" / "Ambas as equipes marcam": sim/nao.
- Odds são números decimais como 1.87, 3.30, 5.25. Se um mercado não estiver visível ou estiver bloqueado, use null.
- Ignore mercados de jogadores, cartões, e impulsos/promoções (odds turbinadas).
- Escreva os nomes das equipes em português.`;

export default function AnaliseEvento() {
  const [activeTab, setActiveTab] = useState('calculator');

  // Database State
  const [searchTerm, setSearchTerm] = useState('');
  const [filterConfed, setFilterConfed] = useState('Todas');
  const [sortConfig, setSortConfig] = useState({ key: 'rating', direction: 'desc' });

  // Calculator State
  const [team1Id, setTeam1Id] = useState(36); // Portugal
  const [team2Id, setTeam2Id] = useState(32); // RD Congo
  // Os valores ficam como TEXTO (string) enquanto o usuário digita — isso permite
  // apagar a caixa inteira e ela ficar vazia, em vez de "saltar" para 0 sozinha.
  // A conversão para número só acontece no momento do cálculo (função toNumber).
  const [metrics, setMetrics] = useState({
    xg1: '2.44', xga1: '1.12', poss1: '60', shots1: '18.44', shotsOnTarget1: '6.25', corners1: '6.31',
    xg2: '1.64', xga2: '0.71', poss2: '40', shots2: '12.36', shotsOnTarget2: '3.33', corners2: '4.44'
  });
  const [results, setResults] = useState(null);
  const [autoLoadMsg, setAutoLoadMsg] = useState('');
  const [casaDeApostas, setCasaDeApostas] = useState('');
  const [predictionsLog, setPredictionsLog] = useState([]);
  const [logMsg, setLogMsg] = useState('');
  const [showCalibration, setShowCalibration] = useState(false);

  const [eloWeight, setEloWeight] = useState(50);
  const [showAllScores, setShowAllScores] = useState(false);
  const [customScore1, setCustomScore1] = useState('2');
  const [customScore2, setCustomScore2] = useState('0');

  // Handicap State
  const [handicapTeam, setHandicapTeam] = useState(1);
  const [handicapLine, setHandicapLine] = useState('1');

  // JSON Import State (unificado: aceita estatísticas e/ou odds, coladas juntas ou separadas)
  const [showJsonInput, setShowJsonInput] = useState(false);
  const [jsonInputData, setJsonInputData] = useState('');
  const [jsonError, setJsonError] = useState('');
  const [jsonSuccess, setJsonSuccess] = useState('');

  // OCR State (compartilhado pelos dois leitores)
  const [ocrLoading, setOcrLoading] = useState(false);
  const [ocrError, setOcrError] = useState('');
  const [ocrSuccess, setOcrSuccess] = useState('');
  const [ocrJsonPreview, setOcrJsonPreview] = useState('');
  const statsInputRef = useRef(null);
  const oddsInputRef = useRef(null);

  // Odds da casa importadas (para o scanner multi-Kelly)
  const [bookieOddsData, setBookieOddsData] = useState(null);

  // Monte Carlo & Kelly State
  const [bankroll, setBankroll] = useState('100');
  const [bookieOdd, setBookieOdd] = useState('1.85');
  const [kellyFraction, setKellyFraction] = useState(0.25);
  const [kellyTarget, setKellyTarget] = useState('1');
  const [mcResults, setMcResults] = useState(null);
  const [kellyRecommendation, setKellyRecommendation] = useState(null);
  const [mcRunning, setMcRunning] = useState(false);

  // Simulação por Cadeia de Markov (minuto a minuto)
  const [markovDynamics, setMarkovDynamics] = useState(false);
  const [dixonColesEnabled, setDixonColesEnabled] = useState(true);
  const [xgMultiplicativo, setXgMultiplicativo] = useState(true);
  const [markovSimCount, setMarkovSimCount] = useState(20000);
  const [markovResults, setMarkovResults] = useState(null);
  const [markovHeatDisplay, setMarkovHeatDisplay] = useState('pct'); // 'pct' ou 'odd'
  const [markovRunning, setMarkovRunning] = useState(false);
  const [showOnlyEvPlus, setShowOnlyEvPlus] = useState(false);

  const SIMULATIONS = 10000;
  const confederacoes = ['Todas', 'UEFA', 'CONMEBOL', 'CONCACAF', 'AFC', 'CAF', 'OFC'];

  const handleSort = (key) => {
    let direction = 'asc';
    if (sortConfig.key === key && sortConfig.direction === 'asc') direction = 'desc';
    setSortConfig({ key, direction });
  };

  const filteredAndSortedTeams = useMemo(() => {
    let filtered = selecoesData.filter((t) =>
      t.name.toLowerCase().includes(searchTerm.toLowerCase()) &&
      (filterConfed === 'Todas' || t.confederation === filterConfed)
    );
    filtered.sort((a, b) => {
      if (a[sortConfig.key] < b[sortConfig.key]) return sortConfig.direction === 'asc' ? -1 : 1;
      if (a[sortConfig.key] > b[sortConfig.key]) return sortConfig.direction === 'asc' ? 1 : -1;
      return 0;
    });
    return filtered;
  }, [searchTerm, filterConfed, sortConfig]);

  const handleMetricChange = (field, value) => {
    setMetrics(prev => {
      const next = { ...prev, [field]: value };
      // A posse complementar (Equipa 2 = 100 - Equipa 1) só é recalculada quando o
      // texto digitado já forma um número válido — assim, campo vazio ou "em digitação"
      // (ex: "6" antes de virar "65") não força um 0/valor estranho no campo irmão.
      const parsed = parseFloat(value);
      if (Number.isFinite(parsed)) {
        if (field === 'poss1') next.poss2 = String(Math.max(0, 100 - parsed));
        if (field === 'poss2') next.poss1 = String(Math.max(0, 100 - parsed));
      }
      return next;
    });
  };

  // --- Aplica JSON de ESTATÍSTICAS (importador manual OU leitor de imagem) ---
  const applyParsedData = (parsedData) => {
    const nameT1 = parsedData.confronto?.equipe_mandante;
    const nameT2 = parsedData.confronto?.equipe_visitante;

    let matchedT1 = false;
    let matchedT2 = false;

    if (nameT1) {
      const t1 = selecoesData.find(t => t.name.toLowerCase() === nameT1.toLowerCase());
      if (t1) { setTeam1Id(t1.id); matchedT1 = true; }
    }
    if (nameT2) {
      const t2 = selecoesData.find(t => t.name.toLowerCase() === nameT2.toLowerCase());
      if (t2) { setTeam2Id(t2.id); matchedT2 = true; }
    }

    const mStats = parsedData.estatistica_media?.mandante?.metricas;
    const vStats = parsedData.estatistica_media?.visitante?.metricas;

    if (!mStats || !vStats) {
      throw new Error('Formato de JSON não reconhecido.');
    }

    setMetrics(prev => ({
      xg1: mStats.xg_gols_esperados != null ? String(mStats.xg_gols_esperados) : prev.xg1,
      xga1: mStats.xga_xg_sofridos != null ? String(mStats.xga_xg_sofridos) : prev.xga1,
      poss1: '50',
      shots1: mStats.chutes != null ? String(mStats.chutes) : prev.shots1,
      shotsOnTarget1: mStats.chutes_no_gol != null ? String(mStats.chutes_no_gol) : prev.shotsOnTarget1,
      corners1: mStats.escanteios != null ? String(mStats.escanteios) : prev.corners1,

      xg2: vStats.xg_gols_esperados != null ? String(vStats.xg_gols_esperados) : prev.xg2,
      xga2: vStats.xga_xg_sofridos != null ? String(vStats.xga_xg_sofridos) : prev.xga2,
      poss2: '50',
      shots2: vStats.chutes != null ? String(vStats.chutes) : prev.shots2,
      shotsOnTarget2: vStats.chutes_no_gol != null ? String(vStats.chutes_no_gol) : prev.shotsOnTarget2,
      corners2: vStats.escanteios != null ? String(vStats.escanteios) : prev.corners2,
    }));

    return { matchedT1, matchedT2 };
  };

  // --- Salva no Supabase (histórico persistente) — "dispara e esquece": não trava a
  // interface esperando resposta, e se o Supabase não estiver configurado, não faz nada. ---
  const salvarStatsNoSupabase = async (nomeA, nomeB, mStats, vStats) => {
    if (!supabaseAtivo) return;
    const linhas = [
      { team_name: nomeA, xg: mStats.xg_gols_esperados, xga: mStats.xga_xg_sofridos, chutes: mStats.chutes, chutes_no_gol: mStats.chutes_no_gol, escanteios: mStats.escanteios, updated_at: new Date().toISOString() },
      { team_name: nomeB, xg: vStats.xg_gols_esperados, xga: vStats.xga_xg_sofridos, chutes: vStats.chutes, chutes_no_gol: vStats.chutes_no_gol, escanteios: vStats.escanteios, updated_at: new Date().toISOString() },
    ];
    const { error } = await supabase.from('team_stats').upsert(linhas);
    if (error) console.warn('Falha ao salvar estatísticas no Supabase:', error.message);
  };

  const salvarOddsNoSupabase = async (parsed) => {
    if (!supabaseAtivo) return;
    const { error } = await supabase.from('match_odds').upsert({
      equipe_mandante: parsed.confronto?.equipe_mandante,
      equipe_visitante: parsed.confronto?.equipe_visitante,
      odds: parsed.odds,
      casa_de_apostas: casaDeApostas || null,
      updated_at: new Date().toISOString(),
    }, { onConflict: 'equipe_mandante,equipe_visitante' });
    if (error) console.warn('Falha ao salvar odds no Supabase:', error.message);
  };

  // Registra as 3 probabilidades do resultado final (casa/empate/fora) no histórico de
  // calibração — cada uma vira uma "aposta" separada de "quando eu digo X%, acontece X%?"
  const registrarPrevisao = async () => {
    if (!supabaseAtivo || !results) return;
    const odds = bookieOddsData?.odds?.resultado_final;
    const linhas = [
      { equipe_mandante: results.t1.name, equipe_visitante: results.t2.name, mercado: 'resultado_final', outcome: 'casa', probabilidade: results.probWin1, odd_oferecida: odds?.casa ?? null },
      { equipe_mandante: results.t1.name, equipe_visitante: results.t2.name, mercado: 'resultado_final', outcome: 'empate', probabilidade: results.probDraw, odd_oferecida: odds?.empate ?? null },
      { equipe_mandante: results.t1.name, equipe_visitante: results.t2.name, mercado: 'resultado_final', outcome: 'fora', probabilidade: results.probWin2, odd_oferecida: odds?.fora ?? null },
    ];
    const { error } = await supabase.from('predictions_log').insert(linhas);
    if (error) { setLogMsg('Erro ao registrar: ' + error.message); return; }
    setLogMsg(`Previsão registrada (${results.t1.name} x ${results.t2.name}) — 3 linhas, uma por resultado possível.`);
    setTimeout(() => setLogMsg(''), 4000);
    buscarPredictionsLog();
  };

  const buscarPredictionsLog = async () => {
    if (!supabaseAtivo) return;
    const { data, error } = await supabase.from('predictions_log').select('*').order('criado_em', { ascending: false });
    if (error) { console.warn('Falha ao buscar histórico:', error.message); return; }
    setPredictionsLog(data || []);
  };

  // Marca uma previsão pendente como "aconteceu" (true) ou "não aconteceu" (false)
  const resolverPrevisao = async (id, aconteceu) => {
    const { error } = await supabase.from('predictions_log')
      .update({ resultado: aconteceu, resolvido_em: new Date().toISOString() })
      .eq('id', id);
    if (error) { console.warn('Falha ao resolver previsão:', error.message); return; }
    buscarPredictionsLog();
  };

  useEffect(() => { if (showCalibration) buscarPredictionsLog(); }, [showCalibration]);

  // Monta o diagrama de confiabilidade: agrupa as previsões RESOLVIDAS em faixas de 10%
  // (0-10%, 10-20%, ..., 90-100%) e compara a probabilidade média prevista de cada faixa
  // com a frequência real de acerto naquela faixa.
  const calibracao = useMemo(() => {
    const resolvidas = predictionsLog.filter(p => p.resultado !== null);
    const faixas = Array.from({ length: 10 }, (_, i) => ({
      min: i / 10, max: (i + 1) / 10, label: `${i * 10}-${(i + 1) * 10}%`,
      previsoes: [],
    }));
    resolvidas.forEach(p => {
      const idx = Math.min(9, Math.floor(p.probabilidade * 10));
      faixas[idx].previsoes.push(p);
    });
    return faixas.map(f => {
      const n = f.previsoes.length;
      const mediaProvista = n > 0 ? f.previsoes.reduce((s, p) => s + p.probabilidade, 0) / n : null;
      const frequenciaReal = n > 0 ? f.previsoes.filter(p => p.resultado === true).length / n : null;
      return { ...f, n, mediaProvista, frequenciaReal };
    });
  }, [predictionsLog]);

  // Sempre que os times selecionados mudarem, busca no Supabase ANTES de pedir
  // pra colar o JSON de novo. Se já existir algo salvo, preenche sozinho.
  useEffect(() => {
    if (!supabaseAtivo) return;
    const t1 = selecoesData.find(t => t.id === team1Id);
    const t2 = selecoesData.find(t => t.id === team2Id);
    if (!t1 || !t2) return;

    let cancelado = false;
    (async () => {
      const mensagens = [];

      const { data: statsData, error: statsErro } = await supabase
        .from('team_stats')
        .select('*')
        .in('team_name', [t1.name, t2.name]);

      if (statsErro) { console.warn('Falha ao buscar estatísticas no Supabase:', statsErro.message); }
      else if (statsData && statsData.length > 0 && !cancelado) {
        const s1 = statsData.find(s => s.team_name === t1.name);
        const s2 = statsData.find(s => s.team_name === t2.name);
        if (s1 || s2) {
          setMetrics(prev => ({
            ...prev,
            xg1: s1 ? String(s1.xg) : prev.xg1,
            xga1: s1 ? String(s1.xga) : prev.xga1,
            shots1: s1?.chutes != null ? String(s1.chutes) : prev.shots1,
            shotsOnTarget1: s1?.chutes_no_gol != null ? String(s1.chutes_no_gol) : prev.shotsOnTarget1,
            corners1: s1?.escanteios != null ? String(s1.escanteios) : prev.corners1,
            xg2: s2 ? String(s2.xg) : prev.xg2,
            xga2: s2 ? String(s2.xga) : prev.xga2,
            shots2: s2?.chutes != null ? String(s2.chutes) : prev.shots2,
            shotsOnTarget2: s2?.chutes_no_gol != null ? String(s2.chutes_no_gol) : prev.shotsOnTarget2,
            corners2: s2?.escanteios != null ? String(s2.escanteios) : prev.corners2,
          }));
          mensagens.push(`Estatísticas carregadas do banco (${[s1 && t1.name, s2 && t2.name].filter(Boolean).join(', ')})`);
        }
      }

      const { data: oddsData, error: oddsErro } = await supabase
        .from('match_odds')
        .select('*')
        .eq('equipe_mandante', t1.name)
        .eq('equipe_visitante', t2.name)
        .maybeSingle();

      if (oddsErro) { console.warn('Falha ao buscar odds no Supabase:', oddsErro.message); }
      else if (oddsData && !cancelado) {
        setBookieOddsData({ confronto: { equipe_mandante: t1.name, equipe_visitante: t2.name }, odds: oddsData.odds });
        mensagens.push('Odds carregadas do banco');
      }

      if (mensagens.length > 0 && !cancelado) {
        setAutoLoadMsg(mensagens.join(' · '));
        setTimeout(() => setAutoLoadMsg(''), 5000);
      }
    })();

    return () => { cancelado = true; };
  }, [team1Id, team2Id]);

  // Conta quantas "seleções" (linhas de aposta) vieram no JSON de odds, só para feedback visual
  const countOddsMarkets = (parsedData) => {
    const o = parsedData.odds || {};
    return (
      (o.resultado_final ? 3 : 0) +
      (o.total_gols?.length || 0) * 2 +
      (o.total_escanteios?.length || 0) * 2 +
      (o.resultado_correto?.length || 0) +
      (o.ambas_marcam ? 2 : 0) +
      (o.dupla_chance ? 3 : 0) +
      (o.handicap_resultado_final?.length || 0) * 3
    );
  };

  // Varre um texto colado e extrai cada objeto JSON "de nível raiz" (equilibrando chaves { }),
  // ignorando chaves que apareçam dentro de strings. Isso permite colar DOIS JSONs seguidos
  // (ex: o de estatísticas logo em seguida do de odds) na mesma caixa de texto, mesmo que
  // isso não seja, sozinho, um JSON único válido.
  const extractJsonObjects = (text) => {
    const objects = [];
    let depth = 0;
    let start = -1;
    let inString = false;
    let escapeNext = false;

    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (escapeNext) { escapeNext = false; continue; }
      if (ch === '\\') { escapeNext = true; continue; }
      if (ch === '"') { inString = !inString; continue; }
      if (inString) continue;

      if (ch === '{') {
        if (depth === 0) start = i;
        depth++;
      } else if (ch === '}') {
        depth = Math.max(0, depth - 1);
        if (depth === 0 && start !== -1) {
          objects.push(text.slice(start, i + 1));
          start = -1;
        }
      }
    }
    return objects;
  };

  // --- IMPORTAÇÃO UNIFICADA: aceita estatísticas, odds, ou os dois colados juntos ---
  // Reconhece cada bloco automaticamente: se tem "estatistica_media" vira estatísticas,
  // se tem "odds" vira odds da casa. Não importa a ordem nem se veio só um dos dois.
  const handleImportJson = () => {
    setJsonError('');
    setJsonSuccess('');

    if (!jsonInputData.trim()) {
      setJsonError('Cole pelo menos um JSON (estatísticas e/ou odds) antes de importar.');
      return;
    }

    const rawBlocks = extractJsonObjects(jsonInputData);
    if (rawBlocks.length === 0) {
      setJsonError('Não encontrei nenhum JSON válido no texto colado (verifique se as chaves { } estão completas).');
      return;
    }

    let statsApplied = false;
    let oddsApplied = false;
    let statsMatchedBoth = true;
    let oddsMarketCount = 0;
    let oddsTeams = '';
    const blockErrors = [];

    rawBlocks.forEach((raw, idx) => {
      let parsed;
      try {
        parsed = JSON.parse(raw);
      } catch (e) {
        blockErrors.push(`Bloco ${idx + 1}: JSON inválido (verifique vírgulas e chaves).`);
        return;
      }

      if (parsed.estatistica_media) {
        try {
          const { matchedT1, matchedT2 } = applyParsedData(parsed);
          statsApplied = true;
          if (!matchedT1 || !matchedT2) statsMatchedBoth = false;
          const nomeA = parsed.confronto?.equipe_mandante;
          const nomeB = parsed.confronto?.equipe_visitante;
          const mStats = parsed.estatistica_media?.mandante?.metricas;
          const vStats = parsed.estatistica_media?.visitante?.metricas;
          if (nomeA && nomeB && mStats && vStats) salvarStatsNoSupabase(nomeA, nomeB, mStats, vStats);
        } catch (e) {
          blockErrors.push(`Bloco ${idx + 1} (estatísticas): ${e.message}`);
        }
      } else if (parsed.odds) {
        setBookieOddsData(parsed);
        oddsApplied = true;
        oddsMarketCount = countOddsMarkets(parsed);
        oddsTeams = `${parsed.confronto?.equipe_mandante || '?'} x ${parsed.confronto?.equipe_visitante || '?'}`;
        salvarOddsNoSupabase(parsed);
      } else {
        blockErrors.push(`Bloco ${idx + 1}: não reconheci como estatísticas nem odds (faltam os campos "estatistica_media" ou "odds").`);
      }
    });

    if (!statsApplied && !oddsApplied) {
      setJsonError(blockErrors.join(' ') || 'Nenhum dos JSONs colados foi reconhecido.');
      return;
    }

    const parts = [];
    if (statsApplied) {
      parts.push('Estatísticas importadas' + (!statsMatchedBoth ? ' (confira as equipas manualmente, nomes não corresponderam)' : ''));
    }
    if (oddsApplied) {
      parts.push(`Odds importadas (${oddsTeams}, ~${oddsMarketCount} seleções)`);
    }
    if (blockErrors.length) {
      parts.push(`Aviso: ${blockErrors.join(' ')}`);
    }
    setJsonSuccess(parts.join(' · '));

    setTimeout(() => {
      setShowJsonInput(false);
      setJsonInputData('');
      setJsonSuccess('');
    }, 4000);
  };

  // --- Passo A: normaliza QUALQUER imagem (foto de celular, HEIC, PNG gigante...) ---
  // Redesenha a imagem num <canvas> e exporta como JPEG redimensionado.
  // Isso evita falhas por formato incomum e reduz o tamanho do arquivo enviado.
  const normalizeImageToJpeg = (file, maxWidth = 1400, quality = 0.85) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const img = new window.Image();
      img.onload = () => {
        let { width, height } = img;
        if (width > maxWidth) {
          height = Math.round(height * (maxWidth / width));
          width = maxWidth;
        }
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, width, height);
        try {
          const dataUrl = canvas.toDataURL('image/jpeg', quality);
          resolve(dataUrl.split(',')[1]);
        } catch (e) {
          reject(new Error('Não foi possível converter a imagem (canvas bloqueado pelo navegador).'));
        }
      };
      img.onerror = () => reject(new Error('Formato de imagem não suportado pelo navegador. Tente exportar como JPG ou PNG antes de enviar.'));
      img.src = reader.result;
    };
    reader.onerror = () => reject(new Error('Falha ao ler o ficheiro do celular/computador.'));
    reader.readAsDataURL(file);
  });

  // --- Núcleo compartilhado do OCR: imagem -> base64 (normalizada) -> /api/ocr -> JSON ---
  // Repare que aqui NÃO chamamos api.anthropic.com diretamente.
  // Chamamos nosso próprio endpoint (/api/ocr), que roda no servidor do Vercel
  // e guarda a chave da API em segredo (arquivo api/ocr.js).
  const extractJsonFromImage = async (file, prompt) => {
    // Passo 1: normaliza a imagem (sempre vira JPEG, sempre redimensionada)
    const base64Data = await normalizeImageToJpeg(file);
    const mediaType = 'image/jpeg';

    // Passo 2: envia imagem + instruções para o NOSSO backend
    let response;
    try {
      response = await fetch('/api/ocr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: base64Data, mediaType, prompt })
      });
    } catch (networkErr) {
      throw new Error('Falha de rede ao contactar o servidor. Verifique a conexão e tente novamente.');
    }

    let data;
    try {
      data = await response.json();
    } catch (e) {
      throw new Error(`Resposta inválida da API (status ${response.status}).`);
    }

    // Passo 3: se a API retornou um erro explícito, mostra o motivo real
    if (!response.ok || data.error) {
      const msg = data?.error?.message || `status HTTP ${response.status}`;
      throw new Error(`Erro da API: ${msg}`);
    }

    // Passo 4: junta os blocos de texto e limpa possíveis cercas de markdown
    const rawText = (data.content || [])
      .filter(item => item.type === 'text')
      .map(item => item.text)
      .join('\n');

    if (!rawText.trim()) {
      throw new Error('A IA não devolveu texto. Tente novamente ou use outra imagem.');
    }

    const cleanText = rawText.replace(/```json|```/g, '').trim();

    try {
      return JSON.parse(cleanText);
    } catch (e) {
      throw new Error(`A IA não devolveu um JSON válido. Início da resposta: "${cleanText.slice(0, 120)}..."`);
    }
  };

  // --- LEITOR 1: Estatísticas (xG, chutes, escanteios...) ---
  const handleStatsImageUpload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setOcrError(''); setOcrSuccess(''); setOcrJsonPreview(''); setOcrLoading(true);

    try {
      const parsedData = await extractJsonFromImage(file, OCR_STATS_PROMPT);
      setOcrJsonPreview(JSON.stringify(parsedData, null, 2));
      const { matchedT1, matchedT2 } = applyParsedData(parsedData);

      let msg = `Estatísticas de ${parsedData.confronto?.equipe_mandante || 'Equipa 1'} x ${parsedData.confronto?.equipe_visitante || 'Equipa 2'} preenchidas!`;
      if (!matchedT1 || !matchedT2) {
        msg += ' Confira/selecione as equipas manualmente (nome não encontrado na base Elo).';
      }
      setOcrSuccess(msg);
    } catch (error) {
      console.error('Erro no leitor de estatísticas:', error);
      setOcrError(error.message || 'Não foi possível extrair as estatísticas. Tente uma captura mais nítida da tabela "Estatística média".');
    } finally {
      setOcrLoading(false);
      if (statsInputRef.current) statsInputRef.current.value = '';
    }
  };

  // --- LEITOR 2: Odds da casa de apostas (para o scanner multi-Kelly) ---
  const handleOddsImageUpload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setOcrError(''); setOcrSuccess(''); setOcrJsonPreview(''); setOcrLoading(true);

    try {
      const parsedData = await extractJsonFromImage(file, OCR_ODDS_PROMPT);

      if (!parsedData.odds) {
        throw new Error('Sem campo odds');
      }

      setOcrJsonPreview(JSON.stringify(parsedData, null, 2));
      setBookieOddsData(parsedData);

      const nMercados = countOddsMarkets(parsedData);

      setOcrSuccess(`Odds da casa importadas (${parsedData.confronto?.equipe_mandante || '?'} x ${parsedData.confronto?.equipe_visitante || '?'}, ~${nMercados} seleções mapeadas). Agora processe a precificação e o Scanner Kelly aparecerá nos resultados.`);
    } catch (error) {
      console.error('Erro no leitor de odds:', error);
      setOcrError(error.message || 'Não foi possível extrair as odds. Tente uma captura que mostre claramente os mercados (Resultado Final, Total de Gols, etc.).');
    } finally {
      setOcrLoading(false);
      if (oddsInputRef.current) oddsInputRef.current.value = '';
    }
  };

  const runAlgorithm = () => {
    const t1 = selecoesData.find(t => t.id === Number(team1Id));
    const t2 = selecoesData.find(t => t.id === Number(team2Id));

    // Converte os campos de texto para números só aqui, no momento do cálculo
    // (campo vazio ou "em digitação" vira 0, sem afetar o que está escrito na tela)
    const m = {
      xg1: toNumber(metrics.xg1), xga1: toNumber(metrics.xga1), poss1: toNumber(metrics.poss1),
      shots1: toNumber(metrics.shots1), shotsOnTarget1: toNumber(metrics.shotsOnTarget1), corners1: toNumber(metrics.corners1),
      xg2: toNumber(metrics.xg2), xga2: toNumber(metrics.xga2), poss2: toNumber(metrics.poss2),
      shots2: toNumber(metrics.shots2), shotsOnTarget2: toNumber(metrics.shotsOnTarget2), corners2: toNumber(metrics.corners2),
    };

    const rawEloDiff = t1.rating - t2.rating;
    const weightedEloDiff = rawEloDiff * (eloWeight / 100);

    const expectancyT1 = 1 / (1 + Math.pow(10, -weightedEloDiff / 400));
    const expectancyT2 = 1 - expectancyT1;

    const modPoss1 = 0.8 + (0.2 * (m.poss1 / 50));
    const modPoss2 = 0.8 + (0.2 * (m.poss2 / 50));

    const eff1 = m.shots1 > 0 ? m.xg1 / m.shots1 : 0;
    const eff2 = m.shots2 > 0 ? m.xg2 / m.shots2 : 0;

    // Combina xG próprio + xGA do adversário: multiplicativo (mais profissional, com
    // mando de campo real embutido) ou média simples (comportamento antigo, pra comparar).
    const trueXG_T1 = xgMultiplicativo
      ? (m.xg1 * m.xga2 / LIGA_MEDIA_GERAL) * GAMMA_MANDANTE
      : (m.xg1 + m.xga2) / 2;
    const trueXG_T2 = xgMultiplicativo
      ? (m.xg2 * m.xga1 / LIGA_MEDIA_GERAL) * GAMMA_VISITANTE
      : (m.xg2 + m.xga1) / 2;

    const lambda1 = Math.max(0.1, trueXG_T1 * (expectancyT1 / 0.5) * modPoss1);
    const lambda2 = Math.max(0.1, trueXG_T2 * (expectancyT2 / 0.5) * modPoss2);

    const maxGoals = 10;
    let probWin1 = 0, probWin2 = 0, probDraw = 0;
    let probOver25 = 0, probBtts = 0;
    const exactScores = [];

    for (let i = 0; i <= maxGoals; i++) {
      for (let j = 0; j <= maxGoals; j++) {
        const p1 = poisson(lambda1, i);
        const p2 = poisson(lambda2, j);
        let probMatrix = p1 * p2;

        // Correção de Dixon-Coles (1997): ajusta só as 4 células de placar baixo,
        // onde a suposição de independência entre os gols dos dois times falha um
        // pouco na prática (0-0/1-1 acontecem mais, 1-0/0-1 acontecem menos do que
        // a Poisson pura prevê). rho calibrado com 314 jogos reais — ver calibration/.
        if (dixonColesEnabled) {
          probMatrix *= dixonColesTau(i, j, lambda1, lambda2, DIXON_COLES_RHO);
        }

        exactScores.push({ score: `${i}-${j}`, prob: probMatrix, g1: i, g2: j });

        if (i > j) probWin1 += probMatrix;
        else if (i < j) probWin2 += probMatrix;
        else probDraw += probMatrix;

        if (i + j > 2.5) probOver25 += probMatrix;
        if (i > 0 && j > 0) probBtts += probMatrix;
      }
    }

    const total = probWin1 + probWin2 + probDraw;
    probWin1 /= total; probWin2 /= total; probDraw /= total;
    probBtts /= total;

    const lambdaCorners = m.corners1 + m.corners2;
    const probUnder85Corners = poissonCDF(lambdaCorners, 8);
    const probOver85Corners = 1 - probUnder85Corners;
    const probOver95Corners = 1 - poissonCDF(lambdaCorners, 9);

    let aiInsight = {};
    if (probWin1 > 0.70) {
      aiInsight = {
        type: 'Massacre Evidente', color: 'text-emerald-400', border: 'border-emerald-500/50',
        text: `O modelo projeta um domínio absoluto da equipa ${t1.name} com ${toPct(probWin1)} de chance de vitória. O mercado vai esmagar a odd simples. Use a calculadora de Handicap.`,
        seguranca: `Handicap Asiático com linha terminada em .0 (Proteção de Devolução)`,
        alavancagem: `Explorar placares exatos na "Cauda Longa" da Matriz.`
      };
    } else if (probWin1 > 0.55) {
      aiInsight = {
        type: 'Favoritismo Sólido', color: 'text-blue-400', border: 'border-blue-500/50',
        text: `A equipa ${t1.name} é a favorita matemática. O valor esperado está em blindar o capital contra variâncias e atacar o desequilíbrio ofensivo.`,
        seguranca: `Empate Anula a Aposta (DNB) ou Handicap Asiático 0.0: ${t1.name}`,
        alavancagem: `Vitória Simples (Match Odds) ou Handicap -1.0.`
      };
    } else if (probWin1 >= 0.40 && probWin1 <= 0.55 && probWin2 <= 0.55) {
      aiInsight = {
        type: 'Confronto Equilibrado (Cara ou Coroa)', color: 'text-yellow-400', border: 'border-yellow-500/50',
        text: `Jogo travado. A matemática aponta margem estreita. Fique longe de apostar no vencedor final sem cobertura. O mercado de golos é o porto seguro.`,
        seguranca: probOver25 > 0.5 ? 'Ambas Marcam: SIM ou Mais de 1.5 Golos' : 'Menos de 2.5 Golos ou Under',
        alavancagem: `Handicap Asiático +0.5 para o Azarão.`
      };
    } else {
      aiInsight = {
        type: 'Vantagem do Visitante/Azarão', color: 'text-orange-400', border: 'border-orange-500/50',
        text: `O modelo aponta vantagem severa para o(a) ${t2.name}. Cuidado com a "Odd Falsa" induzindo a apostar no Mandante pelo peso da camisola. Siga a matemática.`,
        seguranca: `Handicap Asiático +1.0 ou +1.5 para o ${t2.name}`,
        alavancagem: `Vitória Simples (Match Odds) ${t2.name}.`
      };
    }

    setResults({
      t1, t2, lambda1, lambda2, probWin1, probWin2, probDraw, probOver25, probBtts, aiInsight,
      eff1, eff2, lambdaCorners, probOver85Corners, probOver95Corners,
      exactScores: exactScores.sort((a, b) => b.prob - a.prob)
    });
    setShowAllScores(false);
    setMcResults(null);
    setKellyRecommendation(null);
    setMarkovResults(null);
  };

  // --- MOTOR MONTE CARLO + KELLY (mercado único, manual) ---
  const runMonteCarlo = () => {
    if (!results) return;
    setMcRunning(true);

    setTimeout(() => {
      const { lambda1, lambda2 } = results;
      let wins1 = 0, wins2 = 0, draws = 0;

      for (let i = 0; i < SIMULATIONS; i++) {
        const goals1 = getPoissonRandom(lambda1);
        const goals2 = getPoissonRandom(lambda2);
        if (goals1 > goals2) wins1++;
        else if (goals1 < goals2) wins2++;
        else draws++;
      }

      const probWin1 = wins1 / SIMULATIONS;
      const probDraw = draws / SIMULATIONS;
      const probWin2 = wins2 / SIMULATIONS;

      setMcResults({ probWin1, probDraw, probWin2 });

      let p;
      let targetLabel;
      if (kellyTarget === '1') { p = probWin1; targetLabel = results.t1.name; }
      else if (kellyTarget === 'X') { p = probDraw; targetLabel = 'Empate'; }
      else { p = probWin2; targetLabel = results.t2.name; }

      const bookieOddNum = toNumber(bookieOdd);
      const bankrollNum = toNumber(bankroll);

      const b = bookieOddNum - 1;
      const q = 1 - p;
      const f = b > 0 ? (p * b - q) / b : -1;

      if (f > 0) {
        const safeFraction = f * kellyFraction;
        const recommendedBet = bankrollNum * safeFraction;
        setKellyRecommendation({
          edge: true,
          target: targetLabel,
          fairOdd: p > 0 ? (1 / p).toFixed(2) : '—',
          stake: recommendedBet.toFixed(2),
          pct: (safeFraction * 100).toFixed(2),
          fullKelly: (f * 100).toFixed(2),
          message: `Vantagem matemática (EV+) detectada em "${targetLabel}" @ ${bookieOddNum.toFixed(2)}. Stake recomendada:`
        });
      } else {
        setKellyRecommendation({
          edge: false,
          target: targetLabel,
          fairOdd: p > 0 ? (1 / p).toFixed(2) : '—',
          stake: 0,
          pct: 0,
          message: `Aposta EV- em "${targetLabel}". A odd ${bookieOddNum.toFixed(2)} está abaixo da odd justa do modelo. A matemática manda NÃO apostar.`
        });
      }

      setMcRunning(false);
    }, 50);
  };

  const MARKOV_MINUTES = 90;

  // --- MULTIPLICADORES CALIBRADOS COM DADOS REAIS ---
  // Fonte: StatsBomb Open Data, FIFA World Cup 2022 (64 partidas, licença não-comercial).
  // Metodologia: para cada minuto de jogo, classificamos o estado de placar (do ponto de
  // vista de quem ataca) e medimos a taxa real de golos por minuto em cada estado,
  // dividida pela taxa média geral do torneio (1.4885 golos/90min).
  // Amostra pequena (64 jogos, torneio único e de mata-mata) — trate como um primeiro sinal
  // real, não como verdade definitiva. Script de recalibração: calibrate_markov.py.
  const MARKOV_STATE_MULTIPLIERS = {
    perdendo_2mais: 1.313, // atrás por 2+ golos: ataca mais desesperadamente
    perdendo_1: 0.945,     // atrás por 1 golo: quase neutro
    empatando: 0.768,      // empate: jogo mais cauteloso
    ganhando_1: 1.518,     // à frente por 1: ataca MAIS (contra-ataque, quem está atrás se expõe)
    ganhando_2mais: 1.376, // à frente por 2+: ainda ataca mais que a média
  };

  const getStateMultiplier = (diff) => {
    if (diff <= -2) return MARKOV_STATE_MULTIPLIERS.perdendo_2mais;
    if (diff === -1) return MARKOV_STATE_MULTIPLIERS.perdendo_1;
    if (diff === 0) return MARKOV_STATE_MULTIPLIERS.empatando;
    if (diff === 1) return MARKOV_STATE_MULTIPLIERS.ganhando_1;
    return MARKOV_STATE_MULTIPLIERS.ganhando_2mais;
  };

  // --- MULTIPLICADOR TEMPORAL (distribuição real de gols ao longo dos 90 minutos) ---
  // Mesma fonte e amostra do multiplicador de placar acima (StatsBomb, Copa 2022, 64 jogos).
  // Fato bem documentado no futebol: gols se concentram no fim de cada tempo (cansaço
  // defensivo + acréscimos). Os multiplicadores já vêm normalizados para média 1 ao longo
  // dos 90 minutos, então aplicá-los preserva o total esperado de golos do modelo (λ).
  const MARKOV_MINUTE_BINS = [
    { label: '0–14',  lo: 0,  hi: 14, mult: 0.455 },
    { label: '15–29', lo: 15, hi: 29, mult: 0.485 },
    { label: '30–44', lo: 30, hi: 44, mult: 0.939 },
    { label: '45–59', lo: 45, hi: 59, mult: 1.000 },
    { label: '60–74', lo: 60, hi: 74, mult: 0.939 },
    { label: '75–89', lo: 75, hi: 89, mult: 2.182 }, // inclui efeito de acréscimos/cansaço
  ];
  // Distribuição real observada (% dos golos em cada bin) — usada só para comparação visual
  const REAL_MINUTE_DISTRIBUTION = [0.076, 0.081, 0.157, 0.167, 0.157, 0.364];

  const getTimeMultiplier = (minute) => {
    const bin = MARKOV_MINUTE_BINS.find(b => minute >= b.lo && minute <= b.hi);
    return bin ? bin.mult : 1;
  };

  const getMinuteBinIndex = (minute) => {
    const idx = MARKOV_MINUTE_BINS.findIndex(b => minute >= b.lo && minute <= b.hi);
    return idx === -1 ? MARKOV_MINUTE_BINS.length - 1 : idx;
  };

  // --- MOTOR DE SIMULAÇÃO POR CADEIA DE MARKOV (minuto a minuto) ---
  // Diferença central para o Monte Carlo acima: aqui o jogo é simulado passo a passo
  // (90 "estados", um por minuto). A probabilidade de gol em cada minuto pode reagir
  // ao placar atual, usando os multiplicadores calibrados acima — algo que uma Poisson
  // bivariada simples não consegue representar.
  const runMarkovSimulation = () => {
    if (!results) return;
    setMarkovRunning(true);

    setTimeout(() => {
      const { lambda1, lambda2 } = results;
      // Taxa de gol "de base" por minuto (Poisson dividida igualmente pelos 90 minutos)
      const baseRate1 = lambda1 / MARKOV_MINUTES;
      const baseRate2 = lambda2 / MARKOV_MINUTES;

      let wins1 = 0, wins2 = 0, draws = 0;
      const scoreCounts = {};
      const goalMinuteBins = new Array(MARKOV_MINUTE_BINS.length).fill(0);

      for (let sim = 0; sim < markovSimCount; sim++) {
        let g1 = 0, g2 = 0;

        for (let minute = 0; minute < MARKOV_MINUTES; minute++) {
          let p1 = baseRate1;
          let p2 = baseRate2;

          if (markovDynamics) {
            p1 *= getStateMultiplier(g1 - g2) * getTimeMultiplier(minute);
            p2 *= getStateMultiplier(g2 - g1) * getTimeMultiplier(minute);
          }

          // Estado -> Próximo estado: no máximo 1 gol por equipa por minuto (aproximação razoável,
          // já que p1 e p2 são frações pequenas, tipicamente < 5% por minuto)
          const binIdx = getMinuteBinIndex(minute);
          if (Math.random() < p1) { g1++; goalMinuteBins[binIdx]++; }
          if (Math.random() < p2) { g2++; goalMinuteBins[binIdx]++; }
        }

        if (g1 > g2) wins1++;
        else if (g1 < g2) wins2++;
        else draws++;

        const key = `${g1}-${g2}`;
        scoreCounts[key] = (scoreCounts[key] || 0) + 1;
      }

      const topScores = Object.entries(scoreCounts)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6)
        .map(([score, count]) => ({ score, prob: count / markovSimCount }));

      const totalSimGoals = goalMinuteBins.reduce((a, b) => a + b, 0);
      const minuteDistribution = goalMinuteBins.map(c => totalSimGoals > 0 ? c / totalSimGoals : 0);

      // Matriz 7x7 (0 a 6 golos) com as probabilidades REAIS obtidas na simulação,
      // no mesmo formato do mapa de calor de Poisson lá em cima — permite comparar
      // "teoria" (Poisson puro) com "simulação" (que pode incluir a dinâmica calibrada).
      const heatGrid = [];
      let heatMin = Infinity, heatMax = -Infinity;
      for (let i = 0; i < 7; i++) {
        const row = [];
        for (let j = 0; j < 7; j++) {
          const count = scoreCounts[`${i}-${j}`] || 0;
          const p = count / markovSimCount;
          row.push(p);
          if (p < heatMin) heatMin = p;
          if (p > heatMax) heatMax = p;
        }
        heatGrid.push(row);
      }

      setMarkovResults({
        probWin1: wins1 / markovSimCount,
        probDraw: draws / markovSimCount,
        probWin2: wins2 / markovSimCount,
        topScores,
        minuteDistribution,
        heatGrid,
        heatMin,
        heatMax,
      });
      setMarkovRunning(false);
    }, 50);
  };


  const getCustomScoreData = () => {
    if (!results) return { prob: 0, odd: 0 };
    const k1 = Math.max(0, Math.round(toNumber(customScore1)));
    const k2 = Math.max(0, Math.round(toNumber(customScore2)));
    const prob = poisson(results.lambda1, k1) * poisson(results.lambda2, k2);
    return { prob, odd: toOdd(prob), pct: toPct(prob) };
  };

  const getHandicapData = () => {
    if (!results) return null;
    let pEH_Win = 0, pEH_Draw = 0;
    let pAH_Win_Int = 0, pAH_Push_Int = 0, pAH_Win_Half = 0;

    const H = parseInt(handicapLine) || 1;
    const isT1 = handicapTeam === 1;

    results.exactScores.forEach(s => {
      const margin = isT1 ? (s.g1 - s.g2) : (s.g2 - s.g1);
      if (margin > H) pEH_Win += s.prob;
      if (margin === H) pEH_Draw += s.prob;
      if (margin > H) pAH_Win_Int += s.prob;
      if (margin === H) pAH_Push_Int += s.prob;
      if (margin >= H) pAH_Win_Half += s.prob;
    });

    const probAH_Int_Effective = pAH_Push_Int < 1 ? pAH_Win_Int / (1 - pAH_Push_Int) : 0;

    return {
      teamName: isT1 ? results.t1.name : results.t2.name,
      H,
      oddEH_Win: toOdd(pEH_Win), probEH_Win: toPct(pEH_Win),
      oddEH_Draw: toOdd(pEH_Draw), probEH_Draw: toPct(pEH_Draw),
      oddAH_Int: toOdd(probAH_Int_Effective), probAH_Int: toPct(probAH_Int_Effective),
      oddAH_Half: toOdd(pAH_Win_Half), probAH_Half: toPct(pAH_Win_Half)
    };
  };

  const hcData = getHandicapData();

  // --- MAPA DE CALOR: matriz 7x7 (0 a 6 golos) de probabilidade por placar ---
  // Reaproveita a matriz de Poisson já calculada em results.exactScores.
  const heatmapData = useMemo(() => {
    if (!results) return null;
    const SIZE = 7; // golos de 0 a 6
    const grid = [];
    let min = Infinity, max = -Infinity;

    for (let i = 0; i < SIZE; i++) {
      const row = [];
      for (let j = 0; j < SIZE; j++) {
        const cell = results.exactScores.find(s => s.g1 === i && s.g2 === j);
        const p = cell ? cell.prob : 0;
        row.push(p);
        if (p < min) min = p;
        if (p > max) max = p;
      }
      grid.push(row);
    }
    return { grid, min, max, size: SIZE };
  }, [results]);

  // Interpola a cor de uma célula: 0% da escala = azul, 50% = amarelo, 100% (maior prob) = verde
  // --- SCANNER MULTI-MERCADO DE KELLY ---
  // Compara CADA odd importada da casa com a probabilidade do MODELO,
  // calcula o EV e a stake de Kelly de cada mercado, e ranqueia do melhor ao pior.
  const marketScan = useMemo(() => {
    if (!results || !bookieOddsData?.odds) return null;

    const list = [];
    const o = bookieOddsData.odds;

    // Função auxiliar: registra um mercado se a odd existir
    const push = (label, categoria, p, odd) => {
      const oddNum = Number(odd);
      if (!oddNum || oddNum <= 1 || !p || p <= 0 || p >= 1) return;
      const b = oddNum - 1;
      const q = 1 - p;
      const ev = p * oddNum - 1;              // Valor esperado por unidade apostada
      const kellyFull = (p * b - q) / b;      // Fração de Kelly bruta
      list.push({
        label, categoria,
        p, odd: oddNum,
        fairOdd: 1 / p,
        ev,
        kellyFull: Math.max(0, kellyFull),
      });
    };

    // Probabilidade de Mais de X golos a partir da matriz de placares
    const pOverGoals = (line) => {
      let sum = 0, tot = 0;
      results.exactScores.forEach(s => {
        tot += s.prob;
        if (s.g1 + s.g2 > line) sum += s.prob;
      });
      return tot > 0 ? sum / tot : 0;
    };

    // 1X2
    if (o.resultado_final) {
      push(`Vitória ${results.t1.name}`, 'Resultado Final', results.probWin1, o.resultado_final.casa);
      push('Empate', 'Resultado Final', results.probDraw, o.resultado_final.empate);
      push(`Vitória ${results.t2.name}`, 'Resultado Final', results.probWin2, o.resultado_final.fora);
    }

    // Dupla Chance
    if (o.dupla_chance) {
      push(`${results.t1.name} ou Empate`, 'Dupla Chance', results.probWin1 + results.probDraw, o.dupla_chance.casa_ou_empate);
      push(`${results.t1.name} ou ${results.t2.name}`, 'Dupla Chance', results.probWin1 + results.probWin2, o.dupla_chance.casa_ou_fora);
      push(`Empate ou ${results.t2.name}`, 'Dupla Chance', results.probDraw + results.probWin2, o.dupla_chance.empate_ou_fora);
    }

    // Ambas Marcam
    if (o.ambas_marcam) {
      push('Ambas Marcam: SIM', 'Ambas Marcam', results.probBtts, o.ambas_marcam.sim);
      push('Ambas Marcam: NÃO', 'Ambas Marcam', 1 - results.probBtts, o.ambas_marcam.nao);
    }

    // Total de Golos (linhas dinâmicas)
    (o.total_gols || []).forEach(l => {
      if (l?.linha == null) return;
      const pOver = pOverGoals(l.linha);
      push(`Mais de ${l.linha} golos`, 'Total de Golos', pOver, l.mais);
      push(`Menos de ${l.linha} golos`, 'Total de Golos', 1 - pOver, l.menos);
    });

    // Total de Escanteios (Poisson dos cantos combinados)
    (o.total_escanteios || []).forEach(l => {
      if (l?.linha == null) return;
      const pUnder = poissonCDF(results.lambdaCorners, Math.floor(l.linha));
      push(`Mais de ${l.linha} cantos`, 'Escanteios', 1 - pUnder, l.mais);
      push(`Menos de ${l.linha} cantos`, 'Escanteios', pUnder, l.menos);
    });

    // Placar Exato / Resultado Correto (compara cada placar diretamente com a matriz de Poisson)
    (o.resultado_correto || []).forEach(rc => {
      if (!rc?.placar || rc.odd == null) return;
      const match = results.exactScores.find(s => s.score === rc.placar);
      if (!match) return; // placar fora da matriz (ex: acima de 10 golos)
      push(`Placar Exato ${rc.placar}`, 'Placar Exato', match.prob, rc.odd);
    });

    // Handicap - Resultado Final (3 vias: casa cobre / empate na linha / fora cobre)
    // A "linha" é sempre relativa à equipa mandante: negativa = mandante precisa
    // vencer por mais gols que isso; positiva = mandante já começa com essa vantagem.
    // Convenção: margem_ajustada = (golos_casa - golos_fora) + linha.
    // "Casa" cobre se margem_ajustada > 0  <=>  margem > -linha.
    (o.handicap_resultado_final || []).forEach(h => {
      if (h?.linha == null) return;
      const H = Number(h.linha);
      const threshold = -H;
      let pWinCasa = 0, pDraw = 0;

      results.exactScores.forEach(s => {
        const margin = s.g1 - s.g2;
        if (margin > threshold) pWinCasa += s.prob;
        else if (margin === threshold) pDraw += s.prob;
      });
      const pWinFora = Math.max(0, 1 - pWinCasa - pDraw);

      const sinal = H > 0 ? `+${H}` : `${H}`;
      push(`Handicap ${results.t1.name} (${sinal})`, 'Handicap', pWinCasa, h.casa);
      push(`Handicap Empate (${sinal})`, 'Handicap', pDraw, h.empate);
      push(`Handicap ${results.t2.name} (${sinal})`, 'Handicap', pWinFora, h.fora);
    });

    // Ordena do maior EV para o menor
    list.sort((a, b) => b.ev - a.ev);
    return list;
  }, [results, bookieOddsData]);

  const evPlusCount = marketScan ? marketScan.filter(m => m.ev > 0).length : 0;

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 p-2 md:p-6 font-sans">
      <div className="max-w-7xl mx-auto space-y-6">

        {/* Header e Abas */}
        <div className="bg-slate-800 p-6 rounded-2xl border border-slate-700 shadow-xl">
          <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
            <div>
              <h1 className="text-2xl md:text-3xl font-extrabold flex items-center gap-3 text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-blue-500">
                <Zap className="text-emerald-400" size={32} />
                Quant System Predictor 18.0
              </h1>
              <p className="text-slate-400 mt-1 text-sm">Handicap Europeu (Resultado Final) integrado ao Scanner de Kelly.</p>
            </div>
            <div className="flex bg-slate-900 rounded-lg p-1 w-full md:w-auto border border-slate-700">
              <button
                onClick={() => setActiveTab('calculator')}
                className={`flex-1 md:flex-none px-6 py-2.5 rounded-md text-sm font-semibold transition-all flex items-center justify-center gap-2 ${activeTab === 'calculator' ? 'bg-emerald-500/20 text-emerald-400 shadow-sm' : 'text-slate-400 hover:text-slate-200'}`}
              >
                <Calculator size={18}/> Calculadora EV+
              </button>
              <button
                onClick={() => setActiveTab('database')}
                className={`flex-1 md:flex-none px-6 py-2.5 rounded-md text-sm font-semibold transition-all flex items-center justify-center gap-2 ${activeTab === 'database' ? 'bg-blue-500/20 text-blue-400 shadow-sm' : 'text-slate-400 hover:text-slate-200'}`}
              >
                <BarChart3 size={18}/> Elo Database
              </button>
            </div>
          </div>
        </div>

        {/* ABA: CALCULADORA */}
        {activeTab === 'calculator' && (
          <div className="space-y-6">

            <div className="bg-slate-800 rounded-2xl border border-slate-700 p-6 shadow-xl">
              <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center border-b border-slate-700 pb-4 mb-5 gap-4">
                <h2 className="text-lg font-bold flex items-center gap-2">
                  <Target className="text-blue-400"/> Ingestão de Dados Avançada
                </h2>

                <div className="flex flex-col sm:flex-row gap-2 w-full lg:w-auto">
                  {/* LEITOR 1: ESTATÍSTICAS */}
                  <label className={`flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-colors cursor-pointer ${ocrLoading ? 'bg-slate-700 text-slate-400 cursor-wait' : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-500/20'}`}>
                    {ocrLoading ? <Loader2 size={16} className="animate-spin"/> : <Camera size={16}/>}
                    Ler Estatísticas (OCR)
                    <input
                      ref={statsInputRef}
                      type="file"
                      accept="image/*"
                      className="hidden"
                      disabled={ocrLoading}
                      onChange={handleStatsImageUpload}
                    />
                  </label>

                  {/* LEITOR 2: ODDS DA CASA */}
                  <label className={`flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-colors cursor-pointer ${ocrLoading ? 'bg-slate-700 text-slate-400 cursor-wait' : 'bg-purple-600 hover:bg-purple-500 text-white shadow-lg shadow-purple-500/20'}`}>
                    {ocrLoading ? <Loader2 size={16} className="animate-spin"/> : <ScanLine size={16}/>}
                    Ler Odds da Casa (OCR)
                    <input
                      ref={oddsInputRef}
                      type="file"
                      accept="image/*"
                      className="hidden"
                      disabled={ocrLoading}
                      onChange={handleOddsImageUpload}
                    />
                  </label>

                  <button
                    onClick={() => setShowJsonInput(!showJsonInput)}
                    className={`flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-colors ${showJsonInput ? 'bg-slate-700 text-slate-200' : 'bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-500/20'}`}
                  >
                    <FileJson size={16}/> Colar JSON
                  </button>
                </div>
              </div>

              {autoLoadMsg && (
                <div className="mb-4 flex items-center gap-2 bg-emerald-950/30 border border-emerald-600/40 text-emerald-300 text-xs font-bold px-3 py-2 rounded-lg">
                  <Check size={14}/> {autoLoadMsg} (do Supabase — não precisou colar de novo)
                </div>
              )}

              <div className="mb-4">
                <label className="block text-[10px] uppercase text-slate-500 font-bold mb-1">Casa de apostas (opcional, salvo junto com as odds)</label>
                <input
                  type="text"
                  value={casaDeApostas}
                  onChange={(e) => setCasaDeApostas(e.target.value)}
                  placeholder="Ex: Betano, Bet365..."
                  className="w-full max-w-xs bg-slate-900 border border-slate-600 rounded-md p-2 text-sm text-slate-100"
                />
              </div>

              {/* Status das odds importadas */}
              {bookieOddsData && (
                <div className="mb-5 p-3 rounded-xl border border-purple-500/30 bg-purple-950/20 text-sm flex items-center justify-between gap-2 flex-wrap">
                  <span className="text-purple-300 flex items-center gap-2">
                    <ScanLine size={14}/>
                    Odds da casa carregadas: <strong>{bookieOddsData.confronto?.equipe_mandante || '?'} x {bookieOddsData.confronto?.equipe_visitante || '?'}</strong>
                  </span>
                  <button onClick={() => setBookieOddsData(null)} className="text-xs text-slate-400 hover:text-red-400 flex items-center gap-1"><X size={12}/> Remover</button>
                </div>
              )}

              {/* Feedback do OCR */}
              {(ocrError || ocrSuccess) && (
                <div className={`mb-5 p-4 rounded-xl border text-sm flex items-start gap-2 ${ocrError ? 'bg-red-950/30 border-red-500/40 text-red-400' : 'bg-emerald-950/30 border-emerald-500/40 text-emerald-400'}`}>
                  {ocrError ? <X size={16} className="mt-0.5 shrink-0"/> : <Check size={16} className="mt-0.5 shrink-0"/>}
                  <span>{ocrError || ocrSuccess}</span>
                </div>
              )}

              {/* Preview do JSON gerado pelo OCR */}
              {ocrJsonPreview && (
                <div className="mb-6 p-4 bg-slate-950 border border-emerald-500/20 rounded-xl">
                  <div className="flex justify-between items-center mb-2">
                    <span className="text-xs font-bold text-emerald-400 uppercase tracking-wider flex items-center gap-1"><FileJson size={12}/> JSON Gerado pela IA</span>
                    <button onClick={() => setOcrJsonPreview('')} className="text-slate-500 hover:text-slate-300 text-xs">Ocultar</button>
                  </div>
                  <pre className="text-[11px] text-emerald-300/80 font-mono overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap">{ocrJsonPreview}</pre>
                </div>
              )}

              {/* Área de Importação JSON unificada — aceita estatísticas, odds, ou os dois juntos */}
              {showJsonInput && (
                <div className="mb-8 p-5 bg-slate-900 border border-blue-500/30 rounded-xl animate-in slide-in-from-top-2 duration-200">
                  <label className="block text-sm font-bold text-blue-400 mb-2">
                    Cole aqui o JSON de estatísticas, o de odds, ou os dois colados um após o outro:
                  </label>
                  <textarea
                    className="w-full h-48 bg-slate-950 border border-slate-700 rounded-lg p-3 text-sm text-emerald-400 font-mono outline-none focus:border-blue-500"
                    placeholder={'{"confronto": {"equipe_mandante": "Portugal"...}, "estatistica_media": {...}}\n\n{"confronto": {...}, "odds": {"resultado_final": {...}}}'}
                    value={jsonInputData}
                    onChange={(e) => setJsonInputData(e.target.value)}
                  ></textarea>
                  <p className="text-[11px] text-slate-500 mt-2">
                    O app reconhece sozinho cada bloco: quem tem <code className="text-blue-300">"estatistica_media"</code> vira
                    estatísticas, quem tem <code className="text-purple-300">"odds"</code> vira odds da casa. Não precisa se preocupar
                    com a ordem, nem colar os dois — um só também funciona.
                  </p>

                  <div className="flex items-center justify-between mt-3 gap-3 flex-wrap">
                    <div className="text-sm">
                      {jsonError && <span className="text-red-400 flex items-center gap-1"><X size={14}/> {jsonError}</span>}
                      {jsonSuccess && <span className="text-emerald-400 flex items-center gap-1"><Check size={14}/> {jsonSuccess}</span>}
                    </div>
                    <button
                      onClick={handleImportJson}
                      className="bg-blue-600 hover:bg-blue-500 text-white px-5 py-2 rounded-lg font-bold text-sm transition-colors"
                    >
                      Processar JSON
                    </button>
                  </div>
                </div>
              )}

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                {/* Seleção 1 */}
                <div className="space-y-4 bg-slate-900 p-5 rounded-xl border border-emerald-500/30 shadow-lg relative overflow-hidden">
                  <div className="absolute top-0 right-0 p-2 opacity-5"><Target size={80}/></div>
                  <label className="block text-sm font-bold text-emerald-400 uppercase tracking-wide relative z-10">Equipa 1 (Mandante/Favorita)</label>
                  <select
                    className="w-full bg-slate-800 border border-slate-600 rounded-lg p-3 text-slate-100 outline-none font-semibold relative z-10"
                    value={team1Id} onChange={(e) => setTeam1Id(e.target.value)}
                  >
                    {selecoesData.map(t => <option key={`t1-${t.id}`} value={t.id}>{t.name} (Elo: {t.rating})</option>)}
                  </select>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2 border-t border-slate-700 relative z-10">
                    <div>
                      <label className="block text-[10px] uppercase text-emerald-400 mb-1">xG (Ataque)</label>
                      <input type="number" step="0.1" value={metrics.xg1} onChange={(e) => handleMetricChange('xg1', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-md p-2 text-sm text-slate-100" />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase text-red-400 mb-1">xGA (Defesa)</label>
                      <input type="number" step="0.1" value={metrics.xga1} onChange={(e) => handleMetricChange('xga1', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-md p-2 text-sm text-slate-100" />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase text-blue-400 mb-1">Posse (%)</label>
                      <input type="number" value={metrics.poss1} onChange={(e) => handleMetricChange('poss1', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-md p-2 text-sm text-slate-100" />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase text-slate-400 mb-1">Chutes</label>
                      <input type="number" step="0.1" value={metrics.shots1} onChange={(e) => handleMetricChange('shots1', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-md p-2 text-sm text-slate-100" />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase text-slate-400 mb-1">No Gol</label>
                      <input type="number" step="0.1" value={metrics.shotsOnTarget1} onChange={(e) => handleMetricChange('shotsOnTarget1', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-md p-2 text-sm text-slate-100" />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase text-yellow-400 mb-1">Cantos</label>
                      <input type="number" step="0.1" value={metrics.corners1} onChange={(e) => handleMetricChange('corners1', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-md p-2 text-sm text-slate-100" />
                    </div>
                  </div>
                </div>

                {/* Seleção 2 */}
                <div className="space-y-4 bg-slate-900 p-5 rounded-xl border border-orange-500/30 shadow-lg relative overflow-hidden">
                  <div className="absolute top-0 right-0 p-2 opacity-5"><Target size={80}/></div>
                  <label className="block text-sm font-bold text-orange-400 uppercase tracking-wide relative z-10">Equipa 2 (Visitante/Azarão)</label>
                  <select
                    className="w-full bg-slate-800 border border-slate-600 rounded-lg p-3 text-slate-100 outline-none font-semibold relative z-10"
                    value={team2Id} onChange={(e) => setTeam2Id(e.target.value)}
                  >
                    {selecoesData.map(t => <option key={`t2-${t.id}`} value={t.id}>{t.name} (Elo: {t.rating})</option>)}
                  </select>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2 border-t border-slate-700 relative z-10">
                    <div>
                      <label className="block text-[10px] uppercase text-emerald-400 mb-1">xG (Ataque)</label>
                      <input type="number" step="0.1" value={metrics.xg2} onChange={(e) => handleMetricChange('xg2', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-md p-2 text-sm text-slate-100" />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase text-red-400 mb-1">xGA (Defesa)</label>
                      <input type="number" step="0.1" value={metrics.xga2} onChange={(e) => handleMetricChange('xga2', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-md p-2 text-sm text-slate-100" />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase text-blue-400 mb-1">Posse (%)</label>
                      <input type="number" value={metrics.poss2} readOnly className="w-full bg-slate-800 border border-slate-600 rounded-md p-2 text-sm text-slate-500 opacity-70 cursor-not-allowed" />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase text-slate-400 mb-1">Chutes</label>
                      <input type="number" step="0.1" value={metrics.shots2} onChange={(e) => handleMetricChange('shots2', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-md p-2 text-sm text-slate-100" />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase text-slate-400 mb-1">No Gol</label>
                      <input type="number" step="0.1" value={metrics.shotsOnTarget2} onChange={(e) => handleMetricChange('shotsOnTarget2', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-md p-2 text-sm text-slate-100" />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase text-yellow-400 mb-1">Cantos</label>
                      <input type="number" step="0.1" value={metrics.corners2} onChange={(e) => handleMetricChange('corners2', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-md p-2 text-sm text-slate-100" />
                    </div>
                  </div>
                </div>
              </div>

              {/* SLIDER DE PONDERAÇÃO ELO */}
              <div className="bg-slate-900 border border-slate-700 p-4 rounded-xl mt-6">
                <div className="flex justify-between items-center mb-2">
                   <span className="text-sm font-bold text-slate-200 flex items-center gap-2"><Scale size={16} className="text-emerald-400"/> Peso do Histórico (Elo Rating)</span>
                   <span className="text-emerald-400 font-mono font-bold bg-emerald-500/20 px-2 py-0.5 rounded">{eloWeight}%</span>
                </div>
                <input
                  type="range"
                  min="0" max="100"
                  value={eloWeight}
                  onChange={(e) => setEloWeight(Number(e.target.value))}
                  className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-emerald-500"
                />
                <div className="flex justify-between text-xs text-slate-400 mt-2 font-medium">
                  <span>0% (Apenas Estatística Recente)</span>
                  <span className="text-slate-300">50% (Cenário Médio Híbrido)</span>
                  <span>100% (Ditadura do Elo)</span>
                </div>
              </div>

              <label className="flex items-center gap-3 bg-slate-900 border border-slate-700 rounded-xl p-4 mt-4 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={xgMultiplicativo}
                  onChange={(e) => setXgMultiplicativo(e.target.checked)}
                  className="w-5 h-5 accent-emerald-500"
                />
                <div>
                  <span className="text-sm font-bold text-slate-200 block">Combinação de xG Multiplicativa (com mando de campo real)</span>
                  <span className="text-xs text-slate-500">
                    Desligado: xG próprio + xGA do adversário viram uma MÉDIA simples (comportamento antigo — "dilui"
                    ataques/defesas muito fora do normal). Ligado: combinação multiplicativa (mesmo espírito do
                    Dixon-Coles profissional), com vantagem de mando de campo real embutida — casa ×1,10, fora ×0,90,
                    calibrado com os mesmos 314 jogos reais. Times muito acima ou abaixo da média ficam com previsões
                    mais decisivas, em vez de "amassadas" pela média do adversário.
                  </span>
                </div>
              </label>

              <label className="flex items-center gap-3 bg-slate-900 border border-slate-700 rounded-xl p-4 mt-4 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={dixonColesEnabled}
                  onChange={(e) => setDixonColesEnabled(e.target.checked)}
                  className="w-5 h-5 accent-emerald-500"
                />
                <div>
                  <span className="text-sm font-bold text-slate-200 block">Correção Dixon-Coles (placares baixos, calibrada com dados reais)</span>
                  <span className="text-xs text-slate-500">
                    Desligado: Poisson pura (assume os dois times marcam de forma totalmente independente).
                    Ligado: ajusta 0-0, 1-0, 0-1 e 1-1 com ρ = −0,042, calibrado com 314 jogos reais (Copas do
                    Mundo 2022/2018, Euro 2024/2020, Copa América 2024, Copa Africana 2023) — 0-0 e 1-1 ficam
                    um pouco mais prováveis, 1-0 e 0-1 um pouco menos.
                  </span>
                </div>
              </label>

              <button
                onClick={runAlgorithm}
                className="mt-6 w-full bg-gradient-to-r from-emerald-600 to-blue-600 hover:from-emerald-500 hover:to-blue-500 text-white font-bold py-4 rounded-xl transition-all shadow-lg shadow-emerald-500/20 flex items-center justify-center gap-2 text-lg"
              >
                <Calculator /> Processar Precificação & Odds Justas
              </button>
            </div>

            {results && (
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 animate-in fade-in zoom-in duration-300">

                <div className="lg:col-span-2 grid grid-cols-1 sm:grid-cols-3 gap-4 h-max">
                  <div className="col-span-3 bg-slate-800 p-4 rounded-xl border border-slate-700 flex justify-between items-center">
                    <span className="text-sm font-semibold text-slate-400 uppercase flex items-center gap-2">
                      Ajuste Definitivo de Força (λ)
                      <span className="text-[10px] bg-slate-700 text-slate-300 px-2 py-0.5 rounded">Peso Elo: {eloWeight}%</span>
                    </span>
                    <span className="text-lg font-mono">
                      <span className="text-emerald-400">{results.lambda1.toFixed(2)}</span> vs <span className="text-orange-400">{results.lambda2.toFixed(2)}</span>
                    </span>
                  </div>

                  <div className="bg-slate-900 border-t-4 border-emerald-500 p-5 rounded-xl border border-slate-700 flex flex-col items-center justify-center text-center">
                    <span className="text-slate-400 text-[10px] uppercase tracking-wider mb-2 line-clamp-1">{results.t1.name}</span>
                    <span className="text-3xl font-black text-white">{toOdd(results.probWin1)}</span>
                    <span className="text-xs text-emerald-400 mt-1 font-mono">{toPct(results.probWin1)}</span>
                  </div>
                  <div className="bg-slate-900 border-t-4 border-slate-500 p-5 rounded-xl border border-slate-700 flex flex-col items-center justify-center text-center">
                    <span className="text-slate-400 text-[10px] uppercase tracking-wider mb-2">Empate</span>
                    <span className="text-3xl font-black text-white">{toOdd(results.probDraw)}</span>
                    <span className="text-xs text-slate-400 mt-1 font-mono">{toPct(results.probDraw)}</span>
                  </div>
                  <div className="bg-slate-900 border-t-4 border-orange-500 p-5 rounded-xl border border-slate-700 flex flex-col items-center justify-center text-center">
                    <span className="text-slate-400 text-[10px] uppercase tracking-wider mb-2 line-clamp-1">{results.t2.name}</span>
                    <span className="text-3xl font-black text-white">{toOdd(results.probWin2)}</span>
                    <span className="text-xs text-orange-400 mt-1 font-mono">{toPct(results.probWin2)}</span>
                  </div>

                  <div className="col-span-3 grid grid-cols-1 sm:grid-cols-3 gap-4">
                     <div className="bg-slate-800 p-4 rounded-xl border border-slate-700 flex flex-col justify-center">
                        <span className="text-[10px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1"><Activity size={12}/> Eficiência (xG/Chute)</span>
                        <div className="flex justify-between items-end mt-1">
                          <span className="text-sm font-mono text-emerald-400">{results.eff1.toFixed(2)}</span>
                          <span className="text-sm font-mono text-orange-400">{results.eff2.toFixed(2)}</span>
                        </div>
                     </div>
                     <div className="bg-slate-800 p-4 rounded-xl border border-slate-700 flex justify-between items-center">
                        <div className="flex flex-col">
                          <span className="text-[10px] text-slate-400 uppercase tracking-wider">Over 2.5 Golos</span>
                          <span className="text-lg font-bold">{toOdd(results.probOver25)}</span>
                        </div>
                        <span className="text-xs text-blue-400 font-mono bg-slate-900 p-1.5 rounded">{toPct(results.probOver25)}</span>
                     </div>
                     <div className="bg-slate-800 p-4 rounded-xl border border-slate-700 flex justify-between items-center">
                        <div className="flex flex-col">
                          <span className="text-[10px] text-slate-400 uppercase tracking-wider">Over 8.5 Cantos</span>
                          <span className="text-lg font-bold text-yellow-400">{toOdd(results.probOver85Corners)}</span>
                        </div>
                        <span className="text-xs text-yellow-500 font-mono bg-slate-900 p-1.5 rounded">{toPct(results.probOver85Corners)}</span>
                     </div>
                  </div>

                  <div className={`col-span-3 bg-slate-900 border border-slate-700 p-5 rounded-xl relative overflow-hidden mt-2`}>
                    <div className={`absolute top-0 left-0 w-1 h-full bg-current ${results.aiInsight.color}`}></div>
                    <h3 className="flex items-center gap-2 text-sm font-bold uppercase tracking-wider mb-3 text-slate-100">
                      <AlertTriangle className={results.aiInsight.color} size={18} /> Leitura Tática & Quantitativa
                    </h3>
                    <p className="text-sm text-slate-400 mb-4 leading-relaxed">{results.aiInsight.text}</p>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div className="bg-slate-800/50 p-3 rounded-lg border border-slate-700/50">
                        <div className="flex items-center gap-1.5 text-xs font-bold text-blue-400 uppercase mb-1">
                          <ShieldCheck size={14}/> Hedge (Segurança)
                        </div>
                        <span className="text-sm font-medium text-slate-200">{results.aiInsight.seguranca}</span>
                      </div>
                      <div className="bg-slate-800/50 p-3 rounded-lg border border-slate-700/50">
                        <div className="flex items-center gap-1.5 text-xs font-bold text-emerald-400 uppercase mb-1">
                          <Target size={14}/> EV+ (Alavancagem)
                        </div>
                        <span className="text-sm font-medium text-slate-200">{results.aiInsight.alavancagem}</span>
                      </div>
                    </div>
                  </div>
                </div>

                {supabaseAtivo && (
                  <div className="col-span-3 flex items-center gap-3 flex-wrap bg-slate-900 border border-purple-500/30 rounded-xl p-4 mt-2">
                    <button
                      onClick={registrarPrevisao}
                      className="flex items-center gap-2 bg-purple-700 hover:bg-purple-600 text-white px-4 py-2 rounded-lg text-sm font-bold transition-colors"
                    >
                      <Target size={16}/> Registrar Previsão (Calibração)
                    </button>
                    <span className="text-xs text-slate-500 flex-1 min-w-[200px]">
                      Salva as 3 probabilidades (casa/empate/fora) no histórico. Quando o jogo acabar, volte e marque
                      o que realmente aconteceu — assim dá pra medir se o modelo está bem calibrado.
                    </span>
                    {logMsg && <span className="text-xs text-emerald-400 font-bold flex items-center gap-1"><Check size={13}/> {logMsg}</span>}
                    <button
                      onClick={() => setShowCalibration(!showCalibration)}
                      className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 border border-slate-600 text-slate-200 px-4 py-2 rounded-lg text-sm font-bold transition-colors"
                    >
                      <BarChart3 size={16}/> {showCalibration ? 'Ocultar' : 'Ver'} Histórico e Calibração
                    </button>
                  </div>
                )}

                <div className="space-y-4">
                  <div className="bg-slate-800 p-4 rounded-xl border border-slate-700">
                    <h3 className="text-xs font-bold uppercase text-slate-400 mb-3 text-center flex justify-center items-center gap-2">
                      <Flag size={14} className="text-emerald-400"/> Matriz de Placares Exatos
                    </h3>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {(showAllScores ? results.exactScores.slice(0, 12) : results.exactScores.slice(0, 4)).map((score, idx) => (
                        <div key={idx} className="bg-slate-900 px-3 py-2 rounded flex justify-between items-center border border-slate-700/50 hover:border-emerald-500/50 transition-colors">
                          <span className="font-bold text-slate-200">{score.score}</span>
                          <span className="font-mono text-emerald-400 text-sm">@ {toOdd(score.prob)}</span>
                        </div>
                      ))}
                    </div>
                    <button
                      onClick={() => setShowAllScores(!showAllScores)}
                      className="w-full mt-3 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 text-[10px] font-bold rounded uppercase transition-colors tracking-wider"
                    >
                      {showAllScores ? 'Recolher Matriz' : 'Expandir Cauda Longa (Top 12)'}
                    </button>
                  </div>

                  <div className="bg-gradient-to-br from-slate-800 to-slate-900 p-5 rounded-xl border border-emerald-500/30 shadow-lg relative overflow-hidden">
                    <div className="absolute top-0 right-0 p-4 opacity-10"><Crosshair size={64}/></div>
                    <h3 className="text-[10px] font-bold uppercase text-emerald-400 mb-4 flex items-center gap-2 tracking-wider">
                      <Crosshair size={14}/> Calculadora de Odd Isolada
                    </h3>

                    <div className="flex items-center justify-between gap-2 mb-5 relative z-10">
                      <div className="flex flex-col items-center flex-1">
                        <span className="text-[10px] text-slate-400 mb-1 uppercase tracking-wider truncate w-20 text-center">{results.t1.name}</span>
                        <input
                          type="number" min="0" value={customScore1}
                          onChange={e => setCustomScore1(e.target.value)}
                          className="w-16 bg-slate-950 border border-slate-600 rounded-lg p-2 text-center text-2xl font-bold text-emerald-400 focus:border-emerald-500 outline-none transition-colors"
                        />
                      </div>
                      <span className="text-lg font-black text-slate-600 px-2">X</span>
                      <div className="flex flex-col items-center flex-1">
                        <span className="text-[10px] text-slate-400 mb-1 uppercase tracking-wider truncate w-20 text-center">{results.t2.name}</span>
                        <input
                          type="number" min="0" value={customScore2}
                          onChange={e => setCustomScore2(e.target.value)}
                          className="w-16 bg-slate-950 border border-slate-600 rounded-lg p-2 text-center text-2xl font-bold text-orange-400 focus:border-orange-500 outline-none transition-colors"
                        />
                      </div>
                    </div>

                    <div className="bg-slate-950 p-4 rounded-lg border border-slate-700/50 flex justify-between items-center relative z-10">
                      <div>
                        <span className="block text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1">Odd Justa</span>
                        <span className="text-xl font-black text-white">@ {getCustomScoreData().odd}</span>
                      </div>
                      <div className="text-right border-l border-slate-800 pl-4">
                        <span className="block text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1">Probabilidade</span>
                        <span className="text-lg font-bold font-mono text-blue-400">{getCustomScoreData().pct}</span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* MAPA DE CALOR: MATRIZ 7x7 DE PROBABILIDADES POR PLACAR */}
                {heatmapData && (
                  <div className="lg:col-span-3 bg-slate-800 p-6 rounded-2xl border border-slate-700 mt-4 shadow-xl">
                    <h3 className="text-lg font-bold text-slate-100 mb-1 flex items-center gap-2 border-b border-slate-700 pb-3">
                      <Grid3x3 className="text-blue-400" /> Mapa de Calor — Matriz de Placares (0 a 6 golos)
                    </h3>
                    <p className="text-xs text-slate-400 mt-3 mb-5">
                      Cada célula é a probabilidade de exatamente aquele placar acontecer, calculada pela distribuição de Poisson
                      com λ = {results.lambda1.toFixed(2)} ({results.t1.name}) e λ = {results.lambda2.toFixed(2)} ({results.t2.name}).
                      Azul = menos provável · Amarelo = intermediário · Verde = mais provável.
                    </p>

                    <div className="overflow-x-auto">
                      <div className="inline-block min-w-full">
                        {/* Cabeçalho: golos da equipa 2 (colunas) */}
                        <div className="flex items-center mb-1">
                          <div className="w-24 shrink-0"></div>
                          <div className="flex-1 text-center text-[10px] font-bold text-orange-400 uppercase tracking-wider pb-1">
                            Golos {results.t2.name}
                          </div>
                        </div>
                        <div className="flex">
                          <div className="w-24 shrink-0"></div>
                          {Array.from({ length: heatmapData.size }).map((_, j) => (
                            <div key={j} className="flex-1 min-w-[44px] text-center text-xs font-bold text-slate-400 pb-1">{j}</div>
                          ))}
                        </div>

                        {/* Linhas: golos da equipa 1 */}
                        {heatmapData.grid.map((row, i) => (
                          <div key={i} className="flex items-center">
                            {i === Math.floor(heatmapData.size / 2) ? (
                              <div className="w-24 shrink-0 text-[10px] font-bold text-emerald-400 uppercase tracking-wider text-right pr-2">
                                Golos<br/>{results.t1.name}
                              </div>
                            ) : (
                              <div className="w-24 shrink-0 text-xs font-bold text-slate-400 text-right pr-2">{i}</div>
                            )}
                            {row.map((p, j) => {
                              const { background, color } = heatColor(p, heatmapData.min, heatmapData.max);
                              return (
                                <div
                                  key={j}
                                  className="flex-1 min-w-[44px] aspect-square flex items-center justify-center m-0.5 rounded-md text-[10px] font-bold font-mono transition-transform hover:scale-110"
                                  style={{ background, color }}
                                  title={`${results.t1.name} ${i} x ${j} ${results.t2.name}: ${toPct(p)}`}
                                >
                                  {(p * 100).toFixed(1)}%
                                </div>
                              );
                            })}
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}

                {/* SCANNER MULTI-MERCADO DE KELLY */}
                <div className="lg:col-span-3 bg-slate-800 p-6 rounded-2xl border border-purple-500/30 mt-4 shadow-xl">
                  <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center border-b border-slate-700 pb-3 mb-4 gap-3">
                    <h3 className="text-lg font-bold text-slate-100 flex items-center gap-2">
                      <TrendingUp className="text-purple-400" /> Scanner EV+ Multi-Mercado (Kelly)
                    </h3>
                    {marketScan && (
                      <div className="flex items-center gap-3">
                        <span className={`text-xs font-bold px-3 py-1 rounded-full ${evPlusCount > 0 ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}`}>
                          {evPlusCount} mercado(s) EV+
                        </span>
                        <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer select-none">
                          <input type="checkbox" checked={showOnlyEvPlus} onChange={e => setShowOnlyEvPlus(e.target.checked)} className="accent-purple-500"/>
                          Só EV+
                        </label>
                      </div>
                    )}
                  </div>

                  {!bookieOddsData && (
                    <div className="p-6 bg-slate-900 rounded-xl border border-dashed border-slate-700 text-center">
                      <ScanLine className="mx-auto text-slate-600 mb-2" size={32}/>
                      <p className="text-sm text-slate-400">
                        Nenhuma odd importada. Use o botão roxo <strong className="text-purple-400">"Ler Odds da Casa (OCR)"</strong> no topo
                        e envie um print da página completa de mercados da casa de apostas.
                      </p>
                    </div>
                  )}

                  {bookieOddsData && (!marketScan || marketScan.length === 0) && (
                    <div className="p-6 bg-slate-900 rounded-xl border border-dashed border-slate-700 text-center">
                      <p className="text-sm text-slate-400">Nenhum mercado compatível encontrado no print. Tente uma captura com o Resultado Final e Total de Gols visíveis.</p>
                    </div>
                  )}

                  {marketScan && marketScan.length > 0 && (
                    <>
                      {/* Aviso se as equipas do print de odds não batem com as selecionadas */}
                      {(bookieOddsData.confronto?.equipe_mandante &&
                        bookieOddsData.confronto.equipe_mandante.toLowerCase() !== results.t1.name.toLowerCase()) && (
                        <div className="mb-4 p-3 bg-yellow-950/30 border border-yellow-500/40 rounded-lg text-xs text-yellow-400 flex items-center gap-2">
                          <AlertTriangle size={14} className="shrink-0"/>
                          Atenção: as odds importadas são de "{bookieOddsData.confronto.equipe_mandante} x {bookieOddsData.confronto.equipe_visitante}",
                          mas o modelo está calculado para "{results.t1.name} x {results.t2.name}". Verifique se são o mesmo jogo.
                        </div>
                      )}

                      <p className="text-xs text-slate-400 mb-4">
                        Cada linha compara a <strong className="text-slate-200">odd da casa</strong> com a <strong className="text-slate-200">odd justa do modelo</strong>.
                        EV = (probabilidade × odd) − 1. A stake segue Kelly Fracionado ({(kellyFraction * 100).toFixed(0)}%) sobre a banca de R$ {toNumber(bankroll).toFixed(2)} definida abaixo.
                      </p>

                      <div className="overflow-x-auto rounded-xl border border-slate-700">
                        <table className="w-full text-left text-sm">
                          <thead>
                            <tr className="bg-slate-900 text-slate-400 text-[10px] uppercase tracking-wider">
                              <th className="p-3">Mercado</th>
                              <th className="p-3 hidden sm:table-cell">Categoria</th>
                              <th className="p-3 text-right">Prob. Modelo</th>
                              <th className="p-3 text-right">Odd Justa</th>
                              <th className="p-3 text-right">Odd Casa</th>
                              <th className="p-3 text-right">EV</th>
                              <th className="p-3 text-right">Stake Kelly</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-700/50">
                            {marketScan
                              .filter(m => !showOnlyEvPlus || m.ev > 0)
                              .map((m, idx) => {
                                const isEvPlus = m.ev > 0;
                                const stake = isEvPlus ? (toNumber(bankroll) * m.kellyFull * kellyFraction) : 0;
                                return (
                                  <tr key={idx} className={`${isEvPlus ? 'bg-emerald-950/20 hover:bg-emerald-950/40' : 'hover:bg-slate-700/20'} transition-colors`}>
                                    <td className="p-3 font-semibold text-slate-200">{m.label}</td>
                                    <td className="p-3 hidden sm:table-cell text-xs text-slate-500">{m.categoria}</td>
                                    <td className="p-3 text-right font-mono text-blue-400">{toPct(m.p)}</td>
                                    <td className="p-3 text-right font-mono text-slate-300">@ {m.fairOdd.toFixed(2)}</td>
                                    <td className={`p-3 text-right font-mono font-bold ${isEvPlus ? 'text-emerald-400' : 'text-slate-400'}`}>@ {m.odd.toFixed(2)}</td>
                                    <td className={`p-3 text-right font-mono font-bold ${isEvPlus ? 'text-emerald-400' : 'text-red-400'}`}>
                                      {m.ev > 0 ? '+' : ''}{(m.ev * 100).toFixed(1)}%
                                    </td>
                                    <td className="p-3 text-right font-mono">
                                      {isEvPlus
                                        ? <span className="text-emerald-300 font-bold">R$ {stake.toFixed(2)}</span>
                                        : <span className="text-slate-600">—</span>}
                                    </td>
                                  </tr>
                                );
                              })}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
                </div>

                {/* MOTOR MONTE CARLO & KELLY (mercado único) */}
                <div className="lg:col-span-3 bg-slate-800 p-6 rounded-2xl border border-slate-700 mt-4 shadow-xl">
                  <h3 className="text-lg font-bold text-slate-100 mb-1 flex items-center gap-2 border-b border-slate-700 pb-3">
                    <Activity className="text-emerald-400" /> Motor Monte Carlo & Critério de Kelly (Mercado Único)
                  </h3>
                  <p className="text-xs text-slate-400 mt-3 mb-5">
                    Simula o jogo {SIMULATIONS.toLocaleString('pt-BR')} vezes usando os λ do modelo
                    (<span className="text-emerald-400 font-mono">{results.lambda1.toFixed(2)}</span> vs <span className="text-orange-400 font-mono">{results.lambda2.toFixed(2)}</span>).
                    A banca e a fração de Kelly definidas aqui também alimentam o Scanner acima.
                  </p>

                  <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
                    <div className="bg-slate-950 p-4 rounded-xl border border-slate-800">
                      <label className="text-xs text-slate-500 uppercase font-bold">Banca Total (R$)</label>
                      <input type="number" min="0" step="10" value={bankroll} onChange={e => setBankroll(e.target.value)} className="w-full bg-slate-800 text-white p-2 mt-1 rounded font-mono" />
                    </div>
                    <div className="bg-slate-950 p-4 rounded-xl border border-slate-800">
                      <label className="text-xs text-slate-500 uppercase font-bold">Mercado Alvo</label>
                      <select value={kellyTarget} onChange={e => setKellyTarget(e.target.value)} className="w-full bg-slate-800 text-slate-200 p-2 mt-1 rounded font-semibold">
                        <option value="1">Vitória {results.t1.name}</option>
                        <option value="X">Empate</option>
                        <option value="2">Vitória {results.t2.name}</option>
                      </select>
                    </div>
                    <div className="bg-slate-950 p-4 rounded-xl border border-slate-800">
                      <label className="text-xs text-slate-500 uppercase font-bold">Odd da Casa</label>
                      <input type="number" step="0.01" min="1.01" value={bookieOdd} onChange={e => setBookieOdd(e.target.value)} className="w-full bg-slate-800 text-blue-400 p-2 mt-1 rounded font-mono font-bold" />
                    </div>
                    <div className="bg-slate-950 p-4 rounded-xl border border-slate-800">
                      <label className="text-xs text-slate-500 uppercase font-bold">Risco (Fração Kelly)</label>
                      <select value={kellyFraction} onChange={e => setKellyFraction(Number(e.target.value))} className="w-full bg-slate-800 text-slate-300 p-2 mt-1 rounded">
                        <option value={0.10}>10% (Ultra Seguro)</option>
                        <option value={0.25}>25% (Profissional)</option>
                        <option value={0.50}>50% (Agressivo)</option>
                        <option value={1.00}>100% (Risco de Ruína)</option>
                      </select>
                    </div>
                  </div>

                  <button
                    onClick={runMonteCarlo}
                    disabled={mcRunning}
                    className={`w-full text-white font-bold py-4 rounded-xl transition-all flex justify-center items-center gap-2 ${mcRunning ? 'bg-slate-700 cursor-wait' : 'bg-emerald-600 hover:bg-emerald-500 shadow-[0_0_20px_rgba(16,185,129,0.3)]'}`}
                  >
                    {mcRunning ? <Loader2 className="animate-spin"/> : <PlayCircle />}
                    {mcRunning ? 'Simulando...' : `Rodar ${SIMULATIONS.toLocaleString('pt-BR')} Simulações & Calcular Stake`}
                  </button>

                  {mcResults && (
                    <div className="mt-6 grid grid-cols-1 sm:grid-cols-3 gap-4">
                      <div className="bg-slate-950 p-4 rounded-xl border border-emerald-500/30 text-center">
                        <span className="block text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1 truncate">{results.t1.name}</span>
                        <span className="text-2xl font-black text-emerald-400 font-mono">{toPct(mcResults.probWin1)}</span>
                        <span className="block text-xs text-slate-500 font-mono mt-1">@ {toOdd(mcResults.probWin1)}</span>
                      </div>
                      <div className="bg-slate-950 p-4 rounded-xl border border-slate-700 text-center">
                        <span className="block text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1">Empate</span>
                        <span className="text-2xl font-black text-slate-300 font-mono">{toPct(mcResults.probDraw)}</span>
                        <span className="block text-xs text-slate-500 font-mono mt-1">@ {toOdd(mcResults.probDraw)}</span>
                      </div>
                      <div className="bg-slate-950 p-4 rounded-xl border border-orange-500/30 text-center">
                        <span className="block text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1 truncate">{results.t2.name}</span>
                        <span className="text-2xl font-black text-orange-400 font-mono">{toPct(mcResults.probWin2)}</span>
                        <span className="block text-xs text-slate-500 font-mono mt-1">@ {toOdd(mcResults.probWin2)}</span>
                      </div>
                    </div>
                  )}

                  {kellyRecommendation && (
                    <div className={`mt-4 p-6 rounded-2xl border ${kellyRecommendation.edge ? 'bg-emerald-950/30 border-emerald-500/50' : 'bg-red-950/30 border-red-500/50'}`}>
                      <h4 className="text-base font-bold mb-2 flex items-center gap-2">
                        <DollarSign size={18}/> Veredito de Gestão (Critério de Kelly)
                      </h4>
                      <p className="text-sm text-slate-300 mb-4">{kellyRecommendation.message}</p>

                      {kellyRecommendation.edge ? (
                        <div>
                          <div className="text-4xl font-black text-emerald-400 font-mono">
                            R$ {kellyRecommendation.stake}
                            <span className="text-sm text-emerald-600 ml-2">({kellyRecommendation.pct}% da banca)</span>
                          </div>
                          <div className="flex flex-wrap gap-4 mt-3 text-xs text-slate-400 font-mono">
                            <span>Odd justa do modelo: <span className="text-slate-200">@ {kellyRecommendation.fairOdd}</span></span>
                            <span>Kelly cheio: <span className="text-slate-200">{kellyRecommendation.fullKelly}%</span></span>
                            <span>Fração aplicada: <span className="text-slate-200">{(kellyFraction * 100).toFixed(0)}%</span></span>
                          </div>
                        </div>
                      ) : (
                        <div>
                          <div className="text-2xl font-black text-red-500 font-mono">
                            OPERAÇÃO ABORTADA. ODD INJUSTA.
                          </div>
                          <div className="text-xs text-slate-400 font-mono mt-2">
                            Odd justa do modelo: <span className="text-slate-200">@ {kellyRecommendation.fairOdd}</span> · Odd oferecida: <span className="text-red-400">@ {toNumber(bookieOdd).toFixed(2)}</span>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* SIMULAÇÃO POR CADEIA DE MARKOV (MINUTO A MINUTO) */}
                <div className="lg:col-span-3 bg-slate-800 p-6 rounded-2xl border border-blue-500/30 mt-4 shadow-xl">
                  <h3 className="text-lg font-bold text-slate-100 mb-1 flex items-center gap-2 border-b border-slate-700 pb-3">
                    <Grid3x3 className="text-blue-400" /> Simulação por Cadeia de Markov (Minuto a Minuto)
                  </h3>
                  <p className="text-xs text-slate-400 mt-3 mb-4">
                    Simula partidas minuto a minuto ({MARKOV_MINUTES} passos cada), em vez de sortear o placar final de
                    uma vez. A cada minuto, a chance de gol é a taxa base (λ ÷ {MARKOV_MINUTES}) — e, se a dinâmica de
                    placar estiver ativa, essa chance muda de acordo com quem está ganhando ou perdendo naquele instante.
                  </p>

                  <div className="bg-slate-900 border border-slate-700 p-4 rounded-xl mb-5">
                    <label className="text-xs text-slate-500 uppercase font-bold block mb-1">Quantidade de Simulações</label>
                    <select
                      value={markovSimCount}
                      onChange={(e) => setMarkovSimCount(Number(e.target.value))}
                      className="w-full bg-slate-800 text-slate-200 p-2 mt-1 rounded font-mono font-bold"
                    >
                      <option value={1000}>1.000 (rápido, menos preciso)</option>
                      <option value={5000}>5.000</option>
                      <option value={10000}>10.000</option>
                      <option value={20000}>20.000 (recomendado)</option>
                      <option value={50000}>50.000</option>
                      <option value={100000}>100.000 (mais lento, mais preciso)</option>
                    </select>
                    <p className="text-[11px] text-slate-500 mt-2">
                      Mais simulações reduzem o ruído estatístico da estimativa, ao custo de mais tempo de processamento
                      (cada simulação percorre {MARKOV_MINUTES} minutos).
                    </p>
                  </div>

                  <label className="flex items-center gap-3 bg-slate-900 border border-slate-700 rounded-xl p-4 mb-5 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={markovDynamics}
                      onChange={(e) => setMarkovDynamics(e.target.checked)}
                      className="w-5 h-5 accent-blue-500"
                    />
                    <div>
                      <span className="text-sm font-bold text-slate-200 block">Ativar dinâmica de placar + tempo (calibrada com dados reais)</span>
                      <span className="text-xs text-slate-500">
                        Desligado: estatisticamente equivalente à Poisson pura (serve para validar o modelo).
                        Ligado: usa multiplicadores calibrados com 64 jogos reais da Copa do Mundo 2022 (StatsBomb Open Data) —
                        por placar (empatando: ×0.77; à frente por 1: ×1.52; atrás por 2+: ×1.31) e por minuto do jogo
                        (últimos 15min: ×2.18; primeiros 15min: ×0.46 — gols se concentram no fim da partida).
                        Amostra pequena (um único torneio); trate como indicativo, não definitivo.
                      </span>
                    </div>
                  </label>
                  {markovDynamics && (
                    <p className="text-[11px] text-yellow-400/80 -mt-3 mb-5 flex items-start gap-1.5">
                      <AlertTriangle size={13} className="mt-0.5 shrink-0"/>
                      Calibração baseada em apenas 64 partidas de mata-mata de Copa do Mundo — contexto de alta pressão que
                      pode não generalizar para ligas domésticas. Use o script <code className="text-yellow-300">calibrate_markov.py</code> para
                      recalibrar com mais competições/temporadas.
                    </p>
                  )}

                  <button
                    onClick={runMarkovSimulation}
                    disabled={markovRunning}
                    className={`w-full text-white font-bold py-4 rounded-xl transition-all flex justify-center items-center gap-2 ${markovRunning ? 'bg-slate-700 cursor-wait' : 'bg-blue-600 hover:bg-blue-500 shadow-[0_0_20px_rgba(37,99,235,0.3)]'}`}
                  >
                    {markovRunning ? <Loader2 className="animate-spin"/> : <PlayCircle />}
                    {markovRunning ? 'Simulando minuto a minuto...' : `Rodar ${markovSimCount.toLocaleString('pt-BR')} Simulações (Cadeia de Markov)`}
                  </button>

                  {markovResults && (
                    <>
                      <div className="mt-6 grid grid-cols-1 sm:grid-cols-3 gap-4">
                        <div className="bg-slate-950 p-4 rounded-xl border border-emerald-500/30 text-center">
                          <span className="block text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1 truncate">{results.t1.name}</span>
                          <span className="text-2xl font-black text-emerald-400 font-mono">{toPct(markovResults.probWin1)}</span>
                          <span className="block text-xs text-slate-500 font-mono mt-1">@ {toOdd(markovResults.probWin1)}</span>
                        </div>
                        <div className="bg-slate-950 p-4 rounded-xl border border-slate-700 text-center">
                          <span className="block text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1">Empate</span>
                          <span className="text-2xl font-black text-slate-300 font-mono">{toPct(markovResults.probDraw)}</span>
                          <span className="block text-xs text-slate-500 font-mono mt-1">@ {toOdd(markovResults.probDraw)}</span>
                        </div>
                        <div className="bg-slate-950 p-4 rounded-xl border border-orange-500/30 text-center">
                          <span className="block text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1 truncate">{results.t2.name}</span>
                          <span className="text-2xl font-black text-orange-400 font-mono">{toPct(markovResults.probWin2)}</span>
                          <span className="block text-xs text-slate-500 font-mono mt-1">@ {toOdd(markovResults.probWin2)}</span>
                        </div>
                      </div>

                      {/* Comparação direta com o modelo de Poisson (matriz) */}
                      <div className="mt-4 grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
                        <div className="bg-slate-900 rounded-lg p-3 border border-slate-700">
                          <span className="block text-slate-500 uppercase font-bold mb-1">Δ vs Poisson ({results.t1.name})</span>
                          <span className={`font-mono font-bold ${Math.abs(markovResults.probWin1 - results.probWin1) < 0.01 ? 'text-slate-400' : 'text-yellow-400'}`}>
                            {((markovResults.probWin1 - results.probWin1) * 100).toFixed(1)} p.p.
                          </span>
                        </div>
                        <div className="bg-slate-900 rounded-lg p-3 border border-slate-700">
                          <span className="block text-slate-500 uppercase font-bold mb-1">Δ vs Poisson (Empate)</span>
                          <span className={`font-mono font-bold ${Math.abs(markovResults.probDraw - results.probDraw) < 0.01 ? 'text-slate-400' : 'text-yellow-400'}`}>
                            {((markovResults.probDraw - results.probDraw) * 100).toFixed(1)} p.p.
                          </span>
                        </div>
                        <div className="bg-slate-900 rounded-lg p-3 border border-slate-700">
                          <span className="block text-slate-500 uppercase font-bold mb-1">Δ vs Poisson ({results.t2.name})</span>
                          <span className={`font-mono font-bold ${Math.abs(markovResults.probWin2 - results.probWin2) < 0.01 ? 'text-slate-400' : 'text-yellow-400'}`}>
                            {((markovResults.probWin2 - results.probWin2) * 100).toFixed(1)} p.p.
                          </span>
                        </div>
                      </div>

                      <div className="mt-4 bg-slate-900 rounded-xl border border-slate-700 p-4">
                        <span className="block text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-3">Placares mais frequentes na simulação</span>
                        <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
                          {markovResults.topScores.map((s, idx) => (
                            <div key={idx} className="bg-slate-950 px-2 py-2 rounded text-center border border-slate-700/50">
                              <span className="block font-bold text-slate-200 text-sm">{s.score}</span>
                              <span className="block text-[10px] text-blue-400 font-mono">{toPct(s.prob)}</span>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* DISTRIBUIÇÃO DE GOLS POR MINUTO: simulação vs dados reais */}
                      <div className="mt-4 bg-slate-900 rounded-xl border border-slate-700 p-4">
                        <span className="block text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1">
                          Distribuição de Golos por Minuto (a cada 15 min)
                        </span>
                        <p className="text-[11px] text-slate-500 mb-4">
                          <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-blue-500 inline-block"/> Sua simulação</span>
                          {' vs '}
                          <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-emerald-500 inline-block"/> Real (Copa 2022, referência)</span>
                        </p>
                        <div className="flex items-end justify-between gap-2 h-36">
                          {MARKOV_MINUTE_BINS.map((bin, idx) => {
                            const simPct = markovResults.minuteDistribution[idx] * 100;
                            const realPct = REAL_MINUTE_DISTRIBUTION[idx] * 100;
                            const maxPct = Math.max(...markovResults.minuteDistribution.map(v => v * 100), ...REAL_MINUTE_DISTRIBUTION.map(v => v * 100));
                            return (
                              <div key={idx} className="flex-1 flex flex-col items-center h-full">
                                <div className="flex-1 w-full flex items-end justify-center gap-0.5">
                                  <div
                                    className="w-1/2 bg-blue-500 rounded-t"
                                    style={{ height: `${(simPct / maxPct) * 100}%` }}
                                    title={`Simulação: ${simPct.toFixed(1)}%`}
                                  />
                                  <div
                                    className="w-1/2 bg-emerald-500 rounded-t"
                                    style={{ height: `${(realPct / maxPct) * 100}%` }}
                                    title={`Real: ${realPct.toFixed(1)}%`}
                                  />
                                </div>
                                <span className="text-[9px] text-slate-500 mt-1">{bin.label}</span>
                                <span className="text-[9px] text-blue-400 font-mono">{simPct.toFixed(0)}%</span>
                              </div>
                            );
                          })}
                        </div>
                        {!markovDynamics && (
                          <p className="text-[11px] text-yellow-400/80 mt-3 flex items-start gap-1.5">
                            <AlertTriangle size={12} className="mt-0.5 shrink-0"/>
                            Dinâmica de placar/tempo desligada — sua simulação deve ficar bem mais uniforme (achatada)
                            que a distribuição real. Ligue a dinâmica calibrada acima para reproduzir a concentração
                            de golos no fim do jogo.
                          </p>
                        )}
                      </div>

                      {/* MAPA DE CALOR DA SIMULAÇÃO: mesma matriz 7x7, agora com dados do Markov */}
                      {markovResults.heatGrid && (
                        <div className="mt-4 bg-slate-900 rounded-xl border border-slate-700 p-4">
                          <div className="flex items-center justify-between flex-wrap gap-2 mb-1">
                            <span className="text-[10px] text-slate-500 uppercase font-bold tracking-wider flex items-center gap-2">
                              <Grid3x3 size={13}/> Matriz de Placares — Obtida pela Simulação (0 a 6 golos)
                            </span>
                            <button
                              onClick={() => setMarkovHeatDisplay(markovHeatDisplay === 'pct' ? 'odd' : 'pct')}
                              className="flex items-center gap-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-600 text-slate-200 text-[11px] font-bold px-3 py-1.5 rounded-lg transition-colors"
                            >
                              <Scale size={12}/> Mostrar {markovHeatDisplay === 'pct' ? 'Odd' : '%'}
                            </button>
                          </div>
                          <p className="text-[11px] text-slate-500 mb-4">
                            Baseada nos {markovSimCount.toLocaleString('pt-BR')} jogos simulados minuto a minuto — compare com o
                            mapa de calor teórico (Poisson pura) lá em cima para ver o efeito da dinâmica de placar/tempo.
                          </p>

                          <div className="overflow-x-auto">
                            <div className="inline-block min-w-full">
                              <div className="flex items-center mb-1">
                                <div className="w-24 shrink-0"></div>
                                <div className="flex-1 text-center text-[10px] font-bold text-orange-400 uppercase tracking-wider pb-1">
                                  Golos {results.t2.name}
                                </div>
                              </div>
                              <div className="flex">
                                <div className="w-24 shrink-0"></div>
                                {Array.from({ length: 7 }).map((_, j) => (
                                  <div key={j} className="flex-1 min-w-[44px] text-center text-xs font-bold text-slate-400 pb-1">{j}</div>
                                ))}
                              </div>

                              {markovResults.heatGrid.map((row, i) => (
                                <div key={i} className="flex items-center">
                                  {i === 3 ? (
                                    <div className="w-24 shrink-0 text-[10px] font-bold text-emerald-400 uppercase tracking-wider text-right pr-2">
                                      Golos<br/>{results.t1.name}
                                    </div>
                                  ) : (
                                    <div className="w-24 shrink-0 text-xs font-bold text-slate-400 text-right pr-2">{i}</div>
                                  )}
                                  {row.map((p, j) => {
                                    const { background, color } = heatColor(p, markovResults.heatMin, markovResults.heatMax);
                                    return (
                                      <div
                                        key={j}
                                        className="flex-1 min-w-[44px] aspect-square flex items-center justify-center m-0.5 rounded-md text-[10px] font-bold font-mono transition-transform hover:scale-110"
                                        style={{ background, color }}
                                        title={`${results.t1.name} ${i} x ${j} ${results.t2.name}: ${toPct(p)}`}
                                      >
                                        {markovHeatDisplay === 'pct' ? `${(p * 100).toFixed(1)}%` : toOdd(p)}
                                      </div>
                                    );
                                  })}
                                </div>
                              ))}
                            </div>
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </div>

                {/* COMPARADOR DE HANDICAPS */}
                <div className="lg:col-span-3 bg-slate-800 p-6 rounded-2xl border border-slate-700 mt-4 shadow-xl">
                  <h3 className="text-lg font-bold text-slate-100 mb-6 flex items-center gap-2 border-b border-slate-700 pb-3">
                    <Scale className="text-blue-400" /> Comparador de Handicap: Asiático vs Europeu
                  </h3>

                  <div className="flex flex-col md:flex-row gap-6 mb-8">
                    <div className="flex-1">
                      <label className="block text-xs font-bold text-slate-400 uppercase mb-2">Equipa para Aplicar a Vantagem</label>
                      <select
                        className="w-full bg-slate-900 border border-slate-600 rounded-lg p-3 text-slate-100 outline-none font-semibold"
                        value={handicapTeam} onChange={(e) => setHandicapTeam(Number(e.target.value))}
                      >
                        <option value={1}>{results.t1.name} (Mandante)</option>
                        <option value={2}>{results.t2.name} (Visitante)</option>
                      </select>
                    </div>
                    <div className="flex-1">
                      <label className="block text-xs font-bold text-slate-400 uppercase mb-2">Margem Desejada (Linha do Handicap)</label>
                      <div className="flex items-center gap-3 bg-slate-900 border border-slate-600 rounded-lg p-2">
                        <span className="text-xl font-bold text-slate-500 pl-3">-</span>
                        <input
                          type="number" min="1" max="8" value={handicapLine}
                          onChange={(e) => setHandicapLine(e.target.value)}
                          className="w-full bg-transparent text-center text-xl font-bold text-white outline-none"
                        />
                      </div>
                    </div>
                  </div>

                  {hcData && (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">

                      <div className="bg-slate-900 border border-red-500/30 rounded-xl p-5 relative overflow-hidden">
                        <div className="absolute top-0 right-0 bg-red-500/20 text-red-400 text-[10px] font-bold px-3 py-1 rounded-bl-lg uppercase tracking-wider">Sem Devolução</div>
                        <h4 className="font-bold text-red-400 mb-4 flex items-center gap-2">Handicap Europeu (HE)</h4>

                        <div className="space-y-4">
                          <div className="bg-slate-800 p-3 rounded-lg border border-slate-700">
                            <div className="flex justify-between items-center mb-1">
                              <span className="font-bold text-slate-200">{hcData.teamName} -{hcData.H}</span>
                              <span className="text-lg font-black text-white">@ {hcData.oddEH_Win}</span>
                            </div>
                            <p className="text-xs text-slate-400">Requer vitória por <span className="text-red-400 font-bold">{hcData.H + 1} ou mais</span> golos de diferença.</p>
                          </div>

                          <div className="bg-slate-800 p-3 rounded-lg border border-slate-700">
                            <div className="flex justify-between items-center mb-1">
                              <span className="font-bold text-slate-200">Empate -{hcData.H}</span>
                              <span className="text-lg font-black text-white">@ {hcData.oddEH_Draw}</span>
                            </div>
                            <p className="text-xs text-slate-400">Requer vitória por <span className="text-red-400 font-bold">exatos {hcData.H}</span> golos de diferença.</p>
                          </div>
                        </div>
                      </div>

                      <div className="bg-slate-900 border border-emerald-500/30 rounded-xl p-5 relative overflow-hidden">
                        <div className="absolute top-0 right-0 bg-emerald-500/20 text-emerald-400 text-[10px] font-bold px-3 py-1 rounded-bl-lg uppercase tracking-wider">Com Proteção</div>
                        <h4 className="font-bold text-emerald-400 mb-4 flex items-center gap-2">Handicap Asiático (HA)</h4>

                        <div className="space-y-4">
                          <div className="bg-slate-800 p-3 rounded-lg border border-slate-700 border-l-4 border-l-emerald-500">
                            <div className="flex justify-between items-center mb-1">
                              <span className="font-bold text-slate-200">{hcData.teamName} -{hcData.H}.0</span>
                              <span className="text-lg font-black text-white">@ {hcData.oddAH_Int}</span>
                            </div>
                            <p className="text-xs text-slate-400">Vitória por <span className="text-emerald-400 font-bold">{hcData.H + 1}+</span> golos = <strong className="text-white">Ganha</strong>.<br/>Vitória por <span className="text-emerald-400 font-bold">exatos {hcData.H}</span> golos = <strong className="text-yellow-400">Aposta Devolvida</strong>.</p>
                          </div>

                          <div className="bg-slate-800 p-3 rounded-lg border border-slate-700">
                            <div className="flex justify-between items-center mb-1">
                              <span className="font-bold text-slate-200">{hcData.teamName} -{hcData.H - 0.5}</span>
                              <span className="text-lg font-black text-white">@ {hcData.oddAH_Half}</span>
                            </div>
                            <p className="text-xs text-slate-400">Requer vitória por <span className="text-emerald-400 font-bold">{hcData.H} ou mais</span> golos de diferença.</p>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}

                </div>

              </div>
            )}

            {/* HISTÓRICO E CALIBRAÇÃO — visível mesmo sem um confronto calculado no momento */}
            {supabaseAtivo && showCalibration && (
              <div className="bg-slate-800 p-6 rounded-2xl border border-purple-500/30 mt-4 shadow-xl">
                <h3 className="text-lg font-bold text-slate-100 mb-1 flex items-center gap-2 border-b border-slate-700 pb-3">
                  <BarChart3 className="text-purple-400" /> Histórico e Calibração do Modelo
                </h3>
                <p className="text-xs text-slate-400 mt-3 mb-4">
                  Um modelo bem calibrado não precisa "acertar sempre" — precisa que, das vezes que disse 70%, aconteça
                  perto de 70% mesmo. Resolva as previsões pendentes abaixo (marque o que realmente aconteceu) pra
                  começar a ver o diagrama de confiabilidade.
                </p>

                {predictionsLog.filter(p => p.resultado === null).length > 0 && (
                  <div className="mb-6">
                    <span className="text-xs font-bold text-slate-500 uppercase tracking-wider block mb-2">
                      Pendentes ({predictionsLog.filter(p => p.resultado === null).length})
                    </span>
                    <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
                      {predictionsLog.filter(p => p.resultado === null).map(p => (
                        <div key={p.id} className="flex items-center justify-between gap-2 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm">
                          <span className="flex-1 text-slate-300">
                            {p.equipe_mandante} x {p.equipe_visitante} — <strong className="text-slate-100">{p.outcome}</strong>
                            <span className="text-purple-400 font-mono ml-2">{toPct(p.probabilidade)}</span>
                          </span>
                          <button onClick={() => resolverPrevisao(p.id, true)} className="bg-emerald-700 hover:bg-emerald-600 text-white text-xs font-bold px-3 py-1.5 rounded-lg">Aconteceu</button>
                          <button onClick={() => resolverPrevisao(p.id, false)} className="bg-red-800 hover:bg-red-700 text-white text-xs font-bold px-3 py-1.5 rounded-lg">Não aconteceu</button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {predictionsLog.filter(p => p.resultado !== null).length === 0 ? (
                  <p className="text-sm text-slate-500 text-center py-6">
                    Ainda não há previsões resolvidas. Registre previsões em alguns confrontos, espere os jogos acontecerem,
                    e volte aqui pra marcar o resultado — depois de umas 20-30, o diagrama começa a fazer sentido estatístico.
                  </p>
                ) : (
                  <>
                    <span className="text-xs font-bold text-slate-500 uppercase tracking-wider block mb-2">
                      Diagrama de Confiabilidade ({predictionsLog.filter(p => p.resultado !== null).length} previsões resolvidas)
                    </span>
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-sm">
                        <thead>
                          <tr className="bg-slate-900 text-slate-400 text-[10px] uppercase tracking-wider">
                            <th className="p-2">Faixa Prevista</th>
                            <th className="p-2 text-right">n</th>
                            <th className="p-2 text-right">Média Prevista</th>
                            <th className="p-2 text-right">Frequência Real</th>
                            <th className="p-2 text-right">Diferença</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-700/50">
                          {calibracao.filter(f => f.n > 0).map((f, idx) => {
                            const diff = f.frequenciaReal - f.mediaProvista;
                            return (
                              <tr key={idx}>
                                <td className="p-2 font-mono text-slate-300">{f.label}</td>
                                <td className="p-2 text-right font-mono text-slate-400">{f.n}</td>
                                <td className="p-2 text-right font-mono text-purple-400">{toPct(f.mediaProvista)}</td>
                                <td className="p-2 text-right font-mono text-emerald-400">{toPct(f.frequenciaReal)}</td>
                                <td className={`p-2 text-right font-mono font-bold ${Math.abs(diff) < 0.1 ? 'text-slate-500' : diff > 0 ? 'text-blue-400' : 'text-orange-400'}`}>
                                  {diff > 0 ? '+' : ''}{(diff * 100).toFixed(1)}pp
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    <p className="text-[11px] text-slate-500 mt-3">
                      "Diferença" positiva (azul) = o modelo está sendo <strong>conservador demais</strong> nessa faixa
                      (aconteceu mais do que ele previu). Negativa (laranja) = <strong>otimista demais</strong>. Com poucas
                      previsões por faixa, essas diferenças ainda são só ruído estatístico — não tire conclusões fortes
                      com menos de ~20 previsões por faixa.
                    </p>
                  </>
                )}
              </div>
            )}
          </div>
        )}

        {/* ABA: DATABASE */}
        {activeTab === 'database' && (
          <div className="bg-slate-800 rounded-2xl border border-slate-700 overflow-hidden shadow-2xl animate-in fade-in duration-300">
            <div className="p-4 md:p-6 border-b border-slate-700 flex flex-col md:flex-row gap-4">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-slate-500" />
                <input
                  type="text"
                  placeholder="Filtrar seleção..."
                  className="w-full pl-10 pr-4 py-2.5 bg-slate-900 border border-slate-700 rounded-lg text-slate-100 outline-none focus:border-emerald-500"
                  value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)}
                />
              </div>
              <div className="relative">
                <Filter className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-slate-500" />
                <select
                  className="w-full md:w-48 pl-10 pr-4 py-2.5 bg-slate-900 border border-slate-700 rounded-lg text-slate-100 outline-none appearance-none cursor-pointer"
                  value={filterConfed} onChange={(e) => setFilterConfed(e.target.value)}
                >
                  {confederacoes.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="bg-slate-900 border-b border-slate-700 text-slate-400 text-xs uppercase tracking-wider">
                    <th className="p-4 cursor-pointer hover:text-slate-200" onClick={() => handleSort('name')}>
                      Seleção {sortConfig.key === 'name' ? (sortConfig.direction === 'asc' ? <ChevronUp size={14} className="inline"/> : <ChevronDown size={14} className="inline"/>) : ''}
                    </th>
                    <th className="p-4 cursor-pointer hover:text-slate-200 hidden sm:table-cell" onClick={() => handleSort('confederation')}>
                      Confederação {sortConfig.key === 'confederation' ? (sortConfig.direction === 'asc' ? <ChevronUp size={14} className="inline"/> : <ChevronDown size={14} className="inline"/>) : ''}
                    </th>
                    <th className="p-4 cursor-pointer hover:text-slate-200" onClick={() => handleSort('rating')}>
                      Elo Rating {sortConfig.key === 'rating' ? (sortConfig.direction === 'asc' ? <ChevronUp size={14} className="inline"/> : <ChevronDown size={14} className="inline"/>) : ''}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/50">
                  {filteredAndSortedTeams.map((team, index) => (
                    <tr key={team.id} className="hover:bg-slate-700/30 transition-colors">
                      <td className="p-4 font-medium text-slate-200">
                        <span className="text-slate-500 mr-3 text-xs">{(index + 1).toString().padStart(2, '0')}</span>
                        {team.name}
                      </td>
                      <td className="p-4 hidden sm:table-cell">
                        <span className="bg-slate-900 px-2 py-1 border border-slate-700 rounded text-xs text-slate-400">{team.confederation}</span>
                      </td>
                      <td className={`p-4 font-mono font-bold ${getEloColor(team.rating)}`}>{team.rating}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* METODOLOGIA & FONTES DE DADOS — visível em qualquer aba */}
        <div className="bg-slate-800 rounded-2xl border border-slate-700 p-6 mt-4 shadow-xl">
          <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-4 flex items-center gap-2">
            <BarChart3 className="text-emerald-400" size={16} /> Metodologia & Fontes de Dados
          </h3>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-5">
            <div className="bg-slate-900 border border-slate-700 rounded-lg p-3">
              <span className="block text-[10px] text-slate-500 uppercase font-bold">Vantagem de Mando de Campo</span>
              <span className="block text-lg font-mono font-bold text-emerald-400">casa ×1,10 / fora ×0,90</span>
              <span className="block text-[11px] text-slate-500">314 jogos reais, 6 torneios</span>
            </div>
            <div className="bg-slate-900 border border-slate-700 rounded-lg p-3">
              <span className="block text-[10px] text-slate-500 uppercase font-bold">Correção Dixon-Coles</span>
              <span className="block text-lg font-mono font-bold text-emerald-400">ρ = −0,042</span>
              <span className="block text-[11px] text-slate-500">314 jogos reais, 6 torneios</span>
            </div>
            <div className="bg-slate-900 border border-slate-700 rounded-lg p-3">
              <span className="block text-[10px] text-slate-500 uppercase font-bold">Multiplicador Temporal (Markov)</span>
              <span className="block text-lg font-mono font-bold text-emerald-400">últ. 15min ×2,18</span>
              <span className="block text-[11px] text-slate-500">64 jogos, Copa do Mundo 2022</span>
            </div>
            <div className="bg-slate-900 border border-slate-700 rounded-lg p-3">
              <span className="block text-[10px] text-slate-500 uppercase font-bold">Expected Threat (xT)</span>
              <span className="block text-lg font-mono font-bold text-emerald-400">313 jogos</span>
              <span className="block text-[11px] text-slate-500">587 mil eventos posicionais</span>
            </div>
          </div>

          <div className="mb-2">
            <span className="text-[11px] text-slate-500 uppercase font-bold tracking-wider block mb-2">
              Mapa de Expected Threat (xT) — onde no campo o perigo se concentra (referência, não entra nos cálculos)
            </span>
            <div className="overflow-x-auto">
              <div className="inline-block min-w-full">
                {XT_GRID.map((row, i) => {
                  const flatMin = Math.min(...XT_GRID.flat());
                  const flatMax = Math.max(...XT_GRID.flat());
                  return (
                    <div key={i} className="flex">
                      {row.map((v, j) => {
                        const { background, color } = heatColor(v, flatMin, flatMax);
                        return (
                          <div
                            key={j}
                            className="flex-1 min-w-6 aspect-[3/2] flex items-center justify-center text-[8px] font-mono font-bold m-px rounded"
                            style={{ background, color }}
                            title={`xT: ${(v * 100).toFixed(2)}%`}
                          >
                            {(v * 100).toFixed(1)}
                          </div>
                        );
                      })}
                    </div>
                  );
                })}
              </div>
            </div>
            <p className="text-[10px] text-slate-600 mt-1">← Defesa própria · Ataque adversário →</p>
          </div>

          <p className="text-[11px] text-slate-500 mt-4 pt-4 border-t border-slate-700">
            Dados brutos: <a href="https://github.com/statsbomb/open-data" target="_blank" rel="noopener noreferrer" className="text-emerald-400 hover:underline">StatsBomb Open Data</a> (uso livre para pesquisa e análise, conforme licença pública da StatsBomb). Métodos: Dixon-Coles (1997), Expected Threat / Karun Singh (2018), cadeia de Markov absorvente. Scripts de calibração e dados brutos completos disponíveis mediante solicitação.
          </p>
        </div>

      </div>
    </div>
  );
}
