// src/components/CurvaAprendizadoModelo.jsx
// Curva de aprendizado (log-loss por iteração do boosting, Treino x
// Validação x Teste) de um modelo do Model Benchmarking — pedido do
// usuário. Lê `model_benchmarking_learning_curve` (RLS pública), só
// preenchida pelo backtest manual (scripts/backtest_kelly.py -- ver
// comentário da migração) pra config vencedora do grid search de cada
// modelo/mercado. dixon_coles_v1 nunca aparece aqui (Poisson puro, não é
// treino por iteração de boosting) nem o cron diário de produção.
//
// SVG desenhado à mão (sem lib de gráfico nova) -- paleta categórica de 3
// cores validada (contraste + separação CVD) contra o fundo escuro do app,
// ver skill de dataviz do projeto.
import React, { useState, useEffect, useMemo, useRef } from 'react';
import { Loader2 } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

const CORES_SERIE = { treino: '#3987e5', validacao: '#d95926', teste: '#199e70' };
const ROTULO_SERIE = { treino: 'Treino', validacao: 'Validação', teste: 'Teste' };
const ROTULO_MERCADO_CURVA = { '1X2': '1X2', 'over_under_2.5': 'Over/Under 2.5' };

const LARGURA = 320;
const ALTURA = 150;
const MARGEM = { topo: 8, direita: 8, baixo: 20, esquerda: 34 };

function fmtLogLoss(v) {
  return v == null ? '—' : Number(v).toFixed(3);
}

export default function CurvaAprendizadoModelo({ modelName }) {
  const [linhas, setLinhas] = useState([]);
  const [carregando, setCarregando] = useState(false);
  const [mercado, setMercado] = useState(null);
  const [hoverIdx, setHoverIdx] = useState(null);
  const svgRef = useRef(null);

  useEffect(() => {
    if (!modelName || !supabaseAtivo) return;
    setCarregando(true);
    supabase
      .from('model_benchmarking_learning_curve')
      .select('mercado, iteracao, treino, validacao, teste')
      .eq('model_name', modelName)
      .order('iteracao')
      .then(({ data }) => {
        setLinhas(data || []);
        const mercados = [...new Set((data || []).map((l) => l.mercado))];
        setMercado(mercados[0] || null);
        setCarregando(false);
      });
  }, [modelName]);

  const mercadosDisponiveis = useMemo(() => [...new Set(linhas.map((l) => l.mercado))], [linhas]);
  const pontos = useMemo(() => linhas.filter((l) => l.mercado === mercado), [linhas, mercado]);

  const { escalaX, escalaY, caminhos, yMin, yMax } = useMemo(() => {
    if (pontos.length === 0) return { escalaX: null, escalaY: null, caminhos: {}, yMin: 0, yMax: 1 };
    const larguraUtil = LARGURA - MARGEM.esquerda - MARGEM.direita;
    const alturaUtil = ALTURA - MARGEM.topo - MARGEM.baixo;
    const iterMax = Math.max(...pontos.map((p) => p.iteracao));
    const todosValores = pontos.flatMap((p) => [p.treino, p.validacao, p.teste]).filter((v) => v != null);
    const yMin = Math.min(...todosValores);
    const yMax = Math.max(...todosValores);
    const folga = (yMax - yMin) * 0.08 || 0.05;
    const yMinAjustado = yMin - folga, yMaxAjustado = yMax + folga;
    const escalaX = (iteracao) => MARGEM.esquerda + (iteracao / (iterMax || 1)) * larguraUtil;
    const escalaY = (valor) => MARGEM.topo + alturaUtil - ((valor - yMinAjustado) / (yMaxAjustado - yMinAjustado)) * alturaUtil;
    const caminhos = {};
    for (const serie of ['treino', 'validacao', 'teste']) {
      const comValor = pontos.filter((p) => p[serie] != null);
      if (comValor.length === 0) continue;
      caminhos[serie] = comValor.map((p) => `${escalaX(p.iteracao)},${escalaY(p[serie])}`).join(' ');
    }
    return { escalaX, escalaY, caminhos, yMin: yMinAjustado, yMax: yMaxAjustado };
  }, [pontos]);

  function moverMouse(e) {
    if (!svgRef.current || pontos.length === 0) return;
    const rect = svgRef.current.getBoundingClientRect();
    const xSvg = ((e.clientX - rect.left) / rect.width) * LARGURA;
    let maisPerto = 0, menorDist = Infinity;
    pontos.forEach((p, i) => {
      const dist = Math.abs(escalaX(p.iteracao) - xSvg);
      if (dist < menorDist) { menorDist = dist; maisPerto = i; }
    });
    setHoverIdx(maisPerto);
  }

  if (!modelName) return null;
  if (carregando) {
    return (
      <div className="flex items-center gap-2 text-slate-500 text-xs py-4 justify-center">
        <Loader2 className="animate-spin" size={14} /> Carregando curva...
      </div>
    );
  }
  if (pontos.length === 0) {
    return (
      <p className="text-xs text-slate-500">
        Sem curva de aprendizado ainda — só o backtest manual (scripts/backtest_kelly.py) gera isso, não o treino diário de produção.
      </p>
    );
  }

  const pontoHover = hoverIdx != null ? pontos[hoverIdx] : null;
  const yTicks = [yMin, (yMin + yMax) / 2, yMax];

  return (
    <div>
      {mercadosDisponiveis.length > 1 && (
        <div className="flex gap-1.5 mb-2">
          {mercadosDisponiveis.map((m) => (
            <button
              key={m}
              onClick={() => { setMercado(m); setHoverIdx(null); }}
              className={`px-2 py-0.5 rounded text-[10px] font-bold border ${
                mercado === m ? 'bg-emerald-600 border-emerald-600 text-white' : 'bg-slate-800 border-slate-700 text-slate-400'
              }`}
            >
              {ROTULO_MERCADO_CURVA[m] || m}
            </button>
          ))}
        </div>
      )}

      <div className="flex items-center gap-3 mb-1.5">
        {Object.entries(ROTULO_SERIE).map(([chave, rotulo]) => (
          <span key={chave} className="flex items-center gap-1 text-[10px] text-slate-400">
            <span className="w-2.5 h-0.5 rounded-full" style={{ backgroundColor: CORES_SERIE[chave] }} />
            {rotulo}
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
            <line
              x1={MARGEM.esquerda} x2={LARGURA - MARGEM.direita}
              y1={escalaY(valor)} y2={escalaY(valor)}
              stroke="#2c2c2a" strokeWidth={1}
            />
            <text x={MARGEM.esquerda - 4} y={escalaY(valor) + 3} textAnchor="end" fontSize={8} fill="#898781">
              {valor.toFixed(2)}
            </text>
          </g>
        ))}

        {Object.entries(caminhos).map(([serie, d]) => (
          <polyline key={serie} points={d} fill="none" stroke={CORES_SERIE[serie]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        ))}

        {pontoHover && (
          <line
            x1={escalaX(pontoHover.iteracao)} x2={escalaX(pontoHover.iteracao)}
            y1={MARGEM.topo} y2={ALTURA - MARGEM.baixo}
            stroke="#c3c2b7" strokeWidth={1} strokeDasharray="2,2"
          />
        )}
        {pontoHover &&
          ['treino', 'validacao', 'teste'].map((serie) =>
            pontoHover[serie] != null ? (
              <circle
                key={serie}
                cx={escalaX(pontoHover.iteracao)} cy={escalaY(pontoHover[serie])}
                r={4} fill={CORES_SERIE[serie]} stroke="#0f172a" strokeWidth={2}
              />
            ) : null
          )}

        <text x={MARGEM.esquerda} y={ALTURA - 4} fontSize={8} fill="#898781">0</text>
        <text x={LARGURA - MARGEM.direita} y={ALTURA - 4} textAnchor="end" fontSize={8} fill="#898781">
          {Math.max(...pontos.map((p) => p.iteracao))}
        </text>
        <text x={(MARGEM.esquerda + LARGURA - MARGEM.direita) / 2} y={ALTURA - 4} textAnchor="middle" fontSize={8} fill="#898781">
          iteração
        </text>
      </svg>

      {pontoHover ? (
        <div className="flex items-center justify-between text-[11px] bg-slate-950 border border-slate-800 rounded-lg px-2.5 py-1.5 mt-1">
          <span className="text-slate-500">iteração {pontoHover.iteracao}</span>
          <span className="flex gap-3">
            {['treino', 'validacao', 'teste'].map((serie) => (
              <span key={serie} style={{ color: CORES_SERIE[serie] }}>
                {ROTULO_SERIE[serie]}: {fmtLogLoss(pontoHover[serie])}
              </span>
            ))}
          </span>
        </div>
      ) : (
        <p className="text-[10px] text-slate-600 mt-1">Passe o mouse sobre o gráfico pra ver o valor de cada iteração.</p>
      )}
    </div>
  );
}
