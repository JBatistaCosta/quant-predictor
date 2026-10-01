// Ficha de chutes de um jogador: setor preferido (14 zonas polares), precisão (no alvo)
// e gol, COMPARADOS com o esperado para quem chuta das mesmas posições (zona x
// cabeça/pé x bola rolando/parada) e com encolhimento (empirical Bayes).
// Tudo vem da RPC `ficha_chutes_jogador` (migration 20261001120000) -- a regra de zona
// mora só no SQL (`zona_chute`).
//
// Por que não mostrar só a taxa bruta: a taxa bruta de gol é quase só função da
// distância (r = -0,70 entre distância média e gol/chute). Controlando a posição, a
// preferência de setor não prevê mais precisão nem gol, e a habilidade individual é
// modesta (confiabilidade por metades: 0,31 no alvo, 0,20 no gol). Por isso a
// diferença "encolhida" é a que vale: puxa para o esperado quem tem poucos chutes.
import { useEffect, useState } from 'react';
import { Crosshair, Loader2, AlertTriangle } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { ZONAS_CHUTE } from '../utils/zoneTransitionMatrix';
import MapaZonasChute from './MapaZonasChute';

const pct1 = (v) => `${(v * 100).toFixed(1)}%`;
const pp = (v) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)} p.p.`;

function veredito(estimativa, ic) {
  if (estimativa == null || ic == null) return { texto: '—', cor: 'text-slate-500' };
  if (Math.abs(estimativa) <= ic) return { texto: 'indistinguível do esperado', cor: 'text-slate-400' };
  return estimativa > 0
    ? { texto: 'acima do esperado', cor: 'text-emerald-400' }
    : { texto: 'abaixo do esperado', cor: 'text-red-400' };
}

export default function FichaChutesJogador({ playerId }) {
  const [ficha, setFicha] = useState(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');

  useEffect(() => {
    if (!supabaseAtivo) { setCarregando(false); return; }
    let cancelado = false;
    (async () => {
      setCarregando(true);
      setErro('');
      const { data, error } = await supabase.rpc('ficha_chutes_jogador', { p_player_id: Number(playerId) });
      if (cancelado) return;
      if (error) setErro(error.message);
      else setFicha(data);
      setCarregando(false);
    })();
    return () => { cancelado = true; };
  }, [playerId]);

  const titulo = (
    <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2 mb-3">
      <Crosshair className="text-emerald-400" size={16} /> Chutes: setor preferido e pontaria
    </h2>
  );

  if (carregando) {
    return <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5 mb-4">{titulo}<Loader2 className="animate-spin text-slate-500" size={18} /></div>;
  }
  if (erro) {
    return <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5 mb-4">{titulo}<p className="text-xs text-red-400">{erro}</p></div>;
  }
  if (!ficha || !ficha.n) {
    return <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5 mb-4">{titulo}<p className="text-xs text-slate-500">Sem chutes com coordenada registrados para este jogador.</p></div>;
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

  const precObs = ficha.n_desbloqueados > 0 ? ficha.no_alvo / ficha.n_desbloqueados : null;
  const precEsp = ficha.n_desbloqueados > 0 ? ficha.alvo_esperado / ficha.n_desbloqueados : null;
  const golObs = ficha.gols / n;
  const golEsp = ficha.gols_esperados / n;

  const linhas = [
    {
      nome: 'Precisão (no alvo entre chutes não bloqueados)',
      obs: precObs, esp: precEsp,
      bruta: ficha.alvo_acima, enc: ficha.alvo_acima_encolhido, ic: ficha.alvo_acima_ic, k: ficha.alvo_fator_encolhimento,
    },
    {
      nome: 'Gol por chute',
      obs: golObs, esp: golEsp,
      bruta: ficha.gol_acima, enc: ficha.gol_acima_encolhido, ic: ficha.gol_acima_ic, k: ficha.gol_fator_encolhimento,
    },
  ];

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5 mb-4">
      {titulo}

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
                  const v = veredito(l.enc, l.ic);
                  return (
                    <tr key={l.nome} className="border-b border-slate-800 align-top">
                      <td className="py-1.5 pr-2">{l.nome}</td>
                      <td className="text-right px-2 font-mono">{l.obs != null ? pct1(l.obs) : '—'}</td>
                      <td className="text-right px-2 font-mono">{l.esp != null ? pct1(l.esp) : '—'}</td>
                      <td className="text-right pl-2">
                        {l.enc != null ? (
                          <>
                            <span className="font-mono">{pp(l.enc)} ± {(l.ic * 100).toFixed(1)}</span>
                            <div className={`text-[10px] ${v.cor}`}>{v.texto}</div>
                            <div className="text-[9px] text-slate-600">bruta {pp(l.bruta)} · peso do jogador {(l.k * 100).toFixed(0)}%</div>
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

      <p className="text-[10px] text-slate-500 mt-4 leading-relaxed">
        "Esperado" = taxa de toda a base para chutes da mesma zona, mesma parte do corpo (cabeça/pé) e mesmo tipo de
        jogada (bola rolando/parada), sem pênaltis. "No alvo" = gol ou defesa do goleiro; chutes bloqueados ficam fora
        da precisão (o FotMob os marca como "no alvo"). A diferença é encolhida (empirical Bayes) em direção ao
        esperado: a habilidade individual medida é modesta (confiabilidade por metades de 0,31 no alvo e 0,20 no
        gol), então poucos chutes acima da média não provam finalizador. O setor preferido, por outro lado, é
        estável (0,86) — mas, controlada a posição, não prevê precisão nem gol (|r| ≤ 0,09 em 436 jogadores).
        Todas as ligas juntas, sem ajuste por força do time.
      </p>
    </div>
  );
}
