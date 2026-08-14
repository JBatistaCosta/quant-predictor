// src/components/Layout.jsx
import React, { useState, useEffect } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { Zap, Calendar, PlusCircle, Users, Trophy, Calculator, LogOut, Menu, X, Download, StickyNote, Trash2, Copy, ChevronLeft, ChevronRight, BarChart3, TrendingUp, UserRound, Wallet, CalendarRange, FlaskConical, Layers, Database, Settings } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { useAuth } from '../AuthContext';

// Duas "visões": CONSUMO (só olhar o que já existe) e CADASTRO (criar/editar dados).
const GRUPOS_MENU = [
  {
    grupo: 'Consumo',
    itens: [
      { to: '/eventos', label: 'Eventos', icone: Calendar },
      { to: '/analise', label: 'Análise de Evento', icone: Calculator },
      { to: '/modelos', label: 'Estatísticas dos Modelos', icone: BarChart3 },
      { to: '/ratings', label: 'Rating dos Clubes', icone: TrendingUp },
      { to: '/jogadores', label: 'Jogadores', icone: UserRound },
      { to: '/rodada-atual', label: 'Rodada Atual (Brasileirão)', icone: CalendarRange },
      { to: '/model-benchmarking', label: 'Model Benchmarking', icone: Zap },
      { to: '/simulacao-carteira', label: 'Simulação de Carteira', icone: Wallet },
      { to: '/treino-custom', label: 'Treino Customizado', icone: FlaskConical },
      { to: '/modelo-v9', label: 'Modelo v9', icone: Layers },
      { to: '/explorar-dados', label: 'Explorar Dados', icone: Database },
    ],
  },
  {
    grupo: 'Cadastro',
    itens: [
      { to: '/eventos/novo', label: 'Novo Evento', icone: PlusCircle },
      { to: '/importar', label: 'Importar Jogos', icone: Download },
      { to: '/times', label: 'Times', icone: Users },
      { to: '/ligas', label: 'Ligas', icone: Trophy },
    ],
  },
  {
    grupo: 'Configurações',
    itens: [
      { to: '/configuracoes', label: 'Configurações', icone: Settings },
    ],
  },
];

function ItemMenu({ item, mobile, onClick }) {
  return (
    <NavLink
      to={item.to}
      onClick={onClick}
      className={({ isActive }) =>
        `flex items-center gap-1.5 ${mobile ? 'px-3 py-2.5' : 'px-3 py-2'} rounded-lg text-sm font-semibold transition-colors ${
          isActive ? 'bg-emerald-500/20 text-emerald-400' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50'
        }`
      }
    >
      <item.icone size={mobile ? 18 : 16} /> {item.label}
    </NavLink>
  );
}

export default function Layout({ children }) {
  const [menuAberto, setMenuAberto] = useState(false);
  const navigate = useNavigate();
  const { session } = useAuth();

  const sair = async () => {
    if (supabaseAtivo) await supabase.auth.signOut();
    navigate('/login');
  };

  // Bloco de notas global — acessível de qualquer página, pra facilitar
  // anotações que precisam sobreviver entre sessões e dispositivos.
  const [notaAberta, setNotaAberta] = useState(false);
  const [notas, setNotas] = useState([]);
  const [tituloNota, setTituloNota] = useState('');
  const [novaNota, setNovaNota] = useState('');
  const [notaExpandidaId, setNotaExpandidaId] = useState(null);
  const [notaCopiadaId, setNotaCopiadaId] = useState(null);
  const [paginaNotas, setPaginaNotas] = useState(0);
  const NOTAS_POR_PAGINA = 5;

  const buscarNotas = async () => {
    if (!supabaseAtivo) return;
    const { data, error } = await supabase.from('notas').select('*').order('criado_em', { ascending: false });
    if (error) { console.warn('Falha ao buscar notas:', error.message); return; }
    setNotas(data || []);
    setPaginaNotas(0);
  };

  const salvarNota = async () => {
    if (!supabaseAtivo || !novaNota.trim()) return;
    const { error } = await supabase.from('notas').insert({ titulo: tituloNota.trim() || null, conteudo: novaNota.trim() });
    if (error) { console.warn('Falha ao salvar nota:', error.message); return; }
    setTituloNota('');
    setNovaNota('');
    buscarNotas();
  };

  const excluirNota = async (id) => {
    const { error } = await supabase.from('notas').delete().eq('id', id);
    if (error) { console.warn('Falha ao excluir nota:', error.message); return; }
    buscarNotas();
  };

  const copiarNota = (nota) => {
    const texto = nota.titulo ? `${nota.titulo}\n\n${nota.conteudo}` : nota.conteudo;
    navigator.clipboard.writeText(texto);
    setNotaCopiadaId(nota.id);
    setTimeout(() => setNotaCopiadaId(null), 2000);
  };

  useEffect(() => { if (notaAberta) buscarNotas(); }, [notaAberta]);

  const totalPaginasNotas = Math.max(1, Math.ceil(notas.length / NOTAS_POR_PAGINA));
  const notasDaPagina = notas.slice(paginaNotas * NOTAS_POR_PAGINA, (paginaNotas + 1) * NOTAS_POR_PAGINA);

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 font-sans">
      {/* Barra superior */}
      <div className="bg-slate-800 border-b border-slate-700 px-4 py-3 flex items-center justify-between sticky top-0 z-50">
        <div className="flex items-center gap-2">
          <button onClick={() => setMenuAberto(!menuAberto)} className="md:hidden text-slate-300">
            {menuAberto ? <X size={22} /> : <Menu size={22} />}
          </button>
          <Zap className="text-emerald-400" size={22} />
          <span className="font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-blue-500">
            Quant System Predictor
          </span>
        </div>

        {/* Menu desktop: dois grupos separados por uma divisória, com rótulo pequeno acima de cada um */}
        <nav className="hidden md:flex items-end gap-4">
          {GRUPOS_MENU.map((g, gi) => (
            <div key={g.grupo} className={`flex items-center gap-1 ${gi > 0 ? 'pl-4 border-l border-slate-700' : ''}`}>
              <div className="flex flex-col">
                <span className="text-[9px] uppercase tracking-wider text-slate-600 font-bold mb-1 ml-1">{g.grupo}</span>
                <div className="flex items-center gap-1">
                  {g.itens.map(item => <ItemMenu key={item.to} item={item} />)}
                </div>
              </div>
            </div>
          ))}
        </nav>

        <div className="flex items-center gap-3">
          {supabaseAtivo && (
            <button
              onClick={() => setNotaAberta(true)}
              className="flex items-center gap-1.5 text-xs font-bold text-slate-400 hover:text-yellow-400 transition-colors"
              title="Bloco de Notas"
            >
              <StickyNote size={16} /> <span className="hidden md:inline">Notas</span>
            </button>
          )}
          {session?.user?.email && (
            <span className="hidden md:inline text-xs text-slate-500">{session.user.email}</span>
          )}
          {supabaseAtivo && (
            <button onClick={sair} className="flex items-center gap-1.5 text-xs font-bold text-slate-400 hover:text-red-400 transition-colors">
              <LogOut size={16} /> <span className="hidden md:inline">Sair</span>
            </button>
          )}
        </div>
      </div>

      {/* BLOCO DE NOTAS GLOBAL — painel deslizante, acessível de qualquer página */}
      {notaAberta && (
        <>
          <div className="fixed inset-0 bg-black/50 z-[59]" onClick={() => setNotaAberta(false)} />
          <div className="fixed right-0 top-0 h-full w-full sm:w-96 bg-slate-800 border-l border-slate-700 z-[60] flex flex-col shadow-2xl">
            <div className="flex items-center justify-between p-4 border-b border-slate-700">
              <h2 className="text-sm font-bold text-slate-100 flex items-center gap-2">
                <StickyNote className="text-yellow-400" size={18} /> Bloco de Notas
              </h2>
              <button onClick={() => setNotaAberta(false)} className="text-slate-400 hover:text-slate-200">
                <X size={20} />
              </button>
            </div>

            <div className="p-4 border-b border-slate-700">
              <input
                type="text"
                value={tituloNota}
                onChange={(e) => setTituloNota(e.target.value)}
                placeholder="Título (opcional)"
                className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2 text-sm text-slate-100 outline-none focus:border-yellow-500/50 mb-2"
              />
              <textarea
                value={novaNota}
                onChange={(e) => setNovaNota(e.target.value)}
                placeholder="Escreva uma nota..."
                className="w-full h-24 bg-slate-900 border border-slate-600 rounded-lg p-3 text-sm text-slate-100 outline-none focus:border-yellow-500/50 resize-none"
              />
              <button
                onClick={salvarNota}
                disabled={!novaNota.trim()}
                className="mt-2 w-full bg-yellow-600 hover:bg-yellow-500 disabled:opacity-30 disabled:cursor-not-allowed text-white font-bold text-sm py-2 rounded-lg transition-colors"
              >
                Salvar Nota
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-2">
              {notas.length === 0 ? (
                <p className="text-sm text-slate-500 text-center py-6">Nenhuma nota salva ainda.</p>
              ) : (
                notasDaPagina.map(nota => {
                  const expandida = notaExpandidaId === nota.id;
                  return (
                    <div key={nota.id} className="bg-slate-900 border border-slate-700 rounded-lg p-3 group">
                      <button
                        onClick={() => setNotaExpandidaId(expandida ? null : nota.id)}
                        className="w-full text-left"
                      >
                        <span className="text-sm font-bold text-yellow-400 block truncate">{nota.titulo || 'Sem título'}</span>
                        {expandida ? (
                          <p className="text-sm text-slate-200 whitespace-pre-wrap break-words mt-1">{nota.conteudo}</p>
                        ) : (
                          <p className="text-sm text-slate-400 truncate mt-1">{nota.conteudo}</p>
                        )}
                      </button>
                      <div className="flex items-center justify-between mt-2">
                        <span className="text-[10px] text-slate-500">{new Date(nota.criado_em).toLocaleString('pt-BR')}</span>
                        <div className="flex items-center gap-3">
                          {notaCopiadaId === nota.id && <span className="text-[10px] text-emerald-400">Copiado!</span>}
                          <button
                            onClick={() => copiarNota(nota)}
                            className="text-slate-600 hover:text-emerald-400 transition-colors opacity-0 group-hover:opacity-100"
                            title="Copiar nota"
                          >
                            <Copy size={14} />
                          </button>
                          <button
                            onClick={() => excluirNota(nota.id)}
                            className="text-slate-600 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100"
                            title="Excluir nota"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>

            {notas.length > NOTAS_POR_PAGINA && (
              <div className="flex items-center justify-between p-3 border-t border-slate-700">
                <button
                  onClick={() => setPaginaNotas(p => Math.max(0, p - 1))}
                  disabled={paginaNotas === 0}
                  className="flex items-center gap-1 text-xs font-bold text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  <ChevronLeft size={14}/> Anterior
                </button>
                <span className="text-[11px] text-slate-500">Página {paginaNotas + 1} de {totalPaginasNotas}</span>
                <button
                  onClick={() => setPaginaNotas(p => Math.min(totalPaginasNotas - 1, p + 1))}
                  disabled={paginaNotas >= totalPaginasNotas - 1}
                  className="flex items-center gap-1 text-xs font-bold text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  Próxima <ChevronRight size={14}/>
                </button>
              </div>
            )}
          </div>
        </>
      )}

      {/* Menu mobile (expande abaixo da barra), também em dois grupos com título */}
      {menuAberto && (
        <nav className="md:hidden bg-slate-800 border-b border-slate-700 px-4 py-3 flex flex-col gap-3">
          {GRUPOS_MENU.map(g => (
            <div key={g.grupo}>
              <span className="text-[10px] uppercase tracking-wider text-slate-600 font-bold block mb-1 ml-1">{g.grupo}</span>
              <div className="flex flex-col gap-1">
                {g.itens.map(item => (
                  <ItemMenu key={item.to} item={item} mobile onClick={() => setMenuAberto(false)} />
                ))}
              </div>
            </div>
          ))}
        </nav>
      )}

      <div className="p-2 md:p-6">
        {children}
      </div>
    </div>
  );
}
