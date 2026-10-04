// Mapa das 14 zonas polares de chute (ver `zoneTransitionMatrix.js`), com os
// limites anatômicos do campo sobrepostos: grande área, pequena área, gol,
// marca do pênalti, meia-lua e o cone central (±30° do eixo do campo).
// Desenho em metros: gol no topo, centro do gol em (34, 0), 1 m = ESC px.
import { useState } from 'react';
import { ZONAS_CHUTE, XG_MEDIO_ZONA_CHUTE, ESTATISTICA_ZONA_CHUTE, ANEIS_CHUTE_M, ANGULO_CONE_GRAUS } from '../utils/zoneTransitionMatrix';

const ESC = 10;
const LARG = 68;
const PROF = 46; // metros de campo exibidos a partir da linha de fundo
const MARGEM_TOPO = 14; // px, espaço pro gol desenhado fora do campo
const CX = (LARG / 2) * ESC;
const RAIO_MAX = 90; // m, sempre maior que o campo (o clipPath recorta)

const rad = (g) => (g * Math.PI) / 180;
const pt = (r, graus) => [CX + r * Math.sin(rad(graus)) * ESC, r * Math.cos(rad(graus)) * ESC];

// Setor anular entre dois raios e dois ângulos (graus a partir do eixo do campo).
function caminhoSetor(r0, r1, a0, a1) {
  const [x0, y0] = pt(r1, a0);
  const [x1, y1] = pt(r1, a1);
  const [x2, y2] = pt(r0, a1);
  const [x3, y3] = pt(r0, a0);
  const sweep = a1 > a0 ? 0 : 1;
  const grande = Math.abs(a1 - a0) > 180 ? 1 : 0;
  let d = `M ${x0} ${y0} A ${r1 * ESC} ${r1 * ESC} 0 ${grande} ${sweep} ${x1} ${y1} L ${x2} ${y2}`;
  if (r0 > 0) d += ` A ${r0 * ESC} ${r0 * ESC} 0 ${grande} ${1 - sweep} ${x3} ${y3}`;
  return d + ' Z';
}

function cor(t) {
  // pálido -> laranja -> vermelho, t em [0,1]
  const c = Math.max(0, Math.min(1, t));
  const r = Math.round(254 - 15 * c);
  const g = Math.round(243 - 190 * c);
  const b = Math.round(199 - 170 * c);
  return `rgb(${r},${g},${b})`;
}

// Escala divergente em torno de `centro`: azul-esverdeado (abaixo) <- cinza claro -> vermelho (acima).
function corDiv(v, centro, dominio) {
  const t = Math.max(-1, Math.min(1, (v - centro) / (dominio || 1)));
  const a = Math.abs(t);
  const alvo = t >= 0 ? [220, 38, 38] : [13, 148, 136];
  const base = [241, 245, 249];
  const m = (i) => Math.round(base[i] + (alvo[i] - base[i]) * a);
  return `rgb(${m(0)},${m(1)},${m(2)})`;
}

const ic3 = (v, ic) => `${v.toFixed(3)} ± ${ic.toFixed(3)}`;
const pctIc = (v, ic) => `${(v * 100).toFixed(2)}% ± ${(ic * 100).toFixed(2)}`;

// `tabela=false` e `alternarMetrica=false` = modo ficha (só mapa + linhas do campo).
// Modo personalizado (ficha defensiva): `valoresCustom` (14 valores), `formatoCustom(v)`,
// `centro` (escala divergente em torno dele, ex.: 1 = igual ao esperado), `tituloZona(i)`, `legendaCustom`.
export default function MapaZonasChute({
  distribuicao, tabela = true, alternarMetrica = true,
  valoresCustom = null, formatoCustom = null, centro = null, tituloZona = null, legendaCustom = null,
}) {
  const [metrica, setMetrica] = useState('pct'); // 'pct' | 'xg'
  const [linhas, setLinhas] = useState(true);
  const custom = Array.isArray(valoresCustom);
  const valores = custom ? valoresCustom : (metrica === 'pct' ? distribuicao : XG_MEDIO_ZONA_CHUTE);
  const max = Math.max(...valores, 1e-9);
  const dominio = centro != null ? Math.max(...valores.map((v) => Math.abs(v - centro)), 1e-9) : 1;
  const fmt = custom && formatoCustom
    ? formatoCustom
    : (v) => (metrica === 'pct' ? `${(v * 100).toFixed(1)}%` : v.toFixed(3));
  const corZona = (v) => (centro != null ? corDiv(v, centro, dominio) : cor(v / max));

  const raios = ANEIS_CHUTE_M.map((r) => (Number.isFinite(r) ? r : RAIO_MAX));
  const W = LARG * ESC;
  const H = PROF * ESC;

  // Linhas anatômicas (FIFA): grande área 40,32 × 16,5 m; pequena 18,32 × 5,5 m;
  // marca do pênalti a 11 m; arco de 9,15 m; gol de 7,32 m.
  const gaX0 = CX - 20.16 * ESC;
  const paX0 = CX - 9.16 * ESC;
  const dxArco = Math.sqrt(9.15 ** 2 - 5.5 ** 2);
  const branco = 'rgba(255,255,255,0.9)';
  const halo = { stroke: 'rgba(15,23,42,0.85)', strokeWidth: 3, paintOrder: 'stroke' };

  return (
    <div>
      <div className="flex flex-wrap gap-2 justify-center mb-3">
        {alternarMetrica && (
          <button
            onClick={() => setMetrica(metrica === 'pct' ? 'xg' : 'pct')}
            className="text-xs bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-slate-200"
          >
            Mostrar {metrica === 'pct' ? 'xG médio por chute' : '% dos chutes'}
          </button>
        )}
        <button
          onClick={() => setLinhas(!linhas)}
          className="text-xs bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-slate-200"
        >
          {linhas ? 'Ocultar' : 'Mostrar'} limites do campo
        </button>
      </div>

      <svg
        viewBox={`-6 ${-MARGEM_TOPO} ${W + 12} ${H + MARGEM_TOPO + 6}`}
        className="w-full max-w-xl mx-auto"
        role="img"
        aria-label="Mapa das 14 zonas de chute com limites do campo"
      >
        <defs>
          <clipPath id="clip-campo-chute"><rect x="0" y="0" width={W} height={H} /></clipPath>
        </defs>
        <rect x="0" y="0" width={W} height={H} fill="#14532d" />

        <g clipPath="url(#clip-campo-chute)">
          {ZONAS_CHUTE.map((z, i) => {
            const r0 = raios[z.anel];
            const r1 = raios[z.anel + 1];
            const fill = corZona(valores[i]);
            const caminhos = z.setor === 'central'
              ? [caminhoSetor(r0, r1, -ANGULO_CONE_GRAUS, ANGULO_CONE_GRAUS)]
              : [caminhoSetor(r0, r1, ANGULO_CONE_GRAUS, 90), caminhoSetor(r0, r1, -ANGULO_CONE_GRAUS, -90)];
            return (
              <g key={z.id}>
                <title>{custom && tituloZona ? tituloZona(i) : `${z.label} — simulado ${(distribuicao[i] * 100).toFixed(1)}% dos chutes · real ${pctIc(ESTATISTICA_ZONA_CHUTE[i].pct, ESTATISTICA_ZONA_CHUTE[i].pctIc)} · xG ${ic3(ESTATISTICA_ZONA_CHUTE[i].xg, ESTATISTICA_ZONA_CHUTE[i].xgIc)} · gol/chute ${ic3(ESTATISTICA_ZONA_CHUTE[i].gol, ESTATISTICA_ZONA_CHUTE[i].golIc)}`}</title>
                {caminhos.map((d, k) => (
                  <path key={k} d={d} fill={fill} stroke="rgba(15,23,42,0.55)" strokeWidth="1" />
                ))}
              </g>
            );
          })}

          {/* rótulos no meio de cada zona */}
          {ZONAS_CHUTE.map((z, i) => {
            const rm = (raios[z.anel] + Math.min(raios[z.anel + 1], 40)) / 2;
            const angs = z.setor === 'central' ? [0] : [60, -60];
            return angs.map((a) => {
              const [x, y] = pt(rm, a);
              if (x < 4 || x > W - 4 || y > H - 4) return null;
              return (
                <text key={`${z.id}${a}`} x={x} y={y} textAnchor="middle" dominantBaseline="middle"
                  fontSize="11" fontWeight="700" fill="#1e293b" style={{ pointerEvents: 'none' }}>
                  {fmt(valores[i])}
                </text>
              );
            });
          })}
        </g>

        {linhas && (
          <g fill="none" stroke={branco} strokeWidth="1.6">
            {/* contorno visível do campo */}
            <rect x="0" y="0" width={W} height={H} />
            {/* grande área */}
            <rect x={gaX0} y="0" width={40.32 * ESC} height={16.5 * ESC} />
            {/* pequena área */}
            <rect x={paX0} y="0" width={18.32 * ESC} height={5.5 * ESC} />
            {/* meia-lua (arco de 9,15 m fora da grande área) */}
            <path d={`M ${CX - dxArco * ESC} ${16.5 * ESC} A ${9.15 * ESC} ${9.15 * ESC} 0 0 0 ${CX + dxArco * ESC} ${16.5 * ESC}`} />
            {/* gol (fora do campo) */}
            <rect x={CX - 3.66 * ESC} y={-1.2 * ESC} width={7.32 * ESC} height={1.2 * ESC} strokeWidth="2.2" />
            {/* cone central: ±30° do eixo do campo */}
            {[-ANGULO_CONE_GRAUS, ANGULO_CONE_GRAUS].map((a) => {
              const [x, y] = pt(RAIO_MAX, a);
              // recorta a reta na borda inferior do campo
              const t = Math.min(1, H / y);
              return (
                <line key={a} x1={CX} y1={0} x2={CX + (x - CX) * t} y2={y * t}
                  stroke="#fde047" strokeWidth="2" strokeDasharray="7 5" />
              );
            })}
          </g>
        )}
        {linhas && (
          <g>
            <circle cx={CX} cy={11 * ESC} r="3" fill={branco} />
            <text x={CX + 8} y={11 * ESC + 4} fontSize="9" fill={branco} {...halo}>marca do pênalti</text>
            <text x={CX} y={-2.4 * ESC} fontSize="10" fill="#cbd5e1" textAnchor="middle">gol</text>
            <text x={gaX0 + 4} y={16.5 * ESC - 4} fontSize="9" fill={branco} {...halo}>grande área</text>
            <text x={paX0 - 4} y={5.5 * ESC - 4} fontSize="9" fill={branco} textAnchor="end" {...halo}>pequena área</text>
            <text x={CX + 12 * ESC} y={22 * ESC} fontSize="10" fill="#fde047" {...halo}>cone ±{ANGULO_CONE_GRAUS}°</text>
            {ANEIS_CHUTE_M.slice(1, -1).map((r) => (
              <text key={r} x={CX - r * ESC} y={24} fontSize="9" fill="#e2e8f0" textAnchor="middle" {...halo}>{r} m</text>
            ))}
          </g>
        )}
      </svg>

      <div className="flex items-center justify-center gap-2 mt-3 text-[10px] text-slate-400">
        <span>{centro != null ? 'menos que o esperado' : 'menos'}</span>
        <div
          className="h-2 w-40 rounded"
          style={{ background: centro != null
            ? `linear-gradient(to right, ${corDiv(centro - 1, centro, 1)}, ${corDiv(centro, centro, 1)}, ${corDiv(centro + 1, centro, 1)})`
            : `linear-gradient(to right, ${cor(0)}, ${cor(0.5)}, ${cor(1)})` }}
        />
        <span>{centro != null ? 'mais' : 'mais'} ({custom ? (legendaCustom || '') : (metrica === 'pct' ? '% dos chutes' : 'xG/chute')})</span>
      </div>
      {tabela && (
      <div className="overflow-x-auto mt-5">
          <table className="w-full text-[11px] text-slate-300 border-collapse">
            <thead>
              <tr className="text-slate-400 border-b border-slate-700">
                <th className="text-left py-1.5 pr-2 font-semibold">Zona</th>
                <th className="text-right px-2 font-semibold">Chutes</th>
                <th className="text-right px-2 font-semibold">% simulado</th>
                <th className="text-right px-2 font-semibold">% real ± IC95</th>
                <th className="text-right px-2 font-semibold">xG/chute ± IC95</th>
                <th className="text-right pl-2 font-semibold">Gols/chute ± IC95</th>
              </tr>
            </thead>
            <tbody>
              {ZONAS_CHUTE.map((z, i) => {
                const e = ESTATISTICA_ZONA_CHUTE[i];
                const diverge = Math.abs(e.gol - e.xg) > e.golIc + e.xgIc;
                return (
                  <tr key={z.id} className="border-b border-slate-800">
                    <td className="py-1 pr-2">{z.label}</td>
                    <td className="text-right px-2 font-mono">{e.n.toLocaleString('pt-BR')}</td>
                    <td className="text-right px-2 font-mono">{(distribuicao[i] * 100).toFixed(1)}%</td>
                    <td className="text-right px-2 font-mono">{pctIc(e.pct, e.pctIc)}</td>
                    <td className="text-right px-2 font-mono">{ic3(e.xg, e.xgIc)}</td>
                    <td className={`text-right pl-2 font-mono ${diverge ? 'text-yellow-400' : ''}`}>{ic3(e.gol, e.golIc)}{diverge ? ' *' : ''}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="text-[10px] text-slate-500 mt-2">
            IC 95% por partida (chutes do mesmo jogo não são independentes; ~432 mil chutes em ~17 mil jogos do FotMob
            desde 01/08/2021, o período do modelo de xG atual, sem pênaltis). O IC do xG mede a precisão da média do xG do FotMob (saída de modelo), não a conversão verdadeira.
            * = gol/chute real fora do IC do xG (os intervalos não se sobrepõem). O % simulado vem da matriz de passes do
            StatsBomb (uma liga, uma temporada) e não tem IC calculável — compare com o % real.
          </p>
        </div>
      )}
    </div>
  );
}
