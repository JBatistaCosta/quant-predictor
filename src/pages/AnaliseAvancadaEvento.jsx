// src/pages/AnaliseAvancadaEvento.jsx — rota /analise-avancada/:matchId
//
// Painel "inspirado" em AnaliseEvento.jsx (/analise), mas invertendo o fluxo:
// lá o usuário escolhe times e fórmula manualmente; aqui os parâmetros do
// modelo misto (λ dos gols, ρ de Dixon-Coles, λ/dispersão/α/β de escanteios)
// são IMPRESSOS AUTOMATICAMENTE do banco (`model_match_estimates.params`)
// pra uma partida específica, sem nenhum campo editável. Todo o aparato de
// AnaliseEvento.jsx (Monte Carlo, Kelly, scanner EV+ multi-mercado, odds de
// bookmaker) fica de fora — esse painel só mostra o que o modelo misto já
// estimou e persistiu.
//
// Reaproveita a mesma matemática de `src/utils/distribuicoesMercados.js`
// (gêmeo JS de `scripts/distribuicoes.py`, com teste de paridade) — nenhuma
// derivação de mercado é escrita de novo aqui.
//
// `model_match_estimates` tem RLS de leitura pública, então a consulta é
// direta via supabase-js (sem função serverless nova — api/ já está no teto
// de 12 do plano Hobby do Vercel).
import React, { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, AlertTriangle, Shield, Loader2, FlaskConical, Target, TrendingUp, Percent } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import {
  matrizPlacares, mercadosDeGols, mercadosDeEscanteios, lerParametrosPartida, rotuloLinha,
} from '../utils/distribuicoesMercados';

const LINHAS_OU_GOLS = [0.5, 1.5, 2.5, 3.5, 4.5];
const LINHAS_HANDICAP = [-1.5, -1, -0.5, 0, 0.5, 1, 1.5];
const LINHAS_OU_CORNERS = [7.5, 8.5, 9.5, 10.5, 11.5];
const N_PLACARES_EXATOS = 12;

function Escudo({ url, tamanho = 20 }) {
  return url
    ? <img src={url} alt="" className="object-contain shrink-0" style={{ width: tamanho, height: tamanho }} />
    : <Shield size={tamanho * 0.8} className="text-slate-700 shrink-0" />;
}

function fmtPct(v) { return v == null ? '—' : `${(v * 100).toFixed(1)}%`; }
function fmtNum(v, casas = 3) { return v == null ? '—' : Number(v).toFixed(casas); }

function CardParametro({ label, valor }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-3">
      <div className="text-[10px] text-slate-500 uppercase tracking-wider">{label}</div>
      <div className="text-lg font-mono text-slate-100 mt-0.5">{valor}</div>
    </div>
  );
}

function Secao({ titulo, icone: Icone, children }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5">
      <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-2">
        {Icone && <Icone size={15} className="text-emerald-400" />} {titulo}
      </h3>
      {children}
    </div>
  );
}

function LinhaBinaria({ rotulo, over, under, rotuloOver = 'Over', rotuloUnder = 'Under' }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-slate-800 last:border-0 text-sm">
      <span className="text-slate-400 w-20 shrink-0">{rotulo}</span>
      <div className="flex gap-4 font-mono">
        <span className="text-emerald-400 w-24 text-right">{rotuloOver} {fmtPct(over)}</span>
        <span className="text-orange-400 w-24 text-right">{rotuloUnder} {fmtPct(under)}</span>
      </div>
    </div>
  );
}

export default function AnaliseAvancadaEvento() {
  const { matchId } = useParams();
  const [jogo, setJogo] = useState(null);
  const [estimativas, setEstimativas] = useState([]);
  const [modelSelecionado, setModelSelecionado] = useState(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState('');

  useEffect(() => {
    if (!supabaseAtivo) return;
    let cancelado = false;
    (async () => {
      setCarregando(true);
      setErro('');
      const [{ data: j, error: erroJogo }, { data: est, error: erroEst }] = await Promise.all([
        supabase
          .from('matches')
          .select('id, match_date, status, home_goals, away_goals, leagues(name), home:teams!matches_home_team_id_fkey(id,name,crest_url), away:teams!matches_away_team_id_fkey(id,name,crest_url)')
          .eq('id', matchId)
          .single(),
        supabase
          .from('model_match_estimates')
          .select('model_name, params')
          .eq('match_id', matchId)
          .not('params', 'is', null),
      ]);
      if (cancelado) return;

      if (erroJogo || !j) { setErro('Jogo não encontrado.'); setCarregando(false); return; }
      if (erroEst) { setErro(erroEst.message); setCarregando(false); return; }

      setJogo(j);
      const validas = (est || []).filter((e) => lerParametrosPartida(e.params) != null);
      setEstimativas(validas);
      setModelSelecionado(validas[0]?.model_name ?? null);
      setCarregando(false);
    })();
    return () => { cancelado = true; };
  }, [matchId]);

  const estimativaAtiva = useMemo(
    () => estimativas.find((e) => e.model_name === modelSelecionado) || null,
    [estimativas, modelSelecionado]
  );

  const parametros = useMemo(
    () => (estimativaAtiva ? lerParametrosPartida(estimativaAtiva.params) : null),
    [estimativaAtiva]
  );

  const mercadosGols = useMemo(() => {
    if (!parametros) return null;
    const matriz = matrizPlacares(parametros.lambdaHome, parametros.lambdaAway, parametros.rho);
    return mercadosDeGols(matriz, { linhasOverUnder: LINHAS_OU_GOLS, linhasHandicap: LINHAS_HANDICAP });
  }, [parametros]);

  const mercadosCorners = useMemo(() => {
    if (!parametros?.cornersLambdaTotal || !parametros?.cornersDispR) return null;
    return mercadosDeEscanteios(
      parametros.cornersLambdaTotal, parametros.cornersDispR,
      parametros.cornersAlpha || 1, parametros.cornersBeta || 1,
      { linhasTotais: LINHAS_OU_CORNERS }
    );
  }, [parametros]);

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
      <div className="flex items-center justify-between mb-4">
        <Link to="/eventos" className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 text-sm w-fit">
          <ArrowLeft size={16} /> Voltar
        </Link>
        <Link
          to={`/historico/${jogo.id}`}
          className="flex items-center gap-1.5 bg-slate-700/40 hover:bg-slate-700 text-slate-300 text-xs font-bold px-3 py-1.5 rounded-lg transition-colors"
        >
          Forma recente & confronto direto
        </Link>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 mb-4">
        <p className="text-center text-xs text-slate-500 uppercase tracking-wider mb-1 flex items-center justify-center gap-1.5">
          <FlaskConical size={12} className="text-emerald-400" /> Análise Avançada — Modelo Misto
        </p>
        <p className="text-center text-xs text-slate-600 mb-3">
          {jogo.leagues?.name || 'Confronto'}
          {jogo.match_date && ` · ${new Date(jogo.match_date).toLocaleString('pt-BR', { dateStyle: 'long', timeStyle: 'short' })}`}
        </p>
        <div className="flex items-center justify-center gap-4">
          <div className="flex items-center gap-2">
            <Escudo url={jogo.home?.crest_url} tamanho={32} />
            <span className="font-bold text-slate-100">{jogo.home?.name}</span>
          </div>
          <span className="text-slate-500 font-mono">
            {jogo.status === 'finished' ? `${jogo.home_goals}-${jogo.away_goals}` : 'vs'}
          </span>
          <div className="flex items-center gap-2">
            <span className="font-bold text-slate-100">{jogo.away?.name}</span>
            <Escudo url={jogo.away?.crest_url} tamanho={32} />
          </div>
        </div>
      </div>

      {estimativas.length === 0 ? (
        <div className="bg-slate-800 border border-slate-700 rounded-2xl p-8 text-center">
          <FlaskConical className="text-slate-600 mx-auto mb-3" size={32} />
          <p className="text-slate-300 font-semibold">Nenhuma estimativa do modelo misto para esta partida ainda.</p>
          <p className="text-slate-500 text-sm mt-2 max-w-md mx-auto">
            O modelo misto (λ estimado por ML + Dixon-Coles/Binomial Negativa) só cobre partidas
            que passaram pelo pipeline de treino (<code className="text-slate-400">scripts/treinar_modelo_hibrido.py</code> ou
            uma config de Treino Customizado com algoritmo <code className="text-slate-400">hibrido_parametrico</code>).
            Essa partida pode estar fora do escopo de liga/temporada treinado, ou o treino ainda não rodou.
          </p>
        </div>
      ) : (
        <>
          {estimativas.length > 1 && (
            <div className="flex gap-1 mb-4 bg-slate-800 border border-slate-700 rounded-2xl p-1.5 w-fit flex-wrap">
              {estimativas.map((e) => (
                <button
                  key={e.model_name}
                  onClick={() => setModelSelecionado(e.model_name)}
                  className={`px-3.5 py-2 rounded-xl text-sm font-bold transition-colors ${
                    modelSelecionado === e.model_name ? 'bg-emerald-500/20 text-emerald-400' : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {e.model_name}
                </button>
              ))}
            </div>
          )}

          <Secao titulo="Parâmetros do modelo (direto do banco)" icone={FlaskConical}>
            <p className="text-[11px] text-slate-500 mb-3">
              Modelo: <span className="text-slate-300 font-mono">{estimativaAtiva?.model_name}</span> — leitura de
              {' '}<code className="text-slate-400">model_match_estimates.params</code>, sem edição manual.
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <CardParametro label="λ mandante" valor={fmtNum(parametros?.lambdaHome, 2)} />
              <CardParametro label="λ visitante" valor={fmtNum(parametros?.lambdaAway, 2)} />
              <CardParametro label="ρ (Dixon-Coles)" valor={fmtNum(parametros?.rho, 4)} />
              {parametros?.cornersLambdaTotal != null && (
                <CardParametro label="λ escanteios (total)" valor={fmtNum(parametros.cornersLambdaTotal, 2)} />
              )}
              {parametros?.cornersDispR != null && (
                <CardParametro label="Dispersão r (escanteios)" valor={fmtNum(parametros.cornersDispR, 1)} />
              )}
              {parametros?.cornersAlpha != null && (
                <CardParametro label="α (split casa/fora)" valor={fmtNum(parametros.cornersAlpha, 2)} />
              )}
              {parametros?.cornersBeta != null && (
                <CardParametro label="β (split casa/fora)" valor={fmtNum(parametros.cornersBeta, 2)} />
              )}
            </div>
          </Secao>

          {mercadosGols && (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-4">
                <div className="bg-slate-900 border-t-4 border-emerald-500 border border-slate-700 rounded-xl p-5 flex flex-col items-center text-center">
                  <span className="text-slate-400 text-[10px] uppercase tracking-wider mb-2 line-clamp-1">{jogo.home?.name}</span>
                  <span className="text-2xl font-black text-white">{fmtPct(mercadosGols['1X2'].home)}</span>
                </div>
                <div className="bg-slate-900 border-t-4 border-slate-500 border border-slate-700 rounded-xl p-5 flex flex-col items-center text-center">
                  <span className="text-slate-400 text-[10px] uppercase tracking-wider mb-2">Empate</span>
                  <span className="text-2xl font-black text-white">{fmtPct(mercadosGols['1X2'].draw)}</span>
                </div>
                <div className="bg-slate-900 border-t-4 border-orange-500 border border-slate-700 rounded-xl p-5 flex flex-col items-center text-center">
                  <span className="text-slate-400 text-[10px] uppercase tracking-wider mb-2 line-clamp-1">{jogo.away?.name}</span>
                  <span className="text-2xl font-black text-white">{fmtPct(mercadosGols['1X2'].away)}</span>
                </div>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                <Secao titulo="Dupla chance & BTTS" icone={Percent}>
                  <div className="grid grid-cols-3 gap-2 mb-3">
                    <CardParametro label="1X" valor={fmtPct(mercadosGols.dupla_chance['1X'])} />
                    <CardParametro label="12" valor={fmtPct(mercadosGols.dupla_chance['12'])} />
                    <CardParametro label="X2" valor={fmtPct(mercadosGols.dupla_chance.X2)} />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <CardParametro label="Ambas marcam — Sim" valor={fmtPct(mercadosGols.btts.yes)} />
                    <CardParametro label="Ambas marcam — Não" valor={fmtPct(mercadosGols.btts.no)} />
                  </div>
                </Secao>

                <Secao titulo="Faixa de gols" icone={Target}>
                  <div className="grid grid-cols-2 gap-2">
                    {Object.entries(mercadosGols.faixa_gols).map(([faixa, p]) => (
                      <CardParametro key={faixa} label={`${faixa} gols`} valor={fmtPct(p)} />
                    ))}
                  </div>
                </Secao>
              </div>

              <Secao titulo="Over / Under gols" icone={TrendingUp}>
                {LINHAS_OU_GOLS.map((linha) => {
                  const m = mercadosGols[`over_under_${rotuloLinha(linha)}`];
                  return <LinhaBinaria key={linha} rotulo={rotuloLinha(linha)} over={m?.over} under={m?.under} />;
                })}
              </Secao>

              <Secao titulo="Handicap asiático (mandante)" icone={Target}>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-slate-500 text-xs uppercase">
                        <th className="text-left pb-2">Linha</th>
                        <th className="text-right pb-2">{jogo.home?.name}</th>
                        <th className="text-right pb-2">Push</th>
                        <th className="text-right pb-2">{jogo.away?.name}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {LINHAS_HANDICAP.map((linha) => {
                        const h = mercadosGols[`handicap_${rotuloLinha(linha)}`];
                        return (
                          <tr key={linha} className="border-t border-slate-800">
                            <td className="py-1.5 text-slate-300 font-mono">{linha > 0 ? `+${rotuloLinha(linha)}` : rotuloLinha(linha)}</td>
                            <td className="py-1.5 text-right font-mono text-emerald-400">{fmtPct(h?.home)}</td>
                            <td className="py-1.5 text-right font-mono text-slate-500">{h?.push ? fmtPct(h.push) : '—'}</td>
                            <td className="py-1.5 text-right font-mono text-orange-400">{fmtPct(h?.away)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Secao>

              <Secao titulo="Placar exato (mais prováveis)" icone={Target}>
                <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
                  {Object.entries(mercadosGols.placar_exato).slice(0, N_PLACARES_EXATOS).map(([placar, p]) => (
                    <CardParametro key={placar} label={placar} valor={fmtPct(p)} />
                  ))}
                </div>
              </Secao>
            </>
          )}

          {mercadosCorners && (
            <>
              <Secao titulo="Escanteios — Over / Under (total)" icone={TrendingUp}>
                {LINHAS_OU_CORNERS.map((linha) => {
                  const m = mercadosCorners[`corners_over_under_${rotuloLinha(linha)}`];
                  return <LinhaBinaria key={linha} rotulo={rotuloLinha(linha)} over={m?.over} under={m?.under} />;
                })}
              </Secao>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                <Secao titulo="Escanteios — faixa & 1X2" icone={Target}>
                  <div className="grid grid-cols-2 gap-2 mb-3">
                    {Object.entries(mercadosCorners.faixa_corners).map(([faixa, p]) => (
                      <CardParametro key={faixa} label={`${faixa} escanteios`} valor={fmtPct(p)} />
                    ))}
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <CardParametro label="Mais escanteios: casa" valor={fmtPct(mercadosCorners.corners_1X2.home)} />
                    <CardParametro label="Empate" valor={fmtPct(mercadosCorners.corners_1X2.draw)} />
                    <CardParametro label="Mais escanteios: fora" valor={fmtPct(mercadosCorners.corners_1X2.away)} />
                  </div>
                </Secao>

                <Secao titulo="Escanteios por time" icone={Percent}>
                  {[3.5, 4.5, 5.5, 6.5].map((linha) => (
                    <div key={linha} className="mb-3 last:mb-0">
                      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Linha {rotuloLinha(linha)}</p>
                      <LinhaBinaria
                        rotulo={jogo.home?.name?.slice(0, 10) || 'Casa'}
                        over={mercadosCorners[`corners_home_over_under_${rotuloLinha(linha)}`]?.over}
                        under={mercadosCorners[`corners_home_over_under_${rotuloLinha(linha)}`]?.under}
                      />
                      <LinhaBinaria
                        rotulo={jogo.away?.name?.slice(0, 10) || 'Fora'}
                        over={mercadosCorners[`corners_away_over_under_${rotuloLinha(linha)}`]?.over}
                        under={mercadosCorners[`corners_away_over_under_${rotuloLinha(linha)}`]?.under}
                      />
                    </div>
                  ))}
                </Secao>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
