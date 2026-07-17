// src/pages/AnaliseEstatisticaJogo.jsx — rota /estatisticas/:matchId
// Painel de análise estatística voltado pra apostas esportivas, linkado a
// partir de AnaliseHistorica.jsx (/historico/:matchId). Diferente daquela
// página (forma recente + confronto direto) e de AnaliseEvento.jsx (calculadora
// manual com modelo Dixon-Coles rodado à mão), aqui TUDO é importado
// automaticamente do pipeline — nada de entrada manual/OCR:
//   1. Tendências de mercado por time (Over 2.5%, Ambas Marcam%, médias de
//      escanteios/cartões nos últimos N jogos) — o "cheat sheet" de apostador.
//   2. Comparação modelo (model_predictions) vs mercado (odds_market devigada)
//      pra essa partida específica, com o edge calculado.
//   3. Histórico de precisão do modelo (api/model-stats.js) filtrado pra
//      liga/mercado dessa partida, como contexto de confiabilidade.
import React, { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, AlertTriangle, Shield, Loader2, TrendingUp, Target, History } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

// Paleta categórica já validada no projeto (ver RatingClubes.jsx) — ordem fixa.
const COR_MODELO = '#3987e5';   // azul (PALETA[0])
const COR_MERCADO = '#c98500';  // âmbar (PALETA[3]) — evita colidir com o verde
                                 // já usado em outras telas pra "melhor odd"/edge positivo

const OPCOES_N = [10, 20, 38];
const MERCADO_ROTULO = { '1X2': '1X2', 'over_under_2.5': 'Over/Under 2,5 gols', 'corners_over_under_9.5': 'Escanteios O/U 9,5' };
const SELECAO_ROTULO = { home: 'Mandante', draw: 'Empate', away: 'Visitante', over: 'Over', under: 'Under' };

function Escudo({ url, tamanho = 20 }) {
  return url
    ? <img src={url} alt="" className="object-contain shrink-0" style={{ width: tamanho, height: tamanho }} />
    : <Shield size={tamanho * 0.8} className="text-slate-700 shrink-0" />;
}

// Últimos N jogos finalizados do time (qualquer competição) + estatísticas próprias.
async function buscarTendenciasTime(teamId, antesDe, n) {
  const { data } = await supabase
    .from('matches')
    .select('id, home_team_id, away_team_id, home_goals, away_goals')
    .or(`home_team_id.eq.${teamId},away_team_id.eq.${teamId}`)
    .eq('status', 'finished')
    .lt('match_date', antesDe)
    .order('match_date', { ascending: false })
    .limit(n);

  const jogos = data || [];
  if (jogos.length === 0) return null;

  const matchIds = jogos.map(j => j.id);
  const { data: stats } = matchIds.length > 0
    ? await supabase.from('match_stats').select('match_id, team_id, corners, yellow_cards, red_cards').in('match_id', matchIds)
    : { data: [] };
  const statsPorJogo = {};
  (stats || []).filter(s => s.team_id === teamId).forEach(s => { statsPorJogo[s.match_id] = s; });

  let over25 = 0, btts = 0;
  let somaCorners = 0, nCorners = 0, somaCartoes = 0, nCartoes = 0;

  jogos.forEach(j => {
    const total = (j.home_goals ?? 0) + (j.away_goals ?? 0);
    if (total > 2.5) over25++;
    if ((j.home_goals ?? 0) > 0 && (j.away_goals ?? 0) > 0) btts++;

    const s = statsPorJogo[j.id];
    if (s?.corners != null) { somaCorners += Number(s.corners); nCorners++; }
    if (s?.yellow_cards != null) {
      somaCartoes += Number(s.yellow_cards) + Number(s.red_cards || 0);
      nCartoes++;
    }
  });

  return {
    n: jogos.length,
    pctOver25: over25 / jogos.length,
    pctBtts: btts / jogos.length,
    mediaCorners: nCorners > 0 ? somaCorners / nCorners : null,
    mediaCartoes: nCartoes > 0 ? somaCartoes / nCartoes : null,
  };
}

function devigar(oddsPorSelecao) {
  const implicitas = {};
  let soma = 0;
  for (const [sel, odd] of Object.entries(oddsPorSelecao)) {
    implicitas[sel] = 1 / odd;
    soma += implicitas[sel];
  }
  const normalizadas = {};
  for (const sel of Object.keys(implicitas)) normalizadas[sel] = implicitas[sel] / soma;
  return normalizadas;
}

// Previsões do modelo pra essa partida + a melhor fonte de odds disponível
// (prioriza a média de mercado no fechamento; sem isso, cai pra qualquer casa).
async function buscarComparacaoModeloMercado(matchId) {
  const [{ data: previsoes }, { data: oddsRows }] = await Promise.all([
    supabase.from('model_predictions').select('model_name, market, selection, probability').eq('match_id', matchId),
    supabase.from('odds_market').select('bookmaker, market, selection, odds, snapshot, captured_at').eq('match_id', matchId).order('captured_at', { ascending: false }),
  ]);
  if (!previsoes || previsoes.length === 0) return [];

  const porMercado = {};
  (oddsRows || []).forEach(r => {
    if (!porMercado[r.market]) porMercado[r.market] = {};
    const fonte = r.bookmaker === 'media_mercado' && r.snapshot === 'closing' ? 'preferencial' : 'fallback';
    if (!porMercado[r.market][fonte]) porMercado[r.market][fonte] = {};
    if (!(r.selection in porMercado[r.market][fonte])) porMercado[r.market][fonte][r.selection] = r.odds;
  });

  const probMercadoPorMercado = {};
  const fonteUsadaPorMercado = {};
  Object.entries(porMercado).forEach(([mercado, fontes]) => {
    const oddsEscolhidas = fontes.preferencial || fontes.fallback;
    if (oddsEscolhidas) {
      probMercadoPorMercado[mercado] = devigar(oddsEscolhidas);
      fonteUsadaPorMercado[mercado] = fontes.preferencial ? 'Média de mercado (fechamento)' : 'Casa individual (mais recente)';
    }
  });

  const porModeloMercado = {};
  previsoes.forEach(p => {
    const chave = `${p.model_name}__${p.market}`;
    if (!porModeloMercado[chave]) porModeloMercado[chave] = { model_name: p.model_name, market: p.market, selecoes: [] };
    porModeloMercado[chave].selecoes.push({
      selecao: p.selection,
      pModelo: Number(p.probability),
      pMercado: probMercadoPorMercado[p.market]?.[p.selection] ?? null,
    });
  });

  return Object.values(porModeloMercado).map(g => ({
    ...g,
    fonteOdds: fonteUsadaPorMercado[g.market] || null,
  }));
}

async function buscarPrecisaoModelo(ligaId) {
  try {
    const resposta = await fetch(`/api/model-stats?liga_id=${ligaId}`);
    if (!resposta.ok) return [];
    const dados = await resposta.json();
    return dados.grupos || [];
  } catch {
    return [];
  }
}

function BarraProgresso({ pct, cor = '#10b981' }) {
  return (
    <div className="w-full h-1.5 bg-slate-900 rounded-full overflow-hidden">
      <div className="h-full rounded-full" style={{ width: `${Math.max(0, Math.min(100, pct * 100))}%`, backgroundColor: cor }} />
    </div>
  );
}

function CardTendencia({ titulo, valor, sufixo = '', pct = null, cor = '#10b981' }) {
  return (
    <div className="bg-slate-900 rounded-lg p-3">
      <div className="text-[10px] text-slate-500 uppercase mb-1">{titulo}</div>
      <div className="text-lg font-bold text-slate-200 mb-1.5">{valor != null ? `${valor}${sufixo}` : '—'}</div>
      {pct != null && <BarraProgresso pct={pct} cor={cor} />}
    </div>
  );
}

function PainelTendencias({ nome, crestUrl, tendencias, ladoEsquerda }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5">
      <div className={`flex items-center gap-3 mb-4 ${ladoEsquerda ? '' : 'flex-row-reverse text-right'}`}>
        <div className="w-10 h-10 rounded-xl bg-slate-900 border border-slate-700 flex items-center justify-center overflow-hidden shrink-0">
          {crestUrl ? <img src={crestUrl} alt="" className="w-full h-full object-contain" /> : <Shield className="text-slate-600" size={18} />}
        </div>
        <h2 className="font-bold text-slate-100 text-sm">{nome}</h2>
      </div>

      {!tendencias ? (
        <p className="text-xs text-slate-600">Sem jogos suficientes no histórico.</p>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <CardTendencia titulo="Over 2,5 gols" valor={(tendencias.pctOver25 * 100).toFixed(0)} sufixo="%" pct={tendencias.pctOver25} cor="#10b981" />
          <CardTendencia titulo="Ambas marcam" valor={(tendencias.pctBtts * 100).toFixed(0)} sufixo="%" pct={tendencias.pctBtts} cor="#10b981" />
          <CardTendencia titulo="Escanteios (méd.)" valor={tendencias.mediaCorners?.toFixed(1)} />
          <CardTendencia titulo="Cartões (méd.)" valor={tendencias.mediaCartoes?.toFixed(1)} />
        </div>
      )}
      <p className="text-[10px] text-slate-600 mt-3">Últimos {tendencias?.n ?? 0} jogos (qualquer competição).</p>
    </div>
  );
}

function LinhaModeloMercado({ selecao, pModelo, pMercado }) {
  const edge = pMercado != null ? pModelo - pMercado : null;
  return (
    <div className="py-2">
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="text-slate-300 font-semibold">{SELECAO_ROTULO[selecao] || selecao}</span>
        {edge != null && (
          <span className={`font-mono text-[11px] font-bold ${edge > 0 ? 'text-emerald-400' : edge < 0 ? 'text-red-400' : 'text-slate-500'}`}>
            {edge > 0 ? '+' : ''}{(edge * 100).toFixed(1)}pp
          </span>
        )}
      </div>
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-slate-500 w-14 shrink-0">Modelo</span>
          <BarraProgresso pct={pModelo} cor={COR_MODELO} />
          <span className="text-[11px] font-mono text-slate-400 w-10 text-right shrink-0">{(pModelo * 100).toFixed(0)}%</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-slate-500 w-14 shrink-0">Mercado</span>
          {pMercado != null
            ? <BarraProgresso pct={pMercado} cor={COR_MERCADO} />
            : <div className="w-full h-1.5 bg-slate-900 rounded-full" />}
          <span className="text-[11px] font-mono text-slate-400 w-10 text-right shrink-0">{pMercado != null ? `${(pMercado * 100).toFixed(0)}%` : '—'}</span>
        </div>
      </div>
    </div>
  );
}

function PainelModeloMercado({ grupos }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
          <Target className="text-emerald-400" size={16} /> Modelo vs. mercado
        </h2>
        {grupos.length > 0 && (
          <div className="flex items-center gap-3 text-[10px] text-slate-500">
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ backgroundColor: COR_MODELO }} /> Modelo</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ backgroundColor: COR_MERCADO }} /> Mercado</span>
          </div>
        )}
      </div>

      {grupos.length === 0 ? (
        <p className="text-xs text-slate-600">Sem previsão do modelo salva pra essa partida ainda.</p>
      ) : (
        <div className="space-y-4">
          {grupos.map(g => (
            <div key={`${g.model_name}__${g.market}`}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] uppercase font-bold text-slate-500">{MERCADO_ROTULO[g.market] || g.market}</span>
                <span className="text-[10px] text-slate-600">{g.model_name}</span>
              </div>
              <div className="divide-y divide-slate-700/50">
                {g.selecoes.map(s => <LinhaModeloMercado key={s.selecao} {...s} />)}
              </div>
              {g.fonteOdds && <p className="text-[10px] text-slate-600 mt-1">Odds: {g.fonteOdds}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PainelPrecisaoModelo({ grupos }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5">
      <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-2">
        <History className="text-emerald-400" size={16} /> Histórico de precisão do modelo
      </h2>

      {grupos.length === 0 ? (
        <p className="text-xs text-slate-600">Sem histórico de avaliação do modelo pra essa liga/mercado ainda.</p>
      ) : (
        <div className="space-y-3">
          {grupos.map(g => (
            <div key={`${g.model_name}__${g.market}`} className="bg-slate-900 rounded-lg p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-slate-300">{MERCADO_ROTULO[g.market] || g.market}</span>
                <span className="text-[10px] text-slate-600">{g.model_name} · {g.n_jogos} jogos avaliados</span>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div>
                  <div className="text-[10px] text-slate-500 uppercase">Log-loss</div>
                  <div className="text-xs font-mono text-slate-300">
                    {g.log_loss_modelo?.toFixed(3) ?? '—'}
                    {g.log_loss_mercado != null && <span className="text-slate-600"> / {g.log_loss_mercado.toFixed(3)}</span>}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-500 uppercase">Brier</div>
                  <div className="text-xs font-mono text-slate-300">
                    {g.brier_modelo?.toFixed(3) ?? '—'}
                    {g.brier_mercado != null && <span className="text-slate-600"> / {g.brier_mercado.toFixed(3)}</span>}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-500 uppercase">Acurácia</div>
                  <div className="text-xs font-mono text-slate-300">
                    {g.accuracy_modelo != null ? `${(g.accuracy_modelo * 100).toFixed(0)}%` : '—'}
                    {g.accuracy_mercado != null && <span className="text-slate-600"> / {(g.accuracy_mercado * 100).toFixed(0)}%</span>}
                  </div>
                </div>
              </div>
            </div>
          ))}
          <p className="text-[10px] text-slate-600">Modelo / mercado, quando a comparação existe. Métricas mais baixas (log-loss, Brier) são melhores.</p>
        </div>
      )}
    </div>
  );
}

export default function AnaliseEstatisticaJogo() {
  const { matchId } = useParams();
  const [jogo, setJogo] = useState(null);
  const [n, setN] = useState(20);
  const [tendenciasMandante, setTendenciasMandante] = useState(null);
  const [tendenciasVisitante, setTendenciasVisitante] = useState(null);
  const [comparacaoModelo, setComparacaoModelo] = useState([]);
  const [precisaoModelo, setPrecisaoModelo] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');

  useEffect(() => {
    if (!supabaseAtivo) return;
    (async () => {
      setCarregando(true);
      setErro('');
      const { data: j, error: erroJogo } = await supabase
        .from('matches')
        .select('id, match_date, league_id, home_team_id, away_team_id, leagues(name), home:teams!matches_home_team_id_fkey(id,name,crest_url), away:teams!matches_away_team_id_fkey(id,name,crest_url)')
        .eq('id', matchId)
        .single();

      if (erroJogo || !j) { setErro('Jogo não encontrado.'); setCarregando(false); return; }
      setJogo(j);

      const referencia = j.match_date || new Date().toISOString();
      const [tM, tV, comparacao, precisao] = await Promise.all([
        buscarTendenciasTime(j.home_team_id, referencia, n),
        buscarTendenciasTime(j.away_team_id, referencia, n),
        buscarComparacaoModeloMercado(j.id),
        buscarPrecisaoModelo(j.league_id),
      ]);
      setTendenciasMandante(tM);
      setTendenciasVisitante(tV);
      setComparacaoModelo(comparacao);

      const mercadosDaPartida = new Set(comparacao.map(c => c.market));
      const modelosDaPartida = new Set(comparacao.map(c => c.model_name));
      setPrecisaoModelo((precisao || []).filter(g => mercadosDaPartida.has(g.market) && modelosDaPartida.has(g.model_name)));

      setCarregando(false);
    })();
  }, [matchId, n]);

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
      <Link to={`/historico/${jogo.id}`} className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 text-sm mb-4 w-fit">
        <ArrowLeft size={16} /> Voltar pro confronto
      </Link>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
        <p className="text-center text-xs text-slate-500 uppercase tracking-wider mb-1 flex items-center justify-center gap-1.5">
          <TrendingUp size={12} className="text-emerald-400" /> Análise estatística · {jogo.leagues?.name || 'Confronto'}
        </p>
        <div className="flex items-center justify-center gap-4 mt-2">
          <div className="flex items-center gap-2">
            <Escudo url={jogo.home?.crest_url} tamanho={28} />
            <span className="font-bold text-slate-100">{jogo.home?.name}</span>
          </div>
          <span className="text-slate-500 text-xs">vs</span>
          <div className="flex items-center gap-2">
            <span className="font-bold text-slate-100">{jogo.away?.name}</span>
            <Escudo url={jogo.away?.crest_url} tamanho={28} />
          </div>
        </div>

        <div className="flex items-center justify-center gap-2 mt-4">
          <span className="text-xs text-slate-500 mr-1">Tendências dos últimos:</span>
          {OPCOES_N.map(opcao => (
            <button
              key={opcao}
              onClick={() => setN(opcao)}
              className={`px-3 py-1 rounded-lg text-xs font-bold ${n === opcao ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-900 text-slate-500 hover:text-slate-300'}`}
            >
              {opcao} jogos
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <PainelTendencias nome={jogo.home?.name} crestUrl={jogo.home?.crest_url} tendencias={tendenciasMandante} ladoEsquerda />
        <PainelTendencias nome={jogo.away?.name} crestUrl={jogo.away?.crest_url} tendencias={tendenciasVisitante} ladoEsquerda={false} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <PainelModeloMercado grupos={comparacaoModelo} />
        <PainelPrecisaoModelo grupos={precisaoModelo} />
      </div>
    </div>
  );
}
