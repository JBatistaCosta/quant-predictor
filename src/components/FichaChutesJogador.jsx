// Ficha de chutes de um jogador: setor preferido (14 zonas polares), precisão (no alvo),
// colocação (xGOT) e gol, COMPARADOS com o esperado para quem chuta das mesmas posições
// (zona x cabeça/pé x bola rolando/parada) e com encolhimento (empirical Bayes).
// Tudo vem das RPCs `ficha_chutes_jogador` (com DATA DE CORTE e meia-vida) e
// `serie_chutes_jogador` (migrations 20261001120000 e 20261001180000) -- a regra de zona
// mora só no SQL (`zona_chute`).
//
// Por que não mostrar só a taxa bruta: a taxa bruta de gol é quase só função da
// distância (r = -0,70 entre distância média e gol/chute). Controlando a posição, a
// preferência de setor não prevê mais precisão nem gol, e a habilidade individual é
// modesta (confiabilidade por metades: 0,31 no alvo, 0,20 no gol, 0,16 na colocação,
// 0,13 na conversão sobre xGOT). Por isso a diferença "encolhida" é a que vale.
//
// Data de corte: só entram chutes ANTERIORES à data (sem vazamento do futuro). Meia-vida
// padrão de 2 anos porque, no teste walk-forward (cortes 2023/24/25), decair mais rápido
// NÃO melhora a previsão do gol (2 anos e sem decaimento empatam ou vencem 6 meses/1 ano).
import { useEffect, useState } from 'react';
import { Crosshair, Loader2, AlertTriangle } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { ZONAS_CHUTE } from '../utils/zoneTransitionMatrix';
import MapaZonasChute from './MapaZonasChute';

const pct1 = (v) => `${(v * 100).toFixed(1)}%`;
const pp = (v) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)} p.p.`;

const MEIAS_VIDA = [
  { valor: 182, rotulo: '6 meses' },
  { valor: 365, rotulo: '1 ano' },
  { valor: 730, rotulo: '2 anos (padrão)' },
  { valor: 0, rotulo: 'sem decaimento' },
];

function veredito(estimativa, ic) {
  if (estimativa == null || ic == null) return { texto: '—', cor: 'text-slate-500' };
  if (Math.abs(estimativa) <= ic) return { texto: 'indistinguível do esperado', cor: 'text-slate-400' };
  return estimativa > 0
    ? { texto: 'acima do esperado', cor: 'text-emerald-400' }
    : { texto: 'abaixo do esperado', cor: 'text-red-400' };
}

// Estimativa encolhida com barra de IC 95% sobre uma escala simétrica em torno de zero.
function ForestMini({ valor, ic, dominio }) {
  if (valor == null) return <span className="text-slate-600">—</span>;
  const W = 110;
  const x = (v) => W / 2 + (Math.max(-dominio, Math.min(dominio, v)) / dominio) * (W / 2 - 4);
  const sig = Math.abs(valor) > ic;
  const cor = !sig ? '#94a3b8' : valor > 0 ? '#34d399' : '#f87171';
  return (
    <svg width={W} height="16" role="img" aria-label={`${pp(valor)} ± ${(ic * 100).toFixed(1)}`}>
      <line x1={W / 2} y1="1" x2={W / 2} y2="15" stroke="#475569" strokeWidth="1" />
      <line x1={x(valor - ic)} y1="8" x2={x(valor + ic)} y2="8" stroke={cor} strokeWidth="2" />
      <circle cx={x(valor)} cy="8" r="3" fill={cor} />
    </svg>
  );
}

export default function FichaChutesJogador({ playerId }) {
  const [ficha, setFicha] = useState(null);
  const [serie, setSerie] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [ate, setAte] = useState(''); // '' = hoje
  const [meiaVida, setMeiaVida] = useState(730);

  useEffect(() => {
    if (!supabaseAtivo) { setCarregando(false); return; }
    let cancelado = false;
    (async () => {
      const { data, error } = await supabase.rpc('serie_chutes_jogador', { p_player_id: Number(playerId) });
      if (!cancelado && !error) setSerie(data || []);
    })();
    return () => { cancelado = true; };
  }, [playerId]);

  useEffect(() => {
    if (!supabaseAtivo) { setCarregando(false); return; }
    let cancelado = false;
    (async () => {
      setCarregando(true);
      setErro('');
      const { data, error } = await supabase.rpc('ficha_chutes_jogador', {
        p_player_id: Number(playerId),
        p_ate: ate || null,
        p_meia_vida_dias: meiaVida,
      });
      if (cancelado) return;
      if (error) setErro(error.message);
      else setFicha(data);
      setCarregando(false);
    })();
    return () => { cancelado = true; };
  }, [playerId, ate, meiaVida]);

  const titulo = (
    <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2 mb-3">
      <Crosshair className="text-emerald-400" size={16} /> Chutes: setor preferido e pontaria
    </h2>
  );

  const controles = (
    <div className="flex flex-wrap items-end gap-3 mb-4">
      <label className="text-[11px] text-slate-400">
        <span className="block mb-1">Ficha até a data (exclusive)</span>
        <input
          type="date"
          value={ate}
          onChange={(e) => setAte(e.target.value)}
          className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-1 text-slate-200 text-xs"
        />
      </label>
      <label className="text-[11px] text-slate-400">
        <span className="block mb-1">Peso dos chutes (meia-vida)</span>
        <select
          value={meiaVida}
          onChange={(e) => setMeiaVida(Number(e.target.value))}
          className="bg-slate-900 border border-slate-600 rounded-lg px-2 py-1 text-slate-200 text-xs"
        >
          {MEIAS_VIDA.map((m) => <option key={m.valor} value={m.valor}>{m.rotulo}</option>)}
        </select>
      </label>
      {ate && (
        <button
          onClick={() => setAte('')}
          className="text-xs bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-slate-200"
        >
          Voltar para hoje
        </button>
      )}
      {carregando && <Loader2 className="animate-spin text-slate-500 mb-1" size={16} />}
    </div>
  );

  if (erro) {
    return <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5 mb-4">{titulo}{controles}<p className="text-xs text-red-400">{erro}</p></div>;
  }
  if (!ficha) {
    return <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5 mb-4">{titulo}<Loader2 className="animate-spin text-slate-500" size={18} /></div>;
  }
  if (!ficha.n) {
    return (
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5 mb-4">
        {titulo}{controles}
        <p className="text-xs text-slate-500">Sem chutes com coordenada registrados para este jogador antes de {ficha.corte}.</p>
      </div>
    );
  }

  const n = ficha.n;
  const distribuicao = Array(14).fill(0);
  const porZona = Array(14).fill(null);
  (ficha.zonas || []).forEach((z) => { distribuicao[z.z] = z.n / n; porZona[z.z] = z; });

  const idxMax = distribuicao.indexOf(Math.max(...distribuicao));
  const topZonas = distribuicao
    .map((p, i) => ({ i, p }))
    .filter((x) => x.p > 0)
    .sort((a, b) => b.p - a.p)
    .slice(0, 5);

  const nt = ficha.n_no_alvo_xgot;
  const linhas = [
    {
      nome: 'Precisão (no alvo entre chutes não bloqueados)',
      obs: ficha.n_desbloqueados > 0 ? ficha.no_alvo / ficha.n_desbloqueados : null,
      esp: ficha.n_desbloqueados > 0 ? ficha.alvo_esperado / ficha.n_desbloqueados : null,
      est: ficha.alvo,
    },
    {
      nome: 'Colocação (xGOT dos chutes no alvo)',
      obs: nt > 0 ? ficha.xgot / nt : null,
      esp: nt > 0 ? ficha.xgot_esperado / nt : null,
      est: ficha.colocacao,
    },
    {
      nome: 'Gol por chute',
      obs: ficha.gols / n,
      esp: ficha.gols_esperados / n,
      est: ficha.gol,
    },
    {
      nome: 'Conversão sobre o xGOT (gol / xGOT, só no alvo)',
      obs: nt > 0 ? ficha.gols_no_alvo / nt : null,
      esp: nt > 0 ? ficha.xgot / nt : null,
      est: ficha.conversao_xgot,
    },
  ];

  const dominio = (campo) => {
    const m = (serie || []).reduce((mx, s) => (s[campo] ? Math.max(mx, Math.abs(s[campo].encolhida) + s[campo].ic) : mx), 0.04);
    return Math.max(0.04, m);
  };

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5 mb-4">
      {titulo}
      {controles}

      <p className="text-[11px] text-slate-500 mb-3">
        Chutes de {ficha.primeiro_chute} a {ficha.ultimo_chute}, anteriores a {ficha.corte}
        {ficha.meia_vida_dias ? ` · meia-vida ${ficha.meia_vida_dias} dias (amostra efetiva ${Math.round(ficha.n_efetivo)} chutes)` : ' · sem decaimento'}.
      </p>

      {n < 100 && (
        <p className="text-[11px] text-yellow-400/80 mb-3 flex items-start gap-1.5">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          Amostra pequena ({n} chutes): o encolhimento puxa a comparação para o esperado — leia o setor preferido com cautela.
        </p>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
        <div className="bg-slate-900 rounded-xl px-3 py-2 text-center">
          <div className="text-[10px] text-slate-500 uppercase">Chutes</div>
          <div className="text-sm font-bold text-slate-100">{n}</div>
        </div>
        <div className="bg-slate-900 rounded-xl px-3 py-2 text-center">
          <div className="text-[10px] text-slate-500 uppercase">Gols (xG)</div>
          <div className="text-sm font-bold text-slate-100">{ficha.gols} <span className="text-slate-500 font-normal">({ficha.xg.toFixed(1)})</span></div>
        </div>
        <div className="bg-slate-900 rounded-xl px-3 py-2 text-center" title="Zona com mais chutes do jogador">
          <div className="text-[10px] text-slate-500 uppercase">Setor preferido</div>
          <div className="text-sm font-bold text-emerald-400">{ZONAS_CHUTE[idxMax].label}</div>
          <div className="text-[9px] text-slate-600">{pct1(distribuicao[idxMax])} dos chutes</div>
        </div>
        <div className="bg-slate-900 rounded-xl px-3 py-2 text-center">
          <div className="text-[10px] text-slate-500 uppercase">Perfil</div>
          <div className="text-[11px] text-slate-200">{pct1(ficha.pct_central)} no cone central</div>
          <div className="text-[11px] text-slate-400">{pct1(ficha.pct_cabeca)} de cabeça</div>
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-4 items-start">
        <MapaZonasChute distribuicao={distribuicao} tabela={false} alternarMetrica={false} />

        <div>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] text-slate-300 border-collapse">
              <thead>
                <tr className="text-slate-400 border-b border-slate-700">
                  <th className="text-left py-1.5 pr-2 font-semibold"> </th>
                  <th className="text-right px-2 font-semibold">Real</th>
                  <th className="text-right px-2 font-semibold">Esperado</th>
                  <th className="text-right pl-2 font-semibold">Diferença encolhida ± IC95</th>
                </tr>
              </thead>
              <tbody>
                {linhas.map((l) => {
                  const v = veredito(l.est?.encolhida, l.est?.ic);
                  return (
                    <tr key={l.nome} className="border-b border-slate-800 align-top">
                      <td className="py-1.5 pr-2">{l.nome}</td>
                      <td className="text-right px-2 font-mono">{l.obs != null ? pct1(l.obs) : '—'}</td>
                      <td className="text-right px-2 font-mono">{l.esp != null ? pct1(l.esp) : '—'}</td>
                      <td className="text-right pl-2">
                        {l.est ? (
                          <>
                            <span className="font-mono">{pp(l.est.encolhida)} ± {(l.est.ic * 100).toFixed(1)}</span>
                            <div className={`text-[10px] ${v.cor}`}>{v.texto}</div>
                            <div className="text-[9px] text-slate-600">bruta {pp(l.est.bruta)} · peso do jogador {(l.est.fator * 100).toFixed(0)}%</div>
                          </>
                        ) : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <table className="w-full text-[11px] text-slate-300 border-collapse mt-4">
            <thead>
              <tr className="text-slate-400 border-b border-slate-700">
                <th className="text-left py-1.5 pr-2 font-semibold">Zonas mais usadas</th>
                <th className="text-right px-2 font-semibold">Chutes</th>
                <th className="text-right px-2 font-semibold">% do jogador</th>
                <th className="text-right pl-2 font-semibold">Gols</th>
              </tr>
            </thead>
            <tbody>
              {topZonas.map(({ i, p }) => (
                <tr key={i} className="border-b border-slate-800">
                  <td className="py-1 pr-2">{ZONAS_CHUTE[i].label}</td>
                  <td className="text-right px-2 font-mono">{porZona[i].n}</td>
                  <td className="text-right px-2 font-mono">{pct1(p)}</td>
                  <td className="text-right pl-2 font-mono">{porZona[i].gols}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {serie.length > 0 && (
        <div className="mt-5 overflow-x-auto">
          <h3 className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">Por temporada (cada uma isolada, encolhida ± IC95)</h3>
          <table className="w-full text-[11px] text-slate-300 border-collapse">
            <thead>
              <tr className="text-slate-400 border-b border-slate-700">
                <th className="text-left py-1.5 pr-2 font-semibold">Temporada</th>
                <th className="text-right px-2 font-semibold">Chutes</th>
                <th className="text-right px-2 font-semibold">% central</th>
                <th className="text-center px-2 font-semibold">Precisão</th>
                <th className="text-center px-2 font-semibold">Colocação</th>
                <th className="text-center pl-2 font-semibold">Gol/chute</th>
              </tr>
            </thead>
            <tbody>
              {serie.map((s) => (
                <tr key={s.temporada} className="border-b border-slate-800">
                  <td className="py-1 pr-2 font-mono">{s.temporada}/{String(s.temporada + 1).slice(2)}</td>
                  <td className="text-right px-2 font-mono">{s.n}</td>
                  <td className="text-right px-2 font-mono">{pct1(s.pct_central)}</td>
                  <td className="px-2"><ForestMini valor={s.alvo?.encolhida} ic={s.alvo?.ic} dominio={dominio('alvo')} /></td>
                  <td className="px-2"><ForestMini valor={s.colocacao?.encolhida} ic={s.colocacao?.ic} dominio={dominio('colocacao')} /></td>
                  <td className="pl-2"><ForestMini valor={s.gol?.encolhida} ic={s.gol?.ic} dominio={dominio('gol')} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-[10px] text-slate-500 mt-1">
            Barra = estimativa encolhida ± IC95 em torno do esperado (linha central = 0); verde/vermelho só quando o intervalo
            não cobre zero. Temporadas curtas ficam coladas no zero de propósito — não leia oscilação entre barras como
            melhora ou piora sem o intervalo se afastar do zero.
          </p>
        </div>
      )}

      <p className="text-[10px] text-slate-500 mt-4 leading-relaxed">
        "Esperado" = taxa de toda a base para chutes da mesma zona, mesma parte do corpo (cabeça/pé) e mesmo tipo de
        jogada (bola rolando/parada), sem pênaltis. "No alvo" = gol ou defesa do goleiro; chutes bloqueados ficam fora
        da precisão (o FotMob os marca como "no alvo"). Colocação = xGOT do chute (probabilidade de gol dado onde a bola
        foi) menos o esperado da zona, só nos chutes no alvo; conversão = gol menos xGOT (goleiro e sorte pesam muito). Real e
        esperado da tabela são contagens simples da janela; a diferença encolhida usa o peso por meia-vida. O encolhimento puxa
        em direção ao esperado: a habilidade individual medida é modesta (confiabilidade por metades: 0,31 no alvo, 0,20 no
        gol, 0,16 na colocação, 0,13 na conversão). Entre temporadas o setor preferido persiste (distância 0,89, setor
        central 0,74) e o volume de chutes oscila (0,51), mas a pontaria acima do esperado quase não persiste (0,15). O
        decaimento não melhorou a previsão no teste walk-forward. Todas as ligas juntas, sem ajuste por força do time.
      </p>
    </div>
  );
}
