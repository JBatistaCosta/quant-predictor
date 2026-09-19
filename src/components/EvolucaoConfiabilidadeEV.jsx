// src/components/EvolucaoConfiabilidadeEV.jsx
// Evolução diária do ROI médio (com IC95% via bootstrap) das células "odd x
// edge" que estão CONFIÁVEIS no snapshot mais recente de
// matriz_confiabilidade_ev_historico -- pedido do usuário: acompanhar se o
// achado de Cartões (linhas 4.5/5.5, odd 1.30-2.50, edge 15-25%+, ver
// CONTEXTO_PROJETO.md) continua sobrevivendo conforme o cron diário
// (matriz_confiabilidade_ev.yml) acumula mais partidas.
//
// SVG desenhado à mão (sem lib de gráfico nova), mesma convenção de
// CurvaAprendizadoModelo.jsx -- paleta categórica reaproveitada (3 cores já
// validadas contra o fundo escuro do app pra contraste + separação CVD).
import React, { useState, useEffect, useMemo, useRef } from 'react';
import { Loader2 } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

const CORES_SERIE = ['#3987e5', '#d95926', '#199e70', '#a855f7'];
const LARGURA = 640;
const ALTURA = 220;
const MARGEM = { topo: 10, direita: 12, baixo: 24, esquerda: 44 };

function fmtPct(v) {
  return v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;
}
function fmtData(d) {
  return d ? new Date(`${d}T00:00:00Z`).toLocaleDateString('pt-BR', { timeZone: 'UTC' }) : '—';
}
function chaveCelula(r) {
  return `${r.modelo}|${r.mercado}|${r.odd_min}-${r.odd_max}|${r.edge_min}-${r.edge_max}`;
}
function rotuloCelula(r) {
  const oddMax = r.odd_max >= 999 ? '+' : `-${Number(r.odd_max).toFixed(2)}`;
  const edgeMax = r.edge_max >= 9.99 ? '%+' : `-${(r.edge_max * 100).toFixed(0)}%`;
  return `${r.mercado} · odd ${Number(r.odd_min).toFixed(2)}${oddMax} · edge ${(r.edge_min * 100).toFixed(0)}${edgeMax}`;
}

// Busca paginada de verdade (convenção do projeto -- .select() sem .range()
// corta em 1000 linhas silenciosamente, ver CLAUDE.md).
async function buscarTudoPaginado(query) {
  const linhas = [];
  const TAM_PAGINA = 1000;
  for (let pagina = 0; ; pagina++) {
    const { data, error } = await query.range(pagina * TAM_PAGINA, pagina * TAM_PAGINA + TAM_PAGINA - 1);
    if (error) throw error;
    linhas.push(...(data || []));
    if (!data || data.length < TAM_PAGINA) break;
  }
  return linhas;
}

export default function EvolucaoConfiabilidadeEV({ celulasConfiaveis }) {
  const [historico, setHistorico] = useState(null);
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState('');
  const [hoverIdx, setHoverIdx] = useState(null);
  const svgRef = useRef(null);

  const mercadosConfiaveis = useMemo(
    () => [...new Set((celulasConfiaveis || []).map((c) => c.mercado))],
    [celulasConfiaveis]
  );

  useEffect(() => {
    if (!supabaseAtivo || mercadosConfiaveis.length === 0) {
      setHistorico([]);
      return;
    }
    setCarregando(true);
    setErro('');
    buscarTudoPaginado(
      supabase
        .from('matriz_confiabilidade_ev_historico')
        .select('data_execucao, modelo, mercado, odd_min, odd_max, edge_min, edge_max, roi_medio, roi_medio_ic_inf, roi_medio_ic_sup, n')
        .in('mercado', mercadosConfiaveis)
        .order('data_execucao', { ascending: true })
    )
      .then((linhas) => setHistorico(linhas))
      .catch((e) => setErro(e.message))
      .finally(() => setCarregando(false));
  }, [mercadosConfiaveis]);

  const series = useMemo(() => {
    if (!historico || !celulasConfiaveis) return {};
    const chavesAlvo = new Set(celulasConfiaveis.map(chaveCelula));
    const porChave = {};
    for (const linha of historico) {
      const chave = chaveCelula(linha);
      if (!chavesAlvo.has(chave)) continue;
      (porChave[chave] = porChave[chave] || []).push(linha);
    }
    return porChave;
  }, [historico, celulasConfiaveis]);

  const datasEixo = useMemo(() => [...new Set(Object.values(series).flat().map((p) => p.data_execucao))].sort(), [series]);

  const { escalaX, escalaY, yMin, yMax } = useMemo(() => {
    const todasLinhas = Object.values(series).flat();
    if (todasLinhas.length === 0 || datasEixo.length === 0) return {};
    const larguraUtil = LARGURA - MARGEM.esquerda - MARGEM.direita;
    const alturaUtil = ALTURA - MARGEM.topo - MARGEM.baixo;
    const todosValores = todasLinhas.flatMap((p) => [p.roi_medio_ic_inf, p.roi_medio_ic_sup, 0]);
    const yMinBruto = Math.min(...todosValores);
    const yMaxBruto = Math.max(...todosValores);
    const folga = (yMaxBruto - yMinBruto) * 0.1 || 0.05;
    const yMin = yMinBruto - folga, yMax = yMaxBruto + folga;
    const indicePorData = Object.fromEntries(datasEixo.map((d, i) => [d, i]));
    const escalaX = (data) => MARGEM.esquerda + (datasEixo.length > 1 ? (indicePorData[data] / (datasEixo.length - 1)) * larguraUtil : larguraUtil / 2);
    const escalaY = (v) => MARGEM.topo + alturaUtil - ((v - yMin) / (yMax - yMin)) * alturaUtil;
    return { escalaX, escalaY, yMin, yMax };
  }, [series, datasEixo]);

  if (!celulasConfiaveis || celulasConfiaveis.length === 0) {
    return (
      <p className="text-xs text-slate-500">
        Nenhuma célula confiável no snapshot mais recente -- sem série pra acompanhar.
      </p>
    );
  }
  if (carregando || historico === null) {
    return (
      <div className="flex items-center gap-2 text-slate-500 text-xs py-4 justify-center">
        <Loader2 className="animate-spin" size={14} /> Carregando evolução...
      </div>
    );
  }
  if (erro) return <p className="text-xs text-red-400">{erro}</p>;
  if (datasEixo.length < 2) {
    return (
      <p className="text-xs text-slate-500">
        Só {datasEixo.length === 1 ? '1 execução' : 'nenhuma execução'} registrada até agora -- volte depois que o cron diário acumular mais dias pra ver a evolução.
      </p>
    );
  }

  function moverMouse(e) {
    if (!svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const xSvg = ((e.clientX - rect.left) / rect.width) * LARGURA;
    let maisPerto = 0, menorDist = Infinity;
    datasEixo.forEach((data, i) => {
      const dist = Math.abs(escalaX(data) - xSvg);
      if (dist < menorDist) { menorDist = dist; maisPerto = i; }
    });
    setHoverIdx(maisPerto);
  }

  const dataHover = hoverIdx != null ? datasEixo[hoverIdx] : null;
  const yTicks = [yMin, 0, yMax].filter((v, i, arr) => arr.indexOf(v) === i).sort((a, b) => a - b);
  const chaves = Object.keys(series);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-1.5">
        {chaves.map((chave, i) => (
          <span key={chave} className="flex items-center gap-1 text-[10px] text-slate-400">
            <span className="w-2.5 h-0.5 rounded-full" style={{ backgroundColor: CORES_SERIE[i % CORES_SERIE.length] }} />
            {rotuloCelula(series[chave][0])}
          </span>
        ))}
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${LARGURA} ${ALTURA}`}
        className="w-full h-auto touch-none"
        onMouseMove={moverMouse}
        onMouseLeave={() => setHoverIdx(null)}
      >
        {yTicks.map((valor, i) => (
          <g key={i}>
            <line x1={MARGEM.esquerda} x2={LARGURA - MARGEM.direita} y1={escalaY(valor)} y2={escalaY(valor)}
              stroke={valor === 0 ? '#475569' : '#1e293b'} strokeWidth={1} strokeDasharray={valor === 0 ? '3,2' : undefined} />
            <text x={MARGEM.esquerda - 4} y={escalaY(valor) + 3} textAnchor="end" fontSize={8} fill="#64748b">
              {(valor * 100).toFixed(0)}%
            </text>
          </g>
        ))}

        {chaves.map((chave, i) => {
          const pontos = series[chave];
          const cor = CORES_SERIE[i % CORES_SERIE.length];
          const areaIc = [
            ...pontos.map((p) => `${escalaX(p.data_execucao)},${escalaY(p.roi_medio_ic_sup)}`),
            ...[...pontos].reverse().map((p) => `${escalaX(p.data_execucao)},${escalaY(p.roi_medio_ic_inf)}`),
          ].join(' ');
          const linha = pontos.map((p) => `${escalaX(p.data_execucao)},${escalaY(p.roi_medio)}`).join(' ');
          return (
            <g key={chave}>
              <polygon points={areaIc} fill={cor} fillOpacity={0.12} stroke="none" />
              <polyline points={linha} fill="none" stroke={cor} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            </g>
          );
        })}

        {dataHover && (
          <line x1={escalaX(dataHover)} x2={escalaX(dataHover)} y1={MARGEM.topo} y2={ALTURA - MARGEM.baixo}
            stroke="#94a3b8" strokeWidth={1} strokeDasharray="2,2" />
        )}
        {dataHover && chaves.map((chave, i) => {
          const ponto = series[chave].find((p) => p.data_execucao === dataHover);
          if (!ponto) return null;
          return (
            <circle key={chave} cx={escalaX(dataHover)} cy={escalaY(ponto.roi_medio)} r={4}
              fill={CORES_SERIE[i % CORES_SERIE.length]} stroke="#0f172a" strokeWidth={2} />
          );
        })}

        <text x={MARGEM.esquerda} y={ALTURA - 4} fontSize={8} fill="#64748b">{fmtData(datasEixo[0])}</text>
        <text x={LARGURA - MARGEM.direita} y={ALTURA - 4} textAnchor="end" fontSize={8} fill="#64748b">
          {fmtData(datasEixo[datasEixo.length - 1])}
        </text>
      </svg>

      {dataHover ? (
        <div className="text-[11px] bg-slate-950 border border-slate-800 rounded-lg px-2.5 py-1.5 mt-1 space-y-0.5">
          <div className="text-slate-500">{fmtData(dataHover)}</div>
          {chaves.map((chave, i) => {
            const ponto = series[chave].find((p) => p.data_execucao === dataHover);
            return (
              <div key={chave} className="flex justify-between gap-3" style={{ color: CORES_SERIE[i % CORES_SERIE.length] }}>
                <span>{rotuloCelula(series[chave][0])}</span>
                <span>
                  {ponto ? `${fmtPct(ponto.roi_medio)} IC95%[${fmtPct(ponto.roi_medio_ic_inf)}, ${fmtPct(ponto.roi_medio_ic_sup)}] (n=${ponto.n})` : 'sem execução nesse dia'}
                </span>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-[10px] text-slate-600 mt-1">Passe o mouse sobre o gráfico pra ver o ROI médio e o IC95% de cada dia. Faixa sombreada = IC95% via bootstrap.</p>
      )}
    </div>
  );
}
