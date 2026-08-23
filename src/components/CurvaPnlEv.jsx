// src/components/CurvaPnlEv.jsx
// Curva de Retorno (Lucro Real acumulado) x Valor Esperado (EV acumulado)
// de uma sequência de apostas simuladas -- pedido do usuário, alimentado
// por `serie_temporal` de cada grupo de api/backtest-betting.js (ver
// api/_lib/curvaPnlEv.js). SVG desenhado à mão, mesma convenção do resto
// do app (nenhuma lib de gráfico instalada) -- cores reaproveitadas do
// que o app já usa pra ROI positivo/negativo (emerald-400/red-400),
// EV sempre em cinza tracejado (linha neutra, nunca "boa" nem "ruim" por
// si só -- é só a referência teórica).
import React, { useState, useMemo, useRef } from 'react';

const COR_PNL_POSITIVO = '#34d399'; // emerald-400 -- mesma cor de ROI positivo no resto do app
const COR_PNL_NEGATIVO = '#f87171'; // red-400 -- mesma cor de ROI negativo no resto do app
const COR_EV = '#94a3b8'; // slate-400 -- linha neutra/teórica, tracejada

const LARGURA = 640;
const ALTURA = 220;
const MARGEM = { topo: 10, direita: 12, baixo: 24, esquerda: 44 };

function fmtUnidades(v) {
  return v == null ? '—' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}u`;
}
function fmtData(d) {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('pt-BR');
}

export default function CurvaPnlEv({ serieTemporal }) {
  const [hoverIdx, setHoverIdx] = useState(null);
  const svgRef = useRef(null);

  const { escalaX, escalaY, caminhoPnl, caminhoEv, yMin, yMax, corPnl } = useMemo(() => {
    if (!serieTemporal || serieTemporal.length === 0) return {};
    const larguraUtil = LARGURA - MARGEM.esquerda - MARGEM.direita;
    const alturaUtil = ALTURA - MARGEM.topo - MARGEM.baixo;
    const n = serieTemporal.length;
    const todosValores = serieTemporal.flatMap((p) => [p.pnl_acumulado, p.ev_acumulado, 0]);
    const yMinBruto = Math.min(...todosValores);
    const yMaxBruto = Math.max(...todosValores);
    const folga = (yMaxBruto - yMinBruto) * 0.1 || 1;
    const yMin = yMinBruto - folga, yMax = yMaxBruto + folga;
    const escalaX = (i) => MARGEM.esquerda + (n > 1 ? (i / (n - 1)) * larguraUtil : larguraUtil / 2);
    const escalaY = (v) => MARGEM.topo + alturaUtil - ((v - yMin) / (yMax - yMin)) * alturaUtil;
    const caminhoPnl = serieTemporal.map((p, i) => `${escalaX(i)},${escalaY(p.pnl_acumulado)}`).join(' ');
    const caminhoEv = serieTemporal.map((p, i) => `${escalaX(i)},${escalaY(p.ev_acumulado)}`).join(' ');
    const corPnl = serieTemporal[n - 1].pnl_acumulado >= 0 ? COR_PNL_POSITIVO : COR_PNL_NEGATIVO;
    return { escalaX, escalaY, caminhoPnl, caminhoEv, yMin, yMax, corPnl };
  }, [serieTemporal]);

  if (!serieTemporal || serieTemporal.length === 0) {
    return <p className="text-xs text-slate-500">Sem apostas nessa seleção pra desenhar a curva.</p>;
  }

  function moverMouse(e) {
    if (!svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const xSvg = ((e.clientX - rect.left) / rect.width) * LARGURA;
    let maisPerto = 0, menorDist = Infinity;
    serieTemporal.forEach((_, i) => {
      const dist = Math.abs(escalaX(i) - xSvg);
      if (dist < menorDist) { menorDist = dist; maisPerto = i; }
    });
    setHoverIdx(maisPerto);
  }

  const ponto = hoverIdx != null ? serieTemporal[hoverIdx] : null;
  const yZero = escalaY(0);
  const yTicks = [yMin, (yMin + yMax) / 2, yMax];

  return (
    <div>
      <div className="flex items-center gap-4 mb-1.5 text-[10px]">
        <span className="flex items-center gap-1 text-slate-400">
          <span className="w-3 h-0.5 rounded-full" style={{ backgroundColor: corPnl }} /> Lucro Real (PnL)
        </span>
        <span className="flex items-center gap-1 text-slate-400">
          <svg width="14" height="4"><line x1="0" y1="2" x2="14" y2="2" stroke={COR_EV} strokeWidth="2" strokeDasharray="3,2" /></svg>
          Valor Esperado (EV)
        </span>
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${LARGURA} ${ALTURA}`}
        className="w-full h-auto touch-none"
        onMouseMove={moverMouse}
        onMouseLeave={() => setHoverIdx(null)}
      >
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={MARGEM.esquerda} x2={LARGURA - MARGEM.direita} y1={escalaY(v)} y2={escalaY(v)} stroke="#2c2c2a" strokeWidth={1} />
            <text x={MARGEM.esquerda - 6} y={escalaY(v) + 3} textAnchor="end" fontSize={9} fill="#898781">{v.toFixed(1)}</text>
          </g>
        ))}
        {/* linha de zero em destaque -- é a referência "banca inicial" */}
        <line x1={MARGEM.esquerda} x2={LARGURA - MARGEM.direita} y1={yZero} y2={yZero} stroke="#383835" strokeWidth={1} />

        <polyline points={caminhoEv} fill="none" stroke={COR_EV} strokeWidth={2} strokeDasharray="5,4" strokeLinejoin="round" strokeLinecap="round" />
        <polyline points={caminhoPnl} fill="none" stroke={corPnl} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />

        {ponto && (
          <>
            <line x1={escalaX(hoverIdx)} x2={escalaX(hoverIdx)} y1={MARGEM.topo} y2={ALTURA - MARGEM.baixo} stroke="#c3c2b7" strokeWidth={1} strokeDasharray="2,2" />
            <circle cx={escalaX(hoverIdx)} cy={escalaY(ponto.pnl_acumulado)} r={4} fill={corPnl} stroke="#0f172a" strokeWidth={2} />
            <circle cx={escalaX(hoverIdx)} cy={escalaY(ponto.ev_acumulado)} r={4} fill={COR_EV} stroke="#0f172a" strokeWidth={2} />
          </>
        )}

        <text x={MARGEM.esquerda} y={ALTURA - 6} fontSize={9} fill="#898781">{fmtData(serieTemporal[0].date)}</text>
        <text x={LARGURA - MARGEM.direita} y={ALTURA - 6} textAnchor="end" fontSize={9} fill="#898781">
          {fmtData(serieTemporal[serieTemporal.length - 1].date)}
        </text>
      </svg>

      {ponto ? (
        <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] bg-slate-950 border border-slate-800 rounded-lg px-2.5 py-1.5 mt-1">
          <span className="text-slate-500">
            aposta {hoverIdx + 1}/{serieTemporal.length} — {fmtData(ponto.date)}
          </span>
          <span className="flex gap-3">
            <span style={{ color: corPnl }}>PnL: {fmtUnidades(ponto.pnl_acumulado)}</span>
            <span style={{ color: COR_EV }}>EV: {fmtUnidades(ponto.ev_acumulado)}</span>
            <span className="text-slate-500">drawdown: {fmtUnidades(-ponto.drawdown)}</span>
          </span>
        </div>
      ) : (
        <p className="text-[10px] text-slate-600 mt-1">Passe o mouse sobre o gráfico pra ver o valor acumulado de cada aposta.</p>
      )}
    </div>
  );
}
