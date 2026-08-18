// src/components/CoberturaOdds.jsx
// Aba de Configurações: cobertura de odds por liga/temporada, lendo direto
// das views vw_cobertura_odds / vw_cobertura_odds_bookmaker /
// vw_cobertura_odds_origem (migrations cria_views_cobertura_estatisticas_odds
// e cria_vw_cobertura_odds_origem) -- agregação pesada roda no Postgres,
// não no navegador. vw_cobertura_odds_origem atribui cada partida a UMA
// fonte só (a de maior prioridade que ela tem), pra dar pra desenhar a
// barra principal como um empilhado por fonte em vez de uma cor só.
import React, { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Loader2, RefreshCw, AlertTriangle } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { apiUrl } from '../utils/apiUrl';

function corPct(pct) {
  if (pct >= 90) return 'bg-emerald-500';
  if (pct >= 50) return 'bg-yellow-500';
  if (pct > 0) return 'bg-orange-500';
  return 'bg-slate-600';
}

const ROTULO_ORIGEM = {
  oddspapi: 'OddsPapi',
  football_data_co_uk: 'football-data.co.uk',
  kaggle_felipe: 'Kaggle (felipe)',
  kaggle_oddspedia: 'Kaggle (oddspedia)',
  footiqo: 'Footiqo',
};
function rotuloOrigem(origem) {
  return ROTULO_ORIGEM[origem] || 'legado/origem desconhecida';
}

function BarraPct({ pct }) {
  const valor = Number(pct) || 0;
  return (
    <div className="flex items-center gap-2 min-w-[110px]">
      <div className="flex-1 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full ${corPct(valor)}`} style={{ width: `${Math.min(valor, 100)}%` }} />
      </div>
      <span className="text-xs font-mono text-slate-400 w-10 text-right">{valor.toFixed(0)}%</span>
    </div>
  );
}

// Ordem = prioridade usada em vw_cobertura_odds_origem (cada partida cai
// em UM segmento só, o de maior prioridade que ela tem -- por isso a
// barra empilhada soma certinho, sem sobreposição).
// Cores escolhidas pra ficarem bem distintas entre si (evita os dois
// tons de Kaggle parecerem a mesma cor de longe, achado real testando a
// v1 desse componente).
const SEGMENTOS_ORIGEM = [
  { chave: 'com_oddspapi', origem: 'oddspapi', cor: 'bg-emerald-500' },
  { chave: 'com_football_data_co_uk', origem: 'football_data_co_uk', cor: 'bg-sky-500' },
  { chave: 'com_kaggle_felipe', origem: 'kaggle_felipe', cor: 'bg-orange-500' },
  { chave: 'com_kaggle_oddspedia', origem: 'kaggle_oddspedia', cor: 'bg-yellow-400' },
  { chave: 'com_footiqo', origem: 'footiqo', cor: 'bg-fuchsia-500' },
  { chave: 'com_legado_sem_origem', origem: null, cor: 'bg-slate-500' },
];

// Largura mínima em px pra um segmento não-zero nunca sumir visualmente
// (uma fonte com 1-2% do total virava um traço de <1px, imperceptível).
const LARGURA_MINIMA_SEGMENTO_PX = 3;

function BarraPorFonte({ finalizadas, contagens }) {
  const segmentosComDado = SEGMENTOS_ORIGEM
    .map((s) => ({ ...s, jogos: contagens[s.chave] || 0 }))
    .filter((s) => s.jogos > 0);
  if (finalizadas <= 0 || segmentosComDado.length === 0) {
    return <BarraPct pct={0} />;
  }
  const pctTotal = segmentosComDado.reduce((acc, s) => acc + s.jogos, 0) / finalizadas * 100;
  return (
    <div className="flex items-center gap-2 min-w-[130px]">
      <div className="flex-1 h-2.5 bg-slate-700 rounded-full overflow-hidden flex">
        {segmentosComDado.map((s, i) => (
          <div
            key={s.chave}
            className={`${s.cor} ${i > 0 ? 'border-l border-slate-900/60' : ''}`}
            style={{ width: `${Math.min(100, (100 * s.jogos) / finalizadas)}%`, minWidth: `${LARGURA_MINIMA_SEGMENTO_PX}px` }}
            title={`${rotuloOrigem(s.origem)}: ${s.jogos} partida(s)`}
          />
        ))}
      </div>
      <span className="text-xs font-mono text-slate-400 w-10 text-right">{Math.min(pctTotal, 100).toFixed(0)}%</span>
    </div>
  );
}

function LegendaFontes({ finalizadas, contagens }) {
  const segmentosComDado = SEGMENTOS_ORIGEM
    .map((s) => ({ ...s, jogos: contagens[s.chave] || 0 }))
    .filter((s) => s.jogos > 0)
    .sort((a, b) => b.jogos - a.jogos);
  if (segmentosComDado.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-400">
      {segmentosComDado.map((s) => (
        <span key={s.chave} className="flex items-center gap-1.5">
          <span className={`inline-block w-2 h-2 rounded-full ${s.cor}`} />
          {rotuloOrigem(s.origem)} <span className="text-slate-500">({s.jogos} · {finalizadas > 0 ? ((100 * s.jogos) / finalizadas).toFixed(0) : 0}%)</span>
        </span>
      ))}
    </div>
  );
}

export default function CoberturaOdds() {
  const [linhas, setLinhas] = useState([]);
  const [bookmakers, setBookmakers] = useState([]);
  const [porOrigem, setPorOrigem] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [atualizando, setAtualizando] = useState(false);
  const [erro, setErro] = useState('');
  const [ligaAberta, setLigaAberta] = useState(null);

  const carregarDados = async () => {
    const [{ data: odds, error: erroOdds }, { data: bks, error: erroBks }, { data: origens, error: erroOrigens }] = await Promise.all([
      supabase.from('vw_cobertura_odds').select('*').order('liga').order('season'),
      supabase.from('vw_cobertura_odds_bookmaker').select('*'),
      supabase.from('vw_cobertura_odds_origem').select('*'),
    ]);
    if (erroOdds || erroBks || erroOrigens) throw new Error((erroOdds || erroBks || erroOrigens).message);
    setLinhas(odds || []);
    setBookmakers(bks || []);
    setPorOrigem(origens || []);
  };

  useEffect(() => {
    if (!supabaseAtivo) return;
    (async () => {
      setCarregando(true); setErro('');
      try {
        await carregarDados();
      } catch (err) {
        setErro(err.message || 'Falha ao carregar a cobertura de odds.');
      } finally {
        setCarregando(false);
      }
    })();
  }, []);

  const atualizar = async () => {
    setAtualizando(true); setErro('');
    try {
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=refresh-cobertura-odds'));
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || 'Falha ao atualizar.');
      await carregarDados();
    } catch (err) {
      setErro(err.message || 'Falha ao atualizar a cobertura de odds.');
    } finally {
      setAtualizando(false);
    }
  };

  if (!supabaseAtivo) return null;
  if (carregando) {
    return <div className="flex items-center gap-2 text-slate-400 text-sm py-8 justify-center"><Loader2 size={16} className="animate-spin" /> Carregando cobertura de odds...</div>;
  }

  const porLiga = {};
  for (const l of linhas) {
    if (!porLiga[l.liga]) porLiga[l.liga] = { league_id: l.league_id, temporadas: [], finalizadas: 0, com_odds: 0, finalizadas_garantidas: 0, com_odds_garantidas: 0 };
    porLiga[l.liga].temporadas.push(l);
    porLiga[l.liga].finalizadas += l.finalizadas || 0;
    porLiga[l.liga].com_odds += l.com_odds || 0;
    porLiga[l.liga].finalizadas_garantidas += l.finalizadas_garantidas || 0;
    porLiga[l.liga].com_odds_garantidas += l.com_odds_garantidas || 0;
  }

  const contagensOrigemPorLiga = {};
  const contagensOrigemPorTemporada = {};
  for (const o of porOrigem) {
    if (!contagensOrigemPorLiga[o.league_id]) {
      contagensOrigemPorLiga[o.league_id] = { com_oddspapi: 0, com_football_data_co_uk: 0, com_kaggle_felipe: 0, com_kaggle_oddspedia: 0, com_footiqo: 0, com_legado_sem_origem: 0 };
    }
    const acc = contagensOrigemPorLiga[o.league_id];
    acc.com_oddspapi += o.com_oddspapi || 0;
    acc.com_football_data_co_uk += o.com_football_data_co_uk || 0;
    acc.com_kaggle_felipe += o.com_kaggle_felipe || 0;
    acc.com_kaggle_oddspedia += o.com_kaggle_oddspedia || 0;
    acc.com_footiqo += o.com_footiqo || 0;
    acc.com_legado_sem_origem += o.com_legado_sem_origem || 0;
    // Mesma contagem, mas por (liga, temporada) -- pra tabela expandida
    // (por temporada) mostrar a barra quebrada por fonte também, não só
    // o resumo da liga inteira.
    contagensOrigemPorTemporada[`${o.league_id}|${o.season}`] = {
      com_oddspapi: o.com_oddspapi || 0,
      com_football_data_co_uk: o.com_football_data_co_uk || 0,
      com_kaggle_felipe: o.com_kaggle_felipe || 0,
      com_kaggle_oddspedia: o.com_kaggle_oddspedia || 0,
      com_footiqo: o.com_footiqo || 0,
      com_legado_sem_origem: o.com_legado_sem_origem || 0,
    };
  }

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-4">
        <p className="text-slate-400 text-sm">
          Cobertura de <span className="text-slate-300 font-semibold">odds_market</span> por liga e temporada — % de partidas finalizadas com pelo menos uma odd gravada.
          A barra é dividida por fonte (cor = origem, ver legenda embaixo de cada liga). Dados pré-calculados (a tabela já passa de 1,5 milhão de linhas) — clique em "Atualizar" pra recalcular agora.
        </p>
        <button onClick={atualizar} disabled={atualizando}
          className="shrink-0 flex items-center gap-1.5 text-xs font-bold text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 disabled:opacity-50 border border-slate-600 rounded-lg px-3 py-1.5">
          <RefreshCw size={13} className={atualizando ? 'animate-spin' : ''} /> Atualizar
        </button>
      </div>

      {erro && (
        <div className="bg-red-950/30 border border-red-600/40 text-red-300 text-sm px-4 py-2.5 rounded-lg flex items-start gap-2">
          <AlertTriangle size={16} className="shrink-0 mt-0.5" /> {erro}
        </div>
      )}

      {Object.entries(porLiga).map(([liga, info]) => {
        // "Garantida" = partidas a partir de 2026-01-01 -- a OddsPapi documenta
        // retenção de histórico só a partir dessa data ("All historical odds
        // data since January 2026 is available"), confirmado em produção
        // (100% de sucesso real nessa janela vs. 73,5% antes dela). Só mostra
        // quando há partida antiga o suficiente pra essa distinção importar.
        const temPartidaAntiga = info.finalizadas > info.finalizadas_garantidas;
        const pctGarantida = info.finalizadas_garantidas > 0 ? (100 * info.com_odds_garantidas) / info.finalizadas_garantidas : null;
        const aberta = ligaAberta === liga;
        // Agrupado por (bookmaker, origem) -- não só bookmaker -- porque o
        // MESMO nome de casa (ex. "pinnacle") pode vir de fontes diferentes
        // (OddsPapi rica em mercados vs. football-data.co.uk/Kaggle só 1X2).
        // Ver achado em CONTEXTO_PROJETO.md/migration adiciona_origem_odds_market.
        const casasDaLiga = bookmakers
          .filter((b) => b.league_id === info.league_id)
          .reduce((acc, b) => {
            const existente = acc.find((x) => x.bookmaker === b.bookmaker && x.origem === b.origem);
            if (existente) existente.jogos += b.jogos_distintos;
            else acc.push({ bookmaker: b.bookmaker, origem: b.origem, jogos: b.jogos_distintos });
            return acc;
          }, [])
          .sort((a, b) => b.jogos - a.jogos);
        const contagensOrigem = contagensOrigemPorLiga[info.league_id] || {};

        return (
          <div key={liga} className="bg-slate-900 border border-slate-700 rounded-xl overflow-hidden">
            <button
              onClick={() => setLigaAberta(aberta ? null : liga)}
              className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-800/50 transition-colors"
            >
              <div className="flex items-center gap-2">
                {aberta ? <ChevronDown size={16} className="text-slate-500" /> : <ChevronRight size={16} className="text-slate-500" />}
                <span className="font-bold text-slate-100 text-sm">{liga}</span>
                <span className="text-xs text-slate-500">{info.temporadas.length} temporada(s) · {info.finalizadas} partida(s) finalizada(s)</span>
              </div>
              <BarraPorFonte finalizadas={info.finalizadas} contagens={contagensOrigem} />
            </button>

            <div className="px-4 pb-2 -mt-1">
              <LegendaFontes finalizadas={info.finalizadas} contagens={contagensOrigem} />
            </div>

            {temPartidaAntiga && pctGarantida != null && (
              <div className="px-4 pb-2 -mt-1 text-[11px] text-slate-500">
                Cobertura garantida pela fonte (partidas desde jan/2026): <span className="text-slate-300 font-mono">{Math.min(pctGarantida, 100).toFixed(0)}%</span> de {info.finalizadas_garantidas} partida(s) — o resto do histórico depende de dado que a OddsPapi não garante ter.
              </div>
            )}

            {aberta && (
              <div className="border-t border-slate-700 px-4 py-3 space-y-3">
                {casasDaLiga.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {casasDaLiga.map((c) => (
                      <span key={`${c.bookmaker}|${c.origem}`} className="text-[11px] font-mono bg-slate-800 border border-slate-700 rounded px-2 py-0.5 text-slate-300">
                        {c.bookmaker} <span className="text-slate-500">({c.jogos})</span>
                        <span className="text-slate-600"> · {rotuloOrigem(c.origem)}</span>
                      </span>
                    ))}
                  </div>
                )}
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-slate-500 uppercase text-[10px] tracking-wider">
                      <th className="text-left font-bold pb-1.5">Temporada</th>
                      <th className="text-right font-bold pb-1.5">Finalizadas</th>
                      <th className="text-right font-bold pb-1.5">Com odds</th>
                      <th className="text-right font-bold pb-1.5 pl-4">Cobertura</th>
                    </tr>
                  </thead>
                  <tbody>
                    {info.temporadas.map((t) => (
                      <tr key={t.season} className="border-t border-slate-800">
                        <td className="py-1.5 text-slate-300 font-mono">{t.season}</td>
                        <td className="py-1.5 text-right text-slate-400">{t.finalizadas}</td>
                        <td className="py-1.5 text-right text-slate-400">{t.com_odds}</td>
                        <td className="py-1.5 pl-4">
                          <BarraPorFonte finalizadas={t.finalizadas} contagens={contagensOrigemPorTemporada[`${info.league_id}|${t.season}`] || {}} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
