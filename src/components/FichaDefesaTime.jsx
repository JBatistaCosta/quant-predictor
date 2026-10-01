// Ficha DEFENSIVA de um time: fragilidade por zona de chute (chutes / no alvo / gols
// sofridos) nas mesmas 14 zonas polares da ficha do jogador, com data de corte, meia-vida e
// encolhimento. Dados: RPC `ficha_defesa_time` (migration 20261001220000) sobre a view
// materializada `mv_chute_def_jogo` (fotografia; atualizar com `recalcular_chute_def()`).
//
// Razão > 1 = o time permite MAIS chutes naquela zona do que os adversários costumam
// produzir (esperado = ataque médio do adversário por zona ANTES do jogo, mín. 10 jogos --
// controla a força do adversário). O volume é estável (confiabilidade por metades 0,82);
// já "no alvo/gol acima do esperado" é fraco (0,16/0,24: goleiro e sorte), por isso encolhido.
// Mando de campo: o esperado é multiplicado pelo fator do mando do DEFENSOR (em casa sofre 0,91x,
// fora 1,13x). Estado do jogo: testado e NÃO corrigido -- quem lidera sofre ~17% mais chutes, mas o
// ranking dos times é o mesmo olhando só o jogo empatado (r = 0,88): viés de segunda ordem. O índice
// mede "chutes permitidos" (estrutura defensiva + domínio de jogo), não habilidade defensiva pura.
// Meia-vida de 2 anos testada em walk-forward (2023/24/25): melhor ou empatada com 6 meses a 4 anos.
import { useEffect, useState } from 'react';
import { ShieldAlert, Loader2, AlertTriangle } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { ZONAS_CHUTE } from '../utils/zoneTransitionMatrix';
import MapaZonasChute from './MapaZonasChute';

const MEIAS_VIDA = [
  { valor: 182, rotulo: '6 meses' },
  { valor: 365, rotulo: '1 ano' },
  { valor: 730, rotulo: '2 anos (padrão)' },
  { valor: 0, rotulo: 'sem decaimento' },
];

const x2 = (v) => `${v.toFixed(2)}×`;
const dec = (v, n = 2) => v.toFixed(n);
const pp = (v) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)} p.p.`;

function vereditoVolume(est) {
  if (!est) return { texto: '—', cor: 'text-slate-500' };
  if (Math.abs(est.encolhida - 1) <= est.ic) return { texto: 'dentro do esperado', cor: 'text-slate-400' };
  return est.encolhida > 1
    ? { texto: 'defesa frágil (permite mais)', cor: 'text-red-400' }
    : { texto: 'defesa sólida (permite menos)', cor: 'text-emerald-400' };
}

function vereditoResiduo(est) {
  if (!est) return { texto: '—', cor: 'text-slate-500' };
  if (Math.abs(est.encolhida) <= est.ic) return { texto: 'indistinguível do esperado', cor: 'text-slate-400' };
  return est.encolhida > 0
    ? { texto: 'sofre mais do que o esperado', cor: 'text-red-400' }
    : { texto: 'sofre menos do que o esperado', cor: 'text-emerald-400' };
}

export default function FichaDefesaTime({ teamId }) {
  const [ficha, setFicha] = useState(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');
  const [ate, setAte] = useState('');
  const [meiaVida, setMeiaVida] = useState(730);

  useEffect(() => {
    if (!supabaseAtivo) { setCarregando(false); return; }
    let cancelado = false;
    (async () => {
      setCarregando(true);
      setErro('');
      const { data, error } = await supabase.rpc('ficha_defesa_time', {
        p_team_id: Number(teamId),
        p_ate: ate || null,
        p_meia_vida_dias: meiaVida,
      });
      if (cancelado) return;
      if (error) setErro(error.message);
      else setFicha(data);
      setCarregando(false);
    })();
    return () => { cancelado = true; };
  }, [teamId, ate, meiaVida]);

  const titulo = (
    <h2 className="text-sm font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2 mb-3">
      <ShieldAlert className="text-red-400" size={16} /> Defesa: fragilidade por zona de chute
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
        <span className="block mb-1">Peso dos jogos (meia-vida)</span>
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

  const caixa = (conteudo) => (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5 mb-4">{titulo}{conteudo}</div>
  );

  if (erro) return caixa(<>{controles}<p className="text-xs text-red-400">{erro}</p></>);
  if (!ficha) return caixa(<Loader2 className="animate-spin text-slate-500" size={18} />);
  if (!ficha.jogos) {
    return caixa(
      <>
        {controles}
        <p className="text-xs text-slate-500">
          Sem jogos com chutes registrados (e ao menos 10 jogos de histórico do adversário) antes de {ficha.corte}.
        </p>
      </>
    );
  }

  const porZona = Array(14).fill(null);
  (ficha.zonas || []).forEach((z) => { porZona[z.z] = z; });
  const valoresMapa = porZona.map((z) => (z?.razao ? z.razao.encolhida : 1));
  const v = vereditoVolume(ficha.volume);
  const va = vereditoResiduo(ficha.alvo_acima);
  const vg = vereditoResiduo(ficha.gol_acima);

  return caixa(
    <>
      {controles}

      <p className="text-[11px] text-slate-500 mb-3">
        {ficha.jogos} jogos de {ficha.primeiro_jogo} a {ficha.ultimo_jogo}, anteriores a {ficha.corte}
        {ficha.meia_vida_dias ? ` · meia-vida ${ficha.meia_vida_dias} dias (amostra efetiva ${Math.round(ficha.jogos_efetivos)} jogos)` : ' · sem decaimento'}.
      </p>

      {ficha.jogos < 30 && (
        <p className="text-[11px] text-yellow-400/80 mb-3 flex items-start gap-1.5">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          Poucos jogos ({ficha.jogos}): o encolhimento puxa a razão para 1 (igual ao esperado) — leia por zona com cautela.
        </p>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
        <div className="bg-slate-900 rounded-xl px-3 py-2 text-center" title="Razão chutes sofridos / chutes que os adversários costumam produzir, encolhida">
          <div className="text-[10px] text-slate-500 uppercase">Índice de fragilidade</div>
          <div className="text-sm font-bold text-slate-100">{x2(ficha.volume.encolhida)} <span className="text-slate-500 font-normal">± {dec(ficha.volume.ic)}</span></div>
          <div className={`text-[9px] ${v.cor}`}>{v.texto}</div>
        </div>
        <div className="bg-slate-900 rounded-xl px-3 py-2 text-center">
          <div className="text-[10px] text-slate-500 uppercase">Chutes sofridos/jogo</div>
          <div className="text-sm font-bold text-slate-100">{dec(ficha.chutes_sofridos_por_jogo, 1)}</div>
          <div className="text-[9px] text-slate-600">esperado {dec(ficha.chutes_esperados_por_jogo, 1)}</div>
        </div>
        <div className="bg-slate-900 rounded-xl px-3 py-2 text-center">
          <div className="text-[10px] text-slate-500 uppercase">No alvo / gols por jogo</div>
          <div className="text-sm font-bold text-slate-100">{dec(ficha.no_alvo_sofridos_por_jogo, 1)} / {dec(ficha.gols_sofridos_por_jogo)}</div>
        </div>
        <div className="bg-slate-900 rounded-xl px-3 py-2 text-center">
          <div className="text-[10px] text-slate-500 uppercase">xG por chute sofrido</div>
          <div className="text-sm font-bold text-slate-100">{dec(ficha.xg_por_chute_sofrido, 3)}</div>
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-4 items-start">
        <MapaZonasChute
          distribuicao={Array(14).fill(0)}
          tabela={false}
          alternarMetrica={false}
          valoresCustom={valoresMapa}
          formatoCustom={x2}
          centro={1}
          legendaCustom="razão chutes sofridos / esperado"
          tituloZona={(i) => {
            const z = porZona[i];
            if (!z) return `${ZONAS_CHUTE[i].label} — sem dados`;
            return `${ZONAS_CHUTE[i].label} — ${dec(z.chutes_por_jogo)} chutes/jogo (esperado ${dec(z.esperado_por_jogo)}) · razão ${z.razao ? `${x2(z.razao.encolhida)} ± ${dec(z.razao.ic)}` : '—'} · no alvo ${dec(z.no_alvo_por_jogo)} · gols ${dec(z.gols_por_jogo, 3)}/jogo (esperado ${dec(z.gols_esperados_por_jogo, 3)})`;
          }}
        />

        <div>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] text-slate-300 border-collapse">
              <thead>
                <tr className="text-slate-400 border-b border-slate-700">
                  <th className="text-left py-1.5 pr-2 font-semibold">Qualidade do que sofre</th>
                  <th className="text-right pl-2 font-semibold">Diferença encolhida ± IC95</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { nome: 'No alvo acima do esperado (entre chutes não bloqueados)', est: ficha.alvo_acima, ver: va },
                  { nome: 'Gol acima do esperado (por chute sofrido)', est: ficha.gol_acima, ver: vg },
                ].map((l) => (
                  <tr key={l.nome} className="border-b border-slate-800 align-top">
                    <td className="py-1.5 pr-2">{l.nome}</td>
                    <td className="text-right pl-2">
                      {l.est ? (
                        <>
                          <span className="font-mono">{pp(l.est.encolhida)} ± {(l.est.ic * 100).toFixed(1)}</span>
                          <div className={`text-[10px] ${l.ver.cor}`}>{l.ver.texto}</div>
                          <div className="text-[9px] text-slate-600">bruta {pp(l.est.bruta)} · peso do time {(l.est.fator * 100).toFixed(0)}%</div>
                        </>
                      ) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] text-slate-500 mt-2">
            Aqui entram goleiro e sorte: a confiabilidade por metades é 0,16 (no alvo) e 0,24 (gol), contra 0,82 do volume.
            Desconfie de qualquer leitura "o goleiro é ruim" sem o intervalo se afastar do zero.
          </p>
        </div>
      </div>

      <div className="overflow-x-auto mt-4">
        <table className="w-full text-[11px] text-slate-300 border-collapse">
          <thead>
            <tr className="text-slate-400 border-b border-slate-700">
              <th className="text-left py-1.5 pr-2 font-semibold">Zona</th>
              <th className="text-right px-2 font-semibold">Chutes/jogo</th>
              <th className="text-right px-2 font-semibold">Esperado</th>
              <th className="text-right px-2 font-semibold">Razão ± IC95</th>
              <th className="text-right px-2 font-semibold">No alvo/jogo</th>
              <th className="text-right pl-2 font-semibold">Gols/jogo (esp.)</th>
            </tr>
          </thead>
          <tbody>
            {porZona.map((z, i) => {
              if (!z) return null;
              const sig = z.razao && Math.abs(z.razao.encolhida - 1) > z.razao.ic;
              const cor = !sig ? '' : z.razao.encolhida > 1 ? 'text-red-400' : 'text-emerald-400';
              return (
                <tr key={i} className="border-b border-slate-800">
                  <td className="py-1 pr-2">{ZONAS_CHUTE[i].label}</td>
                  <td className="text-right px-2 font-mono">{dec(z.chutes_por_jogo)}</td>
                  <td className="text-right px-2 font-mono">{dec(z.esperado_por_jogo)}</td>
                  <td className={`text-right px-2 font-mono ${cor}`}>{z.razao ? `${x2(z.razao.encolhida)} ± ${dec(z.razao.ic)}` : '—'}</td>
                  <td className="text-right px-2 font-mono">{dec(z.no_alvo_por_jogo)}</td>
                  <td className="text-right pl-2 font-mono">{dec(z.gols_por_jogo, 3)} <span className="text-slate-500">({dec(z.gols_esperados_por_jogo, 3)})</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="text-[10px] text-slate-500 mt-4 leading-relaxed">
        Razão = chutes sofridos ÷ chutes que os adversários costumam produzir naquela zona (média do ataque deles
        antes do jogo, mín. 10 jogos), já ajustada pelo mando de campo do defensor: controla a força do adversário e
        o mando. O estado do jogo não é corrigido (quem lidera sofre mais chutes, mas o ranking é o mesmo só no jogo
        empatado). O índice mede chutes PERMITIDOS — mistura estrutura defensiva e domínio de jogo (time que fica com a
        bola sofre menos chutes), não é habilidade defensiva pura. Verde/vermelho só quando o intervalo não cobre 1.
        "Gols esperados" = o que a qualidade média dos chutes sofridos (zona, parte do corpo, tipo de jogada) renderia.
        A razão é encolhida em direção a 1 (variância entre times estimada na base: 232 times com ≥40 jogos). Só ligas
        bem cobertas pelo FotMob. A base é recalculada todo dia às 08:00 UTC (pg_cron).
      </p>
    </>
  );
}
