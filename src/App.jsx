import React, { useState, useMemo, useRef } from 'react';
import { Search, Filter, ChevronUp, ChevronDown, Calculator, BarChart3, ShieldCheck, Target, Zap, AlertTriangle, Crosshair, Activity, Flag, Scale, FileJson, Check, X, Camera, Loader2, PlayCircle, DollarSign, ScanLine, TrendingUp } from 'lucide-react';

const selecoesData = [
  // CONCACAF
  { id: 1, name: 'Canadá', confederation: 'CONCACAF', rating: 1767, type: 'Sede' },
  { id: 2, name: 'Estados Unidos', confederation: 'CONCACAF', rating: 1780, type: 'Sede' },
  { id: 3, name: 'México', confederation: 'CONCACAF', rating: 1881, type: 'Sede' },
  { id: 4, name: 'Curaçao', confederation: 'CONCACAF', rating: 1434, type: 'Classificada' },
  { id: 5, name: 'Haiti', confederation: 'CONCACAF', rating: 1536, type: 'Classificada' },
  { id: 6, name: 'Panamá', confederation: 'CONCACAF', rating: 1714, type: 'Classificada' },
  // AFC
  { id: 7, name: 'Japão', confederation: 'AFC', rating: 1890, type: 'Classificada' },
  { id: 8, name: 'Irã', confederation: 'AFC', rating: 1820, type: 'Classificada' },
  { id: 9, name: 'Uzbequistão', confederation: 'AFC', rating: 1710, type: 'Classificada' },
  { id: 10, name: 'Coreia do Sul', confederation: 'AFC', rating: 1786, type: 'Classificada' },
  { id: 11, name: 'Jordânia', confederation: 'AFC', rating: 1650, type: 'Classificada' },
  { id: 12, name: 'Austrália', confederation: 'AFC', rating: 1839, type: 'Classificada' },
  { id: 13, name: 'Catar', confederation: 'AFC', rating: 1447, type: 'Classificada' },
  { id: 14, name: 'Arábia Saudita', confederation: 'AFC', rating: 1630, type: 'Classificada' },
  { id: 15, name: 'Iraque', confederation: 'AFC', rating: 1650, type: 'Repescagem' },
  // OFC
  { id: 16, name: 'Nova Zelândia', confederation: 'OFC', rating: 1560, type: 'Classificada' },
  // CONMEBOL
  { id: 17, name: 'Argentina', confederation: 'CONMEBOL', rating: 2119, type: 'Classificada' },
  { id: 18, name: 'Brasil', confederation: 'CONMEBOL', rating: 1978, type: 'Classificada' },
  { id: 19, name: 'Equador', confederation: 'CONMEBOL', rating: 1950, type: 'Classificada' },
  { id: 20, name: 'Uruguai', confederation: 'CONMEBOL', rating: 1913, type: 'Classificada' },
  { id: 21, name: 'Colômbia', confederation: 'CONMEBOL', rating: 1985, type: 'Classificada' },
  { id: 22, name: 'Paraguai', confederation: 'CONMEBOL', rating: 1780, type: 'Classificada' },
  // CAF
  { id: 23, name: 'Marrocos', confederation: 'CAF', rating: 1840, type: 'Classificada' },
  { id: 24, name: 'Tunísia', confederation: 'CAF', rating: 1680, type: 'Classificada' },
  { id: 25, name: 'Egito', confederation: 'CAF', rating: 1690, type: 'Classificada' },
  { id: 26, name: 'Argélia', confederation: 'CAF', rating: 1772, type: 'Classificada' },
  { id: 27, name: 'Gana', confederation: 'CAF', rating: 1510, type: 'Classificada' },
  { id: 28, name: 'Cabo Verde', confederation: 'CAF', rating: 1550, type: 'Classificada' },
  { id: 29, name: 'África do Sul', confederation: 'CAF', rating: 1511, type: 'Classificada' },
  { id: 30, name: 'Costa do Marfim', confederation: 'CAF', rating: 1720, type: 'Classificada' },
  { id: 31, name: 'Senegal', confederation: 'CAF', rating: 1750, type: 'Classificada' },
  { id: 32, name: 'RD Congo', confederation: 'CAF', rating: 1580, type: 'Repescagem' },
  // UEFA
  { id: 33, name: 'Inglaterra', confederation: 'UEFA', rating: 2024, type: 'Classificada' },
  { id: 34, name: 'França', confederation: 'UEFA', rating: 2028, type: 'Classificada' },
  { id: 35, name: 'Croácia', confederation: 'UEFA', rating: 1910, type: 'Classificada' },
  { id: 36, name: 'Portugal', confederation: 'UEFA', rating: 1993, type: 'Classificada' },
  { id: 37, name: 'Noruega', confederation: 'UEFA', rating: 1770, type: 'Classificada' },
  { id: 38, name: 'Holanda', confederation: 'UEFA', rating: 1968, type: 'Classificada' },
  { id: 39, name: 'Alemanha', confederation: 'UEFA', rating: 1914, type: 'Classificada' },
  { id: 40, name: 'Suíça', confederation: 'UEFA', rating: 1865, type: 'Classificada' },
  { id: 41, name: 'Áustria', confederation: 'UEFA', rating: 1880, type: 'Classificada' },
  { id: 42, name: 'Bélgica', confederation: 'UEFA', rating: 1900, type: 'Classificada' },
  { id: 43, name: 'Espanha', confederation: 'UEFA', rating: 2125, type: 'Classificada' },
  { id: 44, name: 'Escócia', confederation: 'UEFA', rating: 1794, type: 'Classificada' },
  { id: 45, name: 'Turquia', confederation: 'UEFA', rating: 1849, type: 'Classificada' },
  { id: 46, name: 'República Tcheca', confederation: 'UEFA', rating: 1712, type: 'Classificada' },
  { id: 47, name: 'Suécia', confederation: 'UEFA', rating: 1780, type: 'Classificada' },
  { id: 48, name: 'Bósnia e Herzegovina', confederation: 'UEFA', rating: 1616, type: 'Classificada' },
].sort((a, b) => a.name.localeCompare(b.name));

// --- Funções Matemáticas Auxiliares (Poisson) ---
const factorial = (n) => (n <= 1 ? 1 : n * factorial(n - 1));
const poisson = (lambda, k) => (Math.exp(-lambda) * Math.pow(lambda, k)) / factorial(k);
const poissonCDF = (lambda, k) => {
  let sum = 0;
  for (let i = 0; i <= k; i++) sum += poisson(lambda, i);
  return sum;
};

// Gerador aleatório de Poisson (algoritmo de Knuth) para o Monte Carlo
const getPoissonRandom = (lambda) => {
  const L = Math.exp(-lambda);
  let k = 0;
  let p = 1;
  do {
    k++;
    p *= Math.random();
  } while (p > L);
  return k - 1;
};

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
    ]
  }
}

Regras:
- "Resultado Final" / "1X2": casa = equipe mandante (esquerda), fora = equipe visitante.
- "Total de Gols": para cada linha visível (Mais de 1.5 / Menos de 1.5 etc.), crie um objeto {"linha", "mais", "menos"}. Inclua todas as linhas visíveis.
- "Escanteios" / "Total de Cantos": mesmo formato em total_escanteios.
- "Ambas Marcam" / "Ambas as equipes marcam": sim/nao.
- Odds são números decimais como 1.87, 3.30, 5.25. Se um mercado não estiver visível ou estiver bloqueado, use null.
- Ignore mercados de jogadores, cartões, handicaps e impulsos/promoções (odds turbinadas).
- Escreva os nomes das equipes em português.`;

export default function App() {
  const [activeTab, setActiveTab] = useState('calculator');

  // Database State
  const [searchTerm, setSearchTerm] = useState('');
  const [filterConfed, setFilterConfed] = useState('Todas');
  const [sortConfig, setSortConfig] = useState({ key: 'rating', direction: 'desc' });

  // Calculator State
  const [team1Id, setTeam1Id] = useState(36); // Portugal
  const [team2Id, setTeam2Id] = useState(32); // RD Congo
  const [metrics, setMetrics] = useState({
    xg1: 2.44, xga1: 1.12, poss1: 60, shots1: 18.44, shotsOnTarget1: 6.25, corners1: 6.31,
    xg2: 1.64, xga2: 0.71, poss2: 40, shots2: 12.36, shotsOnTarget2: 3.33, corners2: 4.44
  });
  const [results, setResults] = useState(null);

  const [eloWeight, setEloWeight] = useState(50);
  const [showAllScores, setShowAllScores] = useState(false);
  const [customScore1, setCustomScore1] = useState(2);
  const [customScore2, setCustomScore2] = useState(0);

  // Handicap State
  const [handicapTeam, setHandicapTeam] = useState(1);
  const [handicapLine, setHandicapLine] = useState(1);

  // JSON Import State
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
  const [bankroll, setBankroll] = useState(100.0);
  const [bookieOdd, setBookieOdd] = useState(1.85);
  const [kellyFraction, setKellyFraction] = useState(0.25);
  const [kellyTarget, setKellyTarget] = useState('1');
  const [mcResults, setMcResults] = useState(null);
  const [kellyRecommendation, setKellyRecommendation] = useState(null);
  const [mcRunning, setMcRunning] = useState(false);
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

  const getEloColor = (rating) => {
    if (rating >= 1950) return 'text-emerald-400';
    if (rating >= 1800) return 'text-blue-400';
    if (rating >= 1650) return 'text-yellow-400';
    return 'text-orange-400';
  };

  const handleMetricChange = (field, value) => {
    const val = parseFloat(value) || 0;
    const newMetrics = { ...metrics, [field]: val };
    if (field === 'poss1') newMetrics.poss2 = Math.max(0, 100 - val);
    if (field === 'poss2') newMetrics.poss1 = Math.max(0, 100 - val);
    setMetrics(newMetrics);
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
      xg1: mStats.xg_gols_esperados ?? prev.xg1,
      xga1: mStats.xga_xg_sofridos ?? prev.xga1,
      poss1: 50,
      shots1: mStats.chutes ?? prev.shots1,
      shotsOnTarget1: mStats.chutes_no_gol ?? prev.shotsOnTarget1,
      corners1: mStats.escanteios ?? prev.corners1,

      xg2: vStats.xg_gols_esperados ?? prev.xg2,
      xga2: vStats.xga_xg_sofridos ?? prev.xga2,
      poss2: 50,
      shots2: vStats.chutes ?? prev.shots2,
      shotsOnTarget2: vStats.chutes_no_gol ?? prev.shotsOnTarget2,
      corners2: vStats.escanteios ?? prev.corners2,
    }));

    return { matchedT1, matchedT2 };
  };

  const handleImportJson = () => {
    setJsonError('');
    setJsonSuccess('');

    if (!jsonInputData.trim()) {
      setJsonError('Cole o código JSON antes de importar.');
      return;
    }

    try {
      const parsedData = JSON.parse(jsonInputData);
      const { matchedT1, matchedT2 } = applyParsedData(parsedData);

      let successMsg = 'Estatísticas importadas com sucesso!';
      if (!matchedT1 || !matchedT2) {
        successMsg += ' (Aviso: Selecione as equipas manualmente, os nomes não corresponderam).';
      }
      setJsonSuccess(successMsg);

      setTimeout(() => {
        setShowJsonInput(false);
        setJsonInputData('');
        setJsonSuccess('');
      }, 3000);
    } catch (error) {
      setJsonError(error.message === 'Formato de JSON não reconhecido.'
        ? error.message
        : 'JSON Inválido. Verifique se copiou todo o código corretamente.');
    }
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

  // --- Núcleo compartilhado do OCR: imagem -> base64 (normalizada) -> IA -> JSON ---
  const extractJsonFromImage = async (file, prompt) => {
    // Passo 1: normaliza a imagem (sempre vira JPEG, sempre redimensionada)
    const base64Data = await normalizeImageToJpeg(file);
    const mediaType = 'image/jpeg';

    // Passo 2: envia imagem + instruções para a IA
    let response;
    try {
      response = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: 'claude-sonnet-4-6',
          max_tokens: 1000,
          messages: [
            {
              role: 'user',
              content: [
                { type: 'image', source: { type: 'base64', media_type: mediaType, data: base64Data } },
                { type: 'text', text: prompt }
              ]
            }
          ]
        })
      });
    } catch (networkErr) {
      throw new Error('Falha de rede ao contactar a IA. Verifique a conexão e tente novamente.');
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

      const nMercados =
        (parsedData.odds.resultado_final ? 3 : 0) +
        (parsedData.odds.total_gols?.length || 0) * 2 +
        (parsedData.odds.total_escanteios?.length || 0) * 2 +
        (parsedData.odds.ambas_marcam ? 2 : 0) +
        (parsedData.odds.dupla_chance ? 3 : 0);

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

    const rawEloDiff = t1.rating - t2.rating;
    const weightedEloDiff = rawEloDiff * (eloWeight / 100);

    const expectancyT1 = 1 / (1 + Math.pow(10, -weightedEloDiff / 400));
    const expectancyT2 = 1 - expectancyT1;

    const modPoss1 = 0.8 + (0.2 * (metrics.poss1 / 50));
    const modPoss2 = 0.8 + (0.2 * (metrics.poss2 / 50));

    const eff1 = metrics.shots1 > 0 ? metrics.xg1 / metrics.shots1 : 0;
    const eff2 = metrics.shots2 > 0 ? metrics.xg2 / metrics.shots2 : 0;

    const trueXG_T1 = (metrics.xg1 + metrics.xga2) / 2;
    const trueXG_T2 = (metrics.xg2 + metrics.xga1) / 2;

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
        const probMatrix = p1 * p2;

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

    const lambdaCorners = metrics.corners1 + metrics.corners2;
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

      const b = bookieOdd - 1;
      const q = 1 - p;
      const f = b > 0 ? (p * b - q) / b : -1;

      if (f > 0) {
        const safeFraction = f * kellyFraction;
        const recommendedBet = bankroll * safeFraction;
        setKellyRecommendation({
          edge: true,
          target: targetLabel,
          fairOdd: p > 0 ? (1 / p).toFixed(2) : '—',
          stake: recommendedBet.toFixed(2),
          pct: (safeFraction * 100).toFixed(2),
          fullKelly: (f * 100).toFixed(2),
          message: `Vantagem matemática (EV+) detectada em "${targetLabel}" @ ${bookieOdd.toFixed(2)}. Stake recomendada:`
        });
      } else {
        setKellyRecommendation({
          edge: false,
          target: targetLabel,
          fairOdd: p > 0 ? (1 / p).toFixed(2) : '—',
          stake: 0,
          pct: 0,
          message: `Aposta EV- em "${targetLabel}". A odd ${bookieOdd.toFixed(2)} está abaixo da odd justa do modelo. A matemática manda NÃO apostar.`
        });
      }

      setMcRunning(false);
    }, 50);
  };

  const toOdd = (prob) => (prob > 0.0001 ? (1 / prob).toFixed(2) : '> 1000');
  const toPct = (prob) => (prob > 0.00001 ? (prob * 100).toFixed(1) + '%' : '< 0.1%');

  const getCustomScoreData = () => {
    if (!results) return { prob: 0, odd: 0 };
    const prob = poisson(results.lambda1, customScore1) * poisson(results.lambda2, customScore2);
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
                Quant System Predictor 8.0
              </h1>
              <p className="text-slate-400 mt-1 text-sm">Duplo OCR (Estatísticas + Odds), com leitura de imagem mais robusta.</p>
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
                    <FileJson size={16}/> JSON
                  </button>
                </div>
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

              {/* Área de Importação JSON manual */}
              {showJsonInput && (
                <div className="mb-8 p-5 bg-slate-900 border border-blue-500/30 rounded-xl animate-in slide-in-from-top-2 duration-200">
                  <label className="block text-sm font-bold text-blue-400 mb-2">Cole o código JSON extraído da IA:</label>
                  <textarea
                    className="w-full h-32 bg-slate-950 border border-slate-700 rounded-lg p-3 text-sm text-emerald-400 font-mono outline-none focus:border-blue-500"
                    placeholder='{"confronto": {"equipe_mandante": "Portugal"...}}'
                    value={jsonInputData}
                    onChange={(e) => setJsonInputData(e.target.value)}
                  ></textarea>

                  <div className="flex items-center justify-between mt-3">
                    <div className="text-sm">
                      {jsonError && <span className="text-red-400 flex items-center gap-1"><X size={14}/> {jsonError}</span>}
                      {jsonSuccess && <span className="text-emerald-400 flex items-center gap-1"><Check size={14}/> {jsonSuccess}</span>}
                    </div>
                    <button
                      onClick={handleImportJson}
                      className="bg-blue-600 hover:bg-blue-500 text-white px-5 py-2 rounded-lg font-bold text-sm transition-colors"
                    >
                      Processar Dados
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

                  <div className="grid grid-cols-3 gap-3 pt-2 border-t border-slate-700 relative z-10">
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

                  <div className="grid grid-cols-3 gap-3 pt-2 border-t border-slate-700 relative z-10">
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

              <button
                onClick={runAlgorithm}
                className="mt-6 w-full bg-gradient-to-r from-emerald-600 to-blue-600 hover:from-emerald-500 hover:to-blue-500 text-white font-bold py-4 rounded-xl transition-all shadow-lg shadow-emerald-500/20 flex items-center justify-center gap-2 text-lg"
              >
                <Calculator /> Processar Precificação & Odds Justas
              </button>
            </div>

            {results && (
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 animate-in fade-in zoom-in duration-300">

                <div className="lg:col-span-2 grid grid-cols-3 gap-4 h-max">
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

                <div className="space-y-4">
                  <div className="bg-slate-800 p-4 rounded-xl border border-slate-700">
                    <h3 className="text-xs font-bold uppercase text-slate-400 mb-3 text-center flex justify-center items-center gap-2">
                      <Flag size={14} className="text-emerald-400"/> Matriz de Placares Exatos
                    </h3>
                    <div className="grid grid-cols-2 gap-2">
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
                          onChange={e => setCustomScore1(Math.max(0, parseInt(e.target.value) || 0))}
                          className="w-16 bg-slate-950 border border-slate-600 rounded-lg p-2 text-center text-2xl font-bold text-emerald-400 focus:border-emerald-500 outline-none transition-colors"
                        />
                      </div>
                      <span className="text-lg font-black text-slate-600 px-2">X</span>
                      <div className="flex flex-col items-center flex-1">
                        <span className="text-[10px] text-slate-400 mb-1 uppercase tracking-wider truncate w-20 text-center">{results.t2.name}</span>
                        <input
                          type="number" min="0" value={customScore2}
                          onChange={e => setCustomScore2(Math.max(0, parseInt(e.target.value) || 0))}
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
                        EV = (probabilidade × odd) − 1. A stake segue Kelly Fracionado ({(kellyFraction * 100).toFixed(0)}%) sobre a banca de R$ {bankroll.toFixed(2)} definida abaixo.
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
                                const stake = isEvPlus ? (bankroll * m.kellyFull * kellyFraction) : 0;
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
                      <input type="number" min="0" step="10" value={bankroll} onChange={e => setBankroll(Math.max(0, Number(e.target.value)))} className="w-full bg-slate-800 text-white p-2 mt-1 rounded font-mono" />
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
                      <input type="number" step="0.01" min="1.01" value={bookieOdd} onChange={e => setBookieOdd(Math.max(1.01, Number(e.target.value)))} className="w-full bg-slate-800 text-blue-400 p-2 mt-1 rounded font-mono font-bold" />
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
                    <div className="mt-6 grid grid-cols-3 gap-4">
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
                            Odd justa do modelo: <span className="text-slate-200">@ {kellyRecommendation.fairOdd}</span> · Odd oferecida: <span className="text-red-400">@ {bookieOdd.toFixed(2)}</span>
                          </div>
                        </div>
                      )}
                    </div>
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
                          onChange={(e) => setHandicapLine(Math.max(1, parseInt(e.target.value) || 1))}
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

      </div>
    </div>
  );
}
