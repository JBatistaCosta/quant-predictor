// src/components/ModeloCartoesFaltas.jsx
// Painel "Cartões e Faltas", usado em AnaliseEstatisticaJogo.jsx.
//
// Caminho rápido (o padrão agora): scripts/prever_partidas_futuras_custom.py
// roda 1x/dia (05:00 UTC) e já pré-computa Cartões/Faltas "geral" (TODAS as
// linhas -- 1.5-6.5 e 20.5-30.5, ver MERCADOS_CARTOES_FALTAS_GERAL) pra toda
// partida agendada dentro de 30 dias nas 6 ligas de benchmark, gravando em
// `model_predictions` -- mesma tabela que AnaliseEstatisticaJogo.jsx já lê
// pro painel "Comparação modelo vs mercado". Essa é a leitura feita aqui:
// instantânea, sem GitHub Actions. Mostra TODAS as linhas que estiverem em
// cache, não só uma por mercado (achado real de uso, 11/09: o cron já
// calculava as 12 linhas, o painel só nunca mostrava mais que 1 delas).
//
// Caminho lento (fallback): quando a partida está fora da janela do cron
// (criada/agendada depois da última rodada, ou mais de 30 dias no futuro),
// não tem nada em cache ainda -- aí sim dispara a estimativa sob demanda
// (api/model-maintenance?tarefa=estimar-cartoes-faltas, com a linha
// escolhida no seletor), que aplica o modelo via GitHub Actions (~1min).
import React, { useState, useCallback, useRef, useEffect } from 'react';
import { Flag, Loader2, ChevronDown, AlertTriangle, RefreshCw } from 'lucide-react';
import { useAuth } from '../AuthContext';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { apiUrl } from '../utils/apiUrl';

const POLL_MS = 6000;

const LABEL_MERCADO = { cartoes: 'Cartões', faltas: 'Faltas' };
// Mesmas linhas de LINHAS_VALIDAS_CARTOES_FALTAS em api/model-maintenance.js
// / dh.LINHAS_CARTOES_OU/LINHAS_FALTAS_OU -- todas já pré-computadas pelo
// cron diário (MERCADOS_CARTOES_FALTAS_GERAL em prever_partidas_futuras_
// custom.py), então buscar todas de uma vez não tem custo de treino novo,
// só mais linhas na mesma tabela já lida.
const LINHAS = {
  cartoes: ['1.5', '2.5', '3.5', '4.5', '5.5', '6.5'],
  faltas: ['20.5', '22.5', '24.5', '26.5', '28.5', '30.5'],
};
const LINHA_PADRAO = { cartoes: '2.5', faltas: '22.5' };
// Algoritmo preferido quando o cache tem os dois (mesmo default do backend,
// ver ALGORITMOS_CARTOES_FALTAS em api/model-maintenance.js).
const ALGORITMO_PREFERIDO = 'xgboost';

function parseMarket(market) {
  const m = /^(cartoes|faltas)_over_under_(.+)$/.exec(market || '');
  return m ? { mercado: m[1], linha: m[2] } : null;
}

function TabelaMercado({ mercado, item }) {
  if (!item) return null;
  const titulo = `${LABEL_MERCADO[mercado] || mercado} — O/U ${item.linha}`;

  if (item.erro) {
    return (
      <div>
        <p className="text-xs font-semibold text-slate-300 mb-1">{titulo}</p>
        <p className="text-xs text-red-400 flex items-center gap-1.5"><AlertTriangle size={13} /> {item.erro}</p>
      </div>
    );
  }

  const emAndamento = item.status === 'pendente' || item.status === 'processando';

  return (
    <div>
      <p className="text-xs font-semibold text-slate-300 mb-1">
        {titulo} {item.origem === 'cache' && <span className="text-slate-600 font-normal">(cron diário)</span>}
      </p>
      {emAndamento && (
        <p className="text-xs text-slate-400 flex items-center gap-1.5">
          <Loader2 size={13} className="animate-spin" />
          {item.status === 'pendente' ? 'Na fila...' : 'Aplicando o modelo...'}
        </p>
      )}
      {item.status === 'erro' && (
        <p className="text-xs text-red-400 flex items-center gap-1.5">
          <AlertTriangle size={13} /> {item.error_message || 'Falha ao calcular a estimativa.'}
        </p>
      )}
      {item.status === 'concluido' && (
        <table className="text-xs border-collapse">
          <thead>
            <tr className="border-b border-slate-700">
              <th className="text-left text-slate-400 py-1 pr-4 font-medium">Seleção</th>
              <th className="text-center text-slate-400 py-1 px-3 font-medium">Probabilidade</th>
              <th className="text-center text-slate-400 py-1 px-3 font-medium">Odd justa</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(item.probabilities || {}).map(([sel, prob]) => (
              <tr key={sel} className="border-b border-slate-800">
                <td className="py-1 pr-4 text-white">{sel === 'over' ? 'Over' : sel === 'under' ? 'Under' : sel}</td>
                <td className="text-center px-3 font-mono text-emerald-400 py-1">{(Number(prob) * 100).toFixed(1)}%</td>
                <td className="text-center px-3 font-mono text-white py-1">
                  {item.fair_odds?.[sel] != null ? Number(item.fair_odds[sel]).toFixed(2) : (Number(prob) > 0 ? (1 / Number(prob)).toFixed(2) : '—')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// Coluna de um mercado (Cartões OU Faltas) -- uma TabelaMercado por linha em
// cache, ordenadas numericamente (Object.keys não garante ordem numérica
// pra chaves tipo "10.5" vindo depois de "2.5" como string).
function ColunaMercado({ mercado, porLinha }) {
  const linhas = Object.keys(porLinha || {}).sort((a, b) => Number(a) - Number(b));
  if (linhas.length === 0) return null;
  return (
    <div className="space-y-3">
      {linhas.map((linha) => (
        <TabelaMercado key={linha} mercado={mercado} item={porLinha[linha]} />
      ))}
    </div>
  );
}

export default function ModeloCartoesFaltas({ matchId, matchDate, mandanteId, visitanteId }) {
  const { session } = useAuth();
  const [aberto, setAberto] = useState(false);
  const [carregandoCache, setCarregandoCache] = useState(false);
  const [disparando, setDisparando] = useState(false);
  const [erroGeral, setErroGeral] = useState('');
  // { cartoes: { "1.5": {...}, "2.5": {...}, ... }, faltas: { "20.5": {...}, ... } }
  const [resultados, setResultados] = useState(null);
  const [linhaCartoesEscolhida, setLinhaCartoesEscolhida] = useState(LINHA_PADRAO.cartoes);
  const [linhaFaltasEscolhida, setLinhaFaltasEscolhida] = useState(LINHA_PADRAO.faltas);
  const pollingRef = useRef(null);

  const authHeader = session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {};

  const pararPolling = useCallback(() => {
    if (pollingRef.current) clearInterval(pollingRef.current);
    pollingRef.current = null;
  }, []);

  useEffect(() => () => pararPolling(), [pararPolling]);

  // Checa o cache (model_predictions, alimentado pelo cron diário) assim
  // que o painel abre -- na maioria das vezes já resolve tudo, sem precisar
  // de nenhum disparo. Busca TODAS as linhas de uma vez (mesma partida, uma
  // query só) -- `carregandoCache` só controla o spinner inicial, não
  // interfere no fluxo sob demanda (que tem seu próprio "disparando").
  useEffect(() => {
    if (!aberto || !matchId || resultados) return;
    let cancelado = false;
    (async () => {
      setCarregandoCache(true);
      const marketsAlvo = [
        ...LINHAS.cartoes.map((l) => `cartoes_over_under_${l}`),
        ...LINHAS.faltas.map((l) => `faltas_over_under_${l}`),
      ];
      const { data } = await supabase
        .from('model_predictions')
        .select('model_name, market, selection, probability, fair_odds')
        .eq('match_id', matchId)
        .in('market', marketsAlvo);
      if (cancelado) return;
      setCarregandoCache(false);
      if (!data || data.length === 0) return;

      const porMercadoLinha = {};
      for (const linha of data) {
        const parsed = parseMarket(linha.market);
        if (!parsed) continue;
        const chave = `${parsed.mercado}__${parsed.linha}`;
        // Prefere o algoritmo padrão quando os dois estão em cache; senão
        // fica com o primeiro que aparecer (ainda assim um modelo de
        // verdade, só não o preferido).
        const ehPreferido = linha.model_name?.endsWith(`[${ALGORITMO_PREFERIDO}]`);
        const atual = porMercadoLinha[chave];
        if (atual && atual.ehPreferido && !ehPreferido) continue;
        if (!atual || (ehPreferido && !atual.ehPreferido)) {
          porMercadoLinha[chave] = { mercado: parsed.mercado, linha: parsed.linha, ehPreferido, probabilities: {}, fair_odds: {} };
        }
        porMercadoLinha[chave].probabilities[linha.selection] = linha.probability;
        if (linha.fair_odds != null) porMercadoLinha[chave].fair_odds[linha.selection] = linha.fair_odds;
      }

      if (Object.keys(porMercadoLinha).length === 0) return;
      setResultados((atual) => {
        const proximo = { cartoes: { ...(atual?.cartoes || {}) }, faltas: { ...(atual?.faltas || {}) } };
        for (const valor of Object.values(porMercadoLinha)) {
          proximo[valor.mercado][valor.linha] = {
            linha: valor.linha, status: 'concluido', probabilities: valor.probabilities, fair_odds: valor.fair_odds, origem: 'cache',
          };
        }
        return proximo;
      });
    })();
    return () => { cancelado = true; };
  }, [aberto, matchId, resultados]);

  async function dispararEstimativa() {
    if (!mandanteId || !visitanteId) return;
    setErroGeral('');
    setDisparando(true);
    try {
      // match_id (+ match_date como 2º fator de segurança) sempre que
      // disponível -- evita que o backend resolva "a partida mais recente
      // entre esses dois times" e acabe estimando uma partida DIFERENTE da
      // que está na tela (achado real de uso, 10/09: acontecia com times
      // que se reencontram em mais de um confronto/temporada).
      const corpo = {
        linha_cartoes: linhaCartoesEscolhida, linha_faltas: linhaFaltasEscolhida,
        ...(matchId ? { match_id: matchId, match_date: matchDate } : { mandante_id: mandanteId, visitante_id: visitanteId }),
      };
      const resp = await fetch(apiUrl('/api/model-maintenance?tarefa=estimar-cartoes-faltas'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader },
        body: JSON.stringify(corpo),
      });
      const dados = await resp.json();
      if (!resp.ok) throw new Error(dados.error?.message || `HTTP ${resp.status}`);

      setResultados((atual) => {
        const proximo = { cartoes: { ...(atual?.cartoes || {}) }, faltas: { ...(atual?.faltas || {}) } };
        for (const m of dados.mercados || []) {
          proximo[m.mercado][m.linha] = m.erro
            ? { linha: m.linha, erro: m.erro }
            : { linha: m.linha, request_id: m.request_id, status: 'pendente' };
        }
        return proximo;
      });

      pararPolling();
      pollingRef.current = setInterval(async () => {
        setResultados((atual) => {
          if (!atual) return atual;
          const pendentes = [];
          for (const mercado of ['cartoes', 'faltas']) {
            for (const [linha, v] of Object.entries(atual[mercado] || {})) {
              if (v.request_id && v.status !== 'concluido' && v.status !== 'erro') pendentes.push({ mercado, linha, requestId: v.request_id });
            }
          }
          if (pendentes.length === 0) { pararPolling(); return atual; }
          (async () => {
            const ids = pendentes.map((p) => p.requestId);
            const { data } = await supabase
              .from('custom_model_ondemand_predictions')
              .select('id, status, probabilities, fair_odds, error_message')
              .in('id', ids);
            if (!data) return;
            setResultados((prev) => {
              if (!prev) return prev;
              const proximo = { cartoes: { ...prev.cartoes }, faltas: { ...prev.faltas } };
              for (const { mercado, linha } of pendentes) {
                const valor = proximo[mercado][linha];
                const encontrado = data.find((d) => d.id === valor.request_id);
                if (encontrado) proximo[mercado][linha] = { ...valor, ...encontrado };
              }
              return proximo;
            });
          })();
          return atual;
        });
      }, POLL_MS);
    } catch (e) {
      setErroGeral(e.message);
    } finally {
      setDisparando(false);
    }
  }

  if (!supabaseAtivo) return null;

  const temAlgumaLinha = (mercado) => resultados?.[mercado] && Object.keys(resultados[mercado]).length > 0;
  const algumEmAndamento = resultados
    ? ['cartoes', 'faltas'].some((m) => Object.values(resultados[m] || {}).some((v) => v.request_id && v.status !== 'concluido' && v.status !== 'erro'))
    : false;
  // Fallback sob demanda só aparece quando um dos dois mercados não tem
  // NENHUMA linha em cache (partida fora da janela do cron) -- se já tem
  // pelo menos uma linha, as outras estão só um clique de distância no
  // painel "Estimar com modelo personalizado" (linha + recorte mandante/
  // visitante), não vale duplicar esse seletor aqui pra cada linha faltante.
  const precisaBotaoSobDemanda = !carregandoCache && (!temAlgumaLinha('cartoes') || !temAlgumaLinha('faltas'));

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-4 mb-4">
      <button
        type="button"
        onClick={() => setAberto((a) => !a)}
        className="flex items-center gap-2 text-sm font-semibold text-amber-300 hover:text-amber-200 transition-colors"
      >
        <Flag size={16} />
        Cartões e Faltas
        <ChevronDown size={14} className={`transition-transform ${aberto ? 'rotate-180' : ''}`} />
      </button>

      {aberto && (
        <div className="mt-3 space-y-3">
          {carregandoCache && (
            <p className="text-xs text-slate-400 flex items-center gap-1.5">
              <Loader2 size={13} className="animate-spin" /> Buscando previsão já calculada pelo cron diário...
            </p>
          )}

          {resultados && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <ColunaMercado mercado="cartoes" porLinha={resultados.cartoes} />
              <ColunaMercado mercado="faltas" porLinha={resultados.faltas} />
            </div>
          )}

          {!session ? (
            precisaBotaoSobDemanda && (
              <p className="text-xs text-slate-500">
                Essa partida ainda não tem previsão pré-calculada. Faça login pra estimar sob demanda (leva ~1min).
              </p>
            )
          ) : (
            precisaBotaoSobDemanda && (
              <>
                <div className="flex flex-wrap items-end gap-3">
                  <div>
                    <label className="block text-[10px] uppercase font-bold text-slate-500 mb-1">Linha (Cartões)</label>
                    <select value={linhaCartoesEscolhida} onChange={(e) => setLinhaCartoesEscolhida(e.target.value)}
                      className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-100">
                      {LINHAS.cartoes.map((l) => <option key={l} value={l}>{l}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block text-[10px] uppercase font-bold text-slate-500 mb-1">Linha (Faltas)</label>
                    <select value={linhaFaltasEscolhida} onChange={(e) => setLinhaFaltasEscolhida(e.target.value)}
                      className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-100">
                      {LINHAS.faltas.map((l) => <option key={l} value={l}>{l}</option>)}
                    </select>
                  </div>
                  <button
                    type="button"
                    onClick={dispararEstimativa}
                    disabled={!mandanteId || !visitanteId || disparando || algumEmAndamento}
                    className="flex items-center gap-1.5 text-xs bg-amber-600 hover:bg-amber-500 disabled:bg-slate-700 disabled:text-slate-500 disabled:cursor-not-allowed text-white rounded-lg px-3 py-1.5 font-medium transition-colors"
                  >
                    {disparando || algumEmAndamento ? (
                      <><Loader2 size={13} className="animate-spin" /> Calculando...</>
                    ) : (
                      <><RefreshCw size={13} /> Estimar sob demanda (~1min)</>
                    )}
                  </button>
                </div>
                <p className="text-[10px] text-slate-600">
                  Essa partida ainda não tem previsão do cron diário em cache (fora da janela de 30 dias, ou criada
                  depois da última rodada às 05:00 UTC) -- por isso o cálculo sob demanda, mais lento. Pro recorte
                  mandante/visitante, use o painel "Estimar com modelo personalizado" abaixo.
                </p>
              </>
            )
          )}

          {erroGeral && (
            <p className="text-xs text-red-400 flex items-center gap-1.5"><AlertTriangle size={13} /> {erroGeral}</p>
          )}
        </div>
      )}
    </div>
  );
}
