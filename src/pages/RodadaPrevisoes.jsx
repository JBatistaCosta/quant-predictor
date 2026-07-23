// src/pages/RodadaPrevisoes.jsx — rota /rodada-atual
// Tela "rodada a rodada" do Brasileirão Série A: pra cada jogo da rodada
// selecionada (por padrão a rodada ATUAL — não existe esse conceito em
// nenhum outro lugar do sistema, calculado aqui como a primeira rodada, em
// ordem crescente, com menos da metade dos jogos finalizados — isso pula
// rodadas "quase encerradas" com só 1-2 jogos adiados reagendados pra mais
// tarde, que senão seriam confundidas com a rodada atual de verdade),
// mostra a odd justa (1/probabilidade) de TODOS os modelos do Model
// Benchmarking (`predicoes`, mesma pipeline de ModelBenchmarking.jsx) nos
// mercados 1X2 e Over/Under 2.5, lado a lado com a odd REAL da Pinnacle
// mais recente (`odds_market`, via OddsPapi).
//
// As três ações manuais abaixo já existiam antes desta tela — nenhum
// arquivo novo foi criado em api/ (o plano Hobby da Vercel já está no teto
// de 12 funções, ver skill workflow-quant-predictor):
//   - "Importar odds Pinnacle": ?tarefa=odds&liga_id=1
//   - "Disparar previsões": ?tarefa=disparar-predicoes (workflow_dispatch do
//     predict.yml no GitHub Actions — assíncrono, ~15min, exige login, e
//     NÃO é filtrável por liga: cobre as 10 partidas mais próximas entre as
//     6 ligas do pipeline, só as que já têm odds capturada)
//   - "Importar FotMob (rodada anterior)": ?tarefa=partidas-fotmob&liga_id=1
//     — só enriquece jogos JÁ FINALIZADOS. Não existe estatística FotMob
//     pré-jogo, então não faz sentido pra rodada atual/futura — a rodada
//     recém-encerrada é o alvo real (alimenta a média recente usada pelo
//     motor de AnaliseEstatisticaJogo.jsx pra prever a próxima).
import React, { useState, useEffect, useCallback } from 'react';
import { ChevronLeft, ChevronRight, Loader2, AlertTriangle, Zap, Download, RefreshCw } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { useAuth } from '../AuthContext';
import { apiUrl } from '../utils/apiUrl';
import { toOdd, toPct } from '../utils/format';
import { MODELOS_BASE, ROTULO_MODELO } from '../utils/modelosBenchmarking';
import PainelInfoModelo, { BotaoInfoModelo } from '../components/PainelInfoModelo';

const LIGA_ID = 1; // Brasileirão Série A

const ROTULO_STATUS = {
  scheduled: 'Agendado', live: 'Ao vivo', finished: 'Encerrado',
  postponed: 'Adiado', cancelled: 'Cancelado',
};

function calcularRodadaAtual(porRodada) {
  const rodadas = Object.keys(porRodada).map(Number).sort((a, b) => a - b);
  for (const r of rodadas) {
    const { total, finalizados } = porRodada[r];
    if (total > 0 && finalizados / total < 0.5) return r;
  }
  return rodadas.length ? rodadas[rodadas.length - 1] : null;
}

function indexarPrevisoes(rows) {
  const porJogo = {};
  for (const p of rows) {
    if (!porJogo[p.match_id]) porJogo[p.match_id] = {};
    if (!porJogo[p.match_id][p.model_name]) porJogo[p.match_id][p.model_name] = {};
    porJogo[p.match_id][p.model_name][p.mercado] = p;
  }
  return porJogo;
}

function indexarOddsPinnacle(rows) {
  // rows vem ordenado captured_at desc -- a primeira ocorrência de cada
  // chave (match_id, market, selection) é a mais recente, então vence.
  const porJogo = {};
  for (const r of rows) {
    if (!porJogo[r.match_id]) porJogo[r.match_id] = {};
    const chave = `${r.market}|${r.selection}`;
    if (!(chave in porJogo[r.match_id])) porJogo[r.match_id][chave] = r.odds;
  }
  return porJogo;
}

function formatarMensagemAcao(tarefa, corpo) {
  if (tarefa === 'odds') {
    if (corpo.pulado) return corpo.motivo;
    const porBookmaker = (corpo.por_bookmaker || [])
      .map((b) => `${b.bookmaker}: ${b.linhas_inseridas ?? 0} linha(s)${b.erro ? ` (erro: ${b.erro})` : ''}`)
      .join(' · ');
    return `Odds importadas — ${corpo.jogos_candidatos ?? 0} jogo(s) na janela de ${corpo.janela_dias ?? '?'} dias, ${corpo.linhas_inseridas ?? 0} linha(s) inserida(s). ${porBookmaker}`;
  }
  if (tarefa === 'partidas-fotmob') {
    if (corpo.mensagem) return corpo.mensagem;
    return `FotMob — ${corpo.processados_agora ?? 0} jogo(s) processado(s) agora (${corpo.sucesso ?? 0} com sucesso), ${corpo.restantes ?? 0} restante(s) no backlog.`;
  }
  if (tarefa === 'disparar-predicoes') {
    return 'Disparado! Roda no GitHub Actions (até ~15 min) e cobre as 10 partidas mais próximas entre as 6 ligas do pipeline, só entre as que já têm odds capturada — clique em "Recarregar" daqui a pouco.';
  }
  return 'Concluído.';
}

function Celula({ prob }) {
  if (prob == null) return <span className="text-slate-600">—</span>;
  return (
    <span>
      <span className="font-semibold text-slate-100">{toOdd(prob)}</span>{' '}
      <span className="text-slate-500 text-xs">({toPct(prob)})</span>
    </span>
  );
}

export default function RodadaPrevisoes() {
  const { session } = useAuth();
  const [temporada, setTemporada] = useState(null);
  const [rodadaAtual, setRodadaAtual] = useState(null);
  const [rodadaExibida, setRodadaExibida] = useState(null);
  const [ultimaRodada, setUltimaRodada] = useState(38);
  const [jogos, setJogos] = useState([]);
  const [previsoesPorJogo, setPrevisoesPorJogo] = useState({});
  const [pinnaclePorJogo, setPinnaclePorJogo] = useState({});
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState(null);
  const [acao, setAcao] = useState(null);
  const [mensagemAcao, setMensagemAcao] = useState(null);
  const [modeloInfo, setModeloInfo] = useState(null);

  const carregarRodada = useCallback(async (temporadaAlvo, rodadaAlvo) => {
    setCarregando(true);
    setErro(null);
    try {
      const { data: todosOsJogos, error: erroJogos } = await supabase
        .from('matches')
        .select('id, round, status, match_date, home_team_id, away_team_id')
        .eq('league_id', LIGA_ID).eq('season', temporadaAlvo)
        .not('round', 'is', null).neq('status', 'cancelled');
      if (erroJogos) throw erroJogos;

      const porRodada = {};
      for (const j of todosOsJogos || []) {
        if (!porRodada[j.round]) porRodada[j.round] = { total: 0, finalizados: 0 };
        porRodada[j.round].total++;
        if (j.status === 'finished') porRodada[j.round].finalizados++;
      }
      const rodadas = Object.keys(porRodada).map(Number).sort((a, b) => a - b);
      setUltimaRodada(rodadas.length ? rodadas[rodadas.length - 1] : 38);

      const atual = calcularRodadaAtual(porRodada);
      setRodadaAtual(atual);
      const alvo = rodadaAlvo ?? atual;
      setRodadaExibida(alvo);

      const jogosDaRodada = (todosOsJogos || [])
        .filter((j) => j.round === alvo)
        .sort((a, b) => new Date(a.match_date) - new Date(b.match_date));

      if (jogosDaRodada.length === 0) {
        setJogos([]); setPrevisoesPorJogo({}); setPinnaclePorJogo({});
        return;
      }

      const idsTimes = [...new Set(jogosDaRodada.flatMap((j) => [j.home_team_id, j.away_team_id]))];
      const idsJogos = jogosDaRodada.map((j) => j.id);

      const [{ data: times }, { data: previsoes }, { data: oddsPinnacle }] = await Promise.all([
        supabase.from('teams').select('id, name').in('id', idsTimes),
        supabase.from('predicoes')
          .select('match_id, model_name, mercado, prob_home, prob_draw, prob_away, prob_over, prob_under')
          .in('match_id', idsJogos).in('model_name', MODELOS_BASE),
        supabase.from('odds_market')
          .select('match_id, market, selection, odds, captured_at')
          .in('match_id', idsJogos).eq('bookmaker', 'pinnacle')
          .order('captured_at', { ascending: false }),
      ]);

      const nomeTime = Object.fromEntries((times || []).map((t) => [t.id, t.name]));
      setJogos(jogosDaRodada.map((j) => ({
        ...j,
        mandante: nomeTime[j.home_team_id] || `#${j.home_team_id}`,
        visitante: nomeTime[j.away_team_id] || `#${j.away_team_id}`,
      })));
      setPrevisoesPorJogo(indexarPrevisoes(previsoes || []));
      setPinnaclePorJogo(indexarOddsPinnacle(oddsPinnacle || []));
    } catch (e) {
      setErro(e.message);
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => {
    if (!supabaseAtivo) { setErro('Supabase não configurado.'); setCarregando(false); return; }
    (async () => {
      const { data } = await supabase.from('matches').select('season')
        .eq('league_id', LIGA_ID).order('season', { ascending: false }).limit(1);
      const temp = data?.[0]?.season;
      if (!temp) { setErro('Nenhuma temporada encontrada pro Brasileirão.'); setCarregando(false); return; }
      setTemporada(temp);
      carregarRodada(temp, null);
    })();
  }, [carregarRodada]);

  function mudarRodada(delta) {
    if (rodadaExibida == null || !temporada) return;
    const nova = Math.min(Math.max(rodadaExibida + delta, 1), ultimaRodada);
    if (nova === rodadaExibida) return;
    carregarRodada(temporada, nova);
  }

  async function acionar(tarefa, { auth = false } = {}) {
    setAcao(tarefa);
    setMensagemAcao(null);
    try {
      let url = `/api/model-maintenance?tarefa=${tarefa}`;
      if (tarefa === 'odds') url += `&liga_id=${LIGA_ID}`;
      if (tarefa === 'partidas-fotmob') url += `&liga_id=${LIGA_ID}&temporada=${temporada}&limite=15`;
      const opcoes = { method: tarefa === 'disparar-predicoes' ? 'POST' : 'GET' };
      if (auth) opcoes.headers = { Authorization: `Bearer ${session?.access_token || ''}` };
      const resp = await fetch(apiUrl(url), opcoes);
      const corpo = await resp.json();
      if (!resp.ok) throw new Error(corpo?.error?.message || `HTTP ${resp.status}`);
      setMensagemAcao({ tipo: 'ok', texto: formatarMensagemAcao(tarefa, corpo) });
    } catch (e) {
      setMensagemAcao({ tipo: 'erro', texto: e.message });
    } finally {
      setAcao(null);
    }
  }

  return (
    <div className="max-w-6xl mx-auto p-4 md:p-6 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-100">Brasileirão Série A — Rodada a Rodada</h1>
          <p className="text-sm text-slate-500">
            Odd justa de todos os modelos do Model Benchmarking (1X2 + Over/Under 2.5), lado a lado com a odd real
            da Pinnacle mais recente importada.
          </p>
        </div>
        <button
          onClick={() => carregarRodada(temporada, rodadaExibida)}
          disabled={carregando || !temporada}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm border border-slate-700 disabled:opacity-50"
        >
          <RefreshCw size={14} className={carregando ? 'animate-spin' : ''} /> Recarregar
        </button>
      </div>

      <div className="flex items-center justify-center gap-3 bg-slate-900 border border-slate-700/50 rounded-lg py-3">
        <button
          onClick={() => mudarRodada(-1)}
          disabled={carregando || !rodadaExibida || rodadaExibida <= 1}
          className="p-2 rounded-lg hover:bg-slate-800 disabled:opacity-30 text-slate-300"
        >
          <ChevronLeft size={18} />
        </button>
        <div className="text-center">
          <div className="text-lg font-bold text-slate-100">Rodada {rodadaExibida ?? '—'}</div>
          {rodadaAtual != null && rodadaExibida !== rodadaAtual && (
            <button onClick={() => carregarRodada(temporada, rodadaAtual)} className="text-xs text-emerald-400 hover:underline">
              voltar pra rodada atual ({rodadaAtual})
            </button>
          )}
        </div>
        <button
          onClick={() => mudarRodada(1)}
          disabled={carregando || !rodadaExibida || rodadaExibida >= ultimaRodada}
          className="p-2 rounded-lg hover:bg-slate-800 disabled:opacity-30 text-slate-300"
        >
          <ChevronRight size={18} />
        </button>
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => acionar('odds')}
          disabled={acao !== null}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-sky-700 hover:bg-sky-600 text-white text-sm font-medium disabled:opacity-50"
        >
          {acao === 'odds' ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
          Importar odds Pinnacle
        </button>
        <button
          onClick={() => acionar('disparar-predicoes', { auth: true })}
          disabled={acao !== null || !session}
          title={!session ? 'Faça login pra disparar as previsões.' : ''}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {acao === 'disparar-predicoes' ? <Loader2 size={16} className="animate-spin" /> : <Zap size={16} />}
          Disparar previsões
        </button>
        <button
          onClick={() => acionar('partidas-fotmob')}
          disabled={acao !== null}
          title="Enriquece os jogos JÁ FINALIZADOS (rodada anterior) com estatística do FotMob — não existe dado pré-jogo."
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm font-medium border border-slate-700 disabled:opacity-50"
        >
          {acao === 'partidas-fotmob' ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
          Importar FotMob (rodada anterior)
        </button>
      </div>

      {mensagemAcao && (
        <div className={`rounded-lg border p-3 text-sm ${mensagemAcao.tipo === 'ok' ? 'bg-emerald-950/40 border-emerald-800 text-emerald-300' : 'bg-red-950/40 border-red-800 text-red-300'}`}>
          {mensagemAcao.texto}
        </div>
      )}

      {erro && (
        <div className="flex items-center gap-2 rounded-lg border border-red-800 bg-red-950/40 p-3 text-sm text-red-300">
          <AlertTriangle size={16} /> {erro}
        </div>
      )}

      {carregando ? (
        <div className="flex items-center gap-2 text-slate-500 text-sm py-8 justify-center">
          <Loader2 size={16} className="animate-spin" /> Carregando rodada...
        </div>
      ) : jogos.length === 0 ? (
        <div className="text-center text-slate-500 text-sm py-12 border border-dashed border-slate-700 rounded-lg">
          Nenhum jogo encontrado pra essa rodada.
        </div>
      ) : (
        <div className="space-y-4">
          {jogos.map((j) => {
            const previsoesJogo = previsoesPorJogo[j.id] || {};
            const pinnacleJogo = pinnaclePorJogo[j.id] || {};
            return (
              <div key={j.id} className="bg-slate-900 border border-slate-700/50 rounded-lg overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 flex items-center justify-between text-sm">
                  <span className="font-medium text-slate-200">{j.mandante} x {j.visitante}</span>
                  <span className="text-slate-500 text-xs flex items-center gap-2">
                    {j.match_date ? new Date(j.match_date).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''}
                    <span className="px-1.5 py-0.5 rounded bg-slate-700/60 text-slate-300">{ROTULO_STATUS[j.status] || j.status}</span>
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-slate-500 text-xs uppercase">
                        <th className="text-left px-4 py-2">Modelo</th>
                        <th className="text-right px-4 py-2">Casa</th>
                        <th className="text-right px-4 py-2">Empate</th>
                        <th className="text-right px-4 py-2">Fora</th>
                        <th className="text-right px-4 py-2">Over 2.5</th>
                        <th className="text-right px-4 py-2">Under 2.5</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr className="border-t border-slate-800 bg-sky-950/30">
                        <td className="px-4 py-2 text-sky-300 font-medium">Pinnacle (odd real)</td>
                        <td className="px-4 py-2 text-right text-sky-200 font-semibold">{pinnacleJogo['1X2|home']?.toFixed(2) ?? '—'}</td>
                        <td className="px-4 py-2 text-right text-sky-200 font-semibold">{pinnacleJogo['1X2|draw']?.toFixed(2) ?? '—'}</td>
                        <td className="px-4 py-2 text-right text-sky-200 font-semibold">{pinnacleJogo['1X2|away']?.toFixed(2) ?? '—'}</td>
                        <td className="px-4 py-2 text-right text-sky-200 font-semibold">{pinnacleJogo['over_under_2.5|over']?.toFixed(2) ?? '—'}</td>
                        <td className="px-4 py-2 text-right text-sky-200 font-semibold">{pinnacleJogo['over_under_2.5|under']?.toFixed(2) ?? '—'}</td>
                      </tr>
                      {MODELOS_BASE.map((nome) => {
                        const p1x2 = previsoesJogo[nome]?.['1X2'];
                        const pou = previsoesJogo[nome]?.['over_under_2.5'];
                        return (
                          <tr key={nome} className="border-t border-slate-800">
                            <td className="px-4 py-2 text-slate-300">
                              <span className="inline-flex items-center gap-1">
                                {ROTULO_MODELO[nome]}
                                <BotaoInfoModelo onClick={() => setModeloInfo(nome)} />
                              </span>
                            </td>
                            <td className="px-4 py-2 text-right"><Celula prob={p1x2?.prob_home} /></td>
                            <td className="px-4 py-2 text-right"><Celula prob={p1x2?.prob_draw} /></td>
                            <td className="px-4 py-2 text-right"><Celula prob={p1x2?.prob_away} /></td>
                            <td className="px-4 py-2 text-right"><Celula prob={pou?.prob_over} /></td>
                            <td className="px-4 py-2 text-right"><Celula prob={pou?.prob_under} /></td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <p className="text-[11px] text-slate-600">
        Odd justa = 1/probabilidade do modelo (sem margem de casa) — não é a mesma coisa que a odd real da Pinnacle,
        que já embute a margem da casa. "Disparar previsões" não é filtrável só pro Brasileirão: importe a odds da
        Pinnacle primeiro pra aumentar a chance dos jogos desta rodada entrarem nas 10 vagas diárias.
      </p>

      <PainelInfoModelo
        modelName={modeloInfo}
        rotulo={ROTULO_MODELO[modeloInfo] || modeloInfo}
        aberto={!!modeloInfo}
        onFechar={() => setModeloInfo(null)}
      />
    </div>
  );
}
