// api/sync-clubelo.js
// Roda no SERVIDOR do Vercel. Variáveis de ambiente necessárias:
//   SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY -> mesmas de sync-matches.js
//   (escrita em team_elo_external é bloqueada pra chave anon pelo RLS)
//
// Preenche public.team_elo_external com o HISTÓRICO COMPLETO de rating Elo de
// cada clube, vindo do ClubElo.com (fonte pública, sem chave de API — não é
// só um snapshot atual, o endpoint por clube devolve todas as janelas de
// rating já registradas, em geral desde décadas atrás pros clubes grandes).
//
// Casamento de nome: nossos times vêm nomeados no padrão football-data.org
// ("Manchester City FC", "Real Madrid CF"), o ClubElo usa nomes mais curtos e
// às vezes bem diferentes ("Man City", "Real Madrid", "Paris SG"). Sem chave
// de país no nosso lado (teams.country está NULL em todas as linhas hoje) pra
// cruzar, o casamento é por sobreposição de palavras significativas (após
// tirar sufixos de tipo de clube tipo "FC"/"CF"/"AFC" e anos de fundação) —
// heurística, não perfeita. Times sem casamento confiável ficam de fora da
// resposta em "sem_correspondencia" pra reconciliação manual futura.
//
// COMO CHAMAR:
//   /api/sync-clubelo?limite=15                      (processa até 15 times ainda não sincronizados)
//   /api/sync-clubelo?limite=15&forcar=true           (re-processa mesmo quem já tem histórico salvo)
//   /api/sync-clubelo?time_id=63                      (só esse time — tenta casar automaticamente no snapshot)
//   /api/sync-clubelo?time_id=63&nome_clubelo=Arsenal (só esse time, pulando o casamento automático — usa
//                                                       o nome exato do ClubElo direto, pra reconciliação manual)

import { createClient } from '@supabase/supabase-js';

function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_ROLE_KEY);
}

const SUFIXOS_IGNORADOS = new Set([
  'fc', 'cf', 'afc', 'sc', 'ac', 'as', 'ss', 'ssc', 'cfc', 'kv', 'sk', 'bc', 'acf', 'rc', 'rcd',
  'ud', 'cd', 'ca', 'ec', 'se', 'aj', 'ogc', 'ol', 'sv', 'tsg', 'vfb', 'vfl', 'fk', 'sfp', 'pae',
  'sco', 'hsc', 'gnk', 'psv', 'de', 'do', 'da', 'e', 'clube', 'club', 'calcio', 'futebol',
]);

function normalizarComEspacos(s) {
  return (s || '')
    .toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function tokensSignificativos(nome) {
  return normalizarComEspacos(nome)
    .split(' ')
    .filter(t => t && !SUFIXOS_IGNORADOS.has(t) && !/^\d+$/.test(t));
}

// Melhor candidato do snapshot do ClubElo pra um time nosso, por sobreposição de tokens.
function acharMelhorCorrespondencia(nomeTime, candidatosClubElo) {
  const tokensTime = tokensSignificativos(nomeTime);
  if (tokensTime.length === 0) return null;

  let melhor = null;
  let melhorScore = 0;
  for (const c of candidatosClubElo) {
    const tokensClube = tokensSignificativos(c.Club);
    if (tokensClube.length === 0) continue;
    const intersecao = tokensTime.filter(t => tokensClube.includes(t)).length;
    if (intersecao === 0) continue;
    const score = intersecao / Math.min(tokensTime.length, tokensClube.length);
    if (score > melhorScore) { melhorScore = score; melhor = c; }
  }
  return melhorScore >= 0.5 ? melhor : null;
}

function parseCSV(texto) {
  const linhas = texto.trim().split('\n');
  const cabecalho = linhas[0].split(',');
  return linhas.slice(1).map(linha => {
    const valores = linha.split(',');
    const obj = {};
    cabecalho.forEach((campo, i) => { obj[campo] = valores[i]; });
    return obj;
  });
}

async function buscarSnapshotAtual() {
  const hoje = new Date().toISOString().slice(0, 10);
  const resposta = await fetch(`http://api.clubelo.com/${hoje}`);
  if (!resposta.ok) throw new Error(`ClubElo snapshot falhou: HTTP ${resposta.status}`);
  return parseCSV(await resposta.text());
}

async function buscarHistoricoDoClube(nomeClubElo) {
  const resposta = await fetch(`http://api.clubelo.com/${encodeURIComponent(nomeClubElo)}`);
  if (!resposta.ok) throw new Error(`ClubElo histórico falhou (${nomeClubElo}): HTTP ${resposta.status}`);
  return parseCSV(await resposta.text());
}

// Sincroniza UM time — usada tanto no modo lote quanto no modo por-time (botão manual).
// `nomeClubeloForcado`: pula o casamento automático e usa esse nome direto (reconciliação manual).
async function sincronizarTime(supabase, time, snapshot, nomeClubeloForcado) {
  const nomeClube = nomeClubeloForcado || acharMelhorCorrespondencia(time.name, snapshot)?.Club;
  if (!nomeClube) return { time: time.name, time_id: time.id, correspondencia: false };

  const historico = await buscarHistoricoDoClube(nomeClube);
  const linhas = historico
    .filter(h => h.Elo && h.From && h.To)
    .map(h => ({
      team_id: time.id,
      clube_nome_externo: h.Club || nomeClube,
      pais: h.Country || null,
      elo: Number(h.Elo),
      valido_de: h.From,
      valido_ate: h.To,
      fonte: 'clubelo',
      atualizado_em: new Date().toISOString(),
    }));

  if (linhas.length > 0) {
    const { error: erroUpsert } = await supabase
      .from('team_elo_external')
      .upsert(linhas, { onConflict: 'fonte,clube_nome_externo,valido_de' });
    if (erroUpsert) throw erroUpsert;
  }

  return { time: time.name, time_id: time.id, correspondencia: true, clubelo: nomeClube, linhas: linhas.length };
}

export default async function handler(req, res) {
  const supabaseUrl = process.env.SUPABASE_URL, serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !serviceKey) {
    return res.status(500).json({ error: { message: 'SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY não configuradas.' } });
  }
  const supabase = getSupabase();

  try {
    // Modo por-time: usado pelo botão manual em Times.jsx, sempre força
    // (ignora o "já sincronizado"), aceita nome do ClubElo explícito.
    if (req.query.time_id) {
      const timeId = Number(req.query.time_id);
      const { data: time, error: erroTime } = await supabase.from('teams').select('id, name').eq('id', timeId).single();
      if (erroTime || !time) return res.status(404).json({ error: { message: 'Time não encontrado.' } });

      const snapshot = req.query.nome_clubelo ? null : await buscarSnapshotAtual();
      const resultado = await sincronizarTime(supabase, time, snapshot, req.query.nome_clubelo || null);
      return res.status(200).json(resultado);
    }

    // Modo lote
    const limite = Math.min(Number(req.query.limite) || 15, 50);
    const forcar = req.query.forcar === 'true';

    const { data: times, error: erroTimes } = await supabase
      .from('teams').select('id, name').or('is_national_team.is.null,is_national_team.eq.false').order('name');
    if (erroTimes) throw erroTimes;

    let timesParaProcessar = times;
    if (!forcar) {
      const { data: jaSincronizados, error: erroSinc } = await supabase.from('team_elo_external').select('team_id');
      if (erroSinc) throw erroSinc;
      const idsSincronizados = new Set((jaSincronizados || []).map(r => r.team_id));
      timesParaProcessar = times.filter(t => !idsSincronizados.has(t.id));
    }
    timesParaProcessar = timesParaProcessar.slice(0, limite);

    if (timesParaProcessar.length === 0) {
      return res.status(200).json({ mensagem: 'Nada a processar — todos os times já têm histórico (use forcar=true pra re-sincronizar).', processados: 0 });
    }

    const snapshot = await buscarSnapshotAtual();
    const resultado = { sincronizados: [], sem_correspondencia: [], erros: [] };

    for (const time of timesParaProcessar) {
      try {
        const r = await sincronizarTime(supabase, time, snapshot, null);
        if (!r.correspondencia) resultado.sem_correspondencia.push(time.name);
        else resultado.sincronizados.push({ time: r.time, clubelo: r.clubelo, linhas: r.linhas });
      } catch (e) {
        resultado.erros.push({ time: time.name, erro: e.message });
      }
    }

    res.status(200).json(resultado);
  } catch (erro) {
    res.status(500).json({ error: { message: erro.message } });
  }
}
