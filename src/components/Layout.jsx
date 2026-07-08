// src/components/Layout.jsx
import React, { useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { Zap, Calendar, PlusCircle, Users, Trophy, Calculator, LogOut, Menu, X } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';
import { useAuth } from '../AuthContext';

const ITENS_MENU = [
  { to: '/eventos', label: 'Eventos', icone: Calendar },
  { to: '/eventos/novo', label: 'Novo Evento', icone: PlusCircle },
  { to: '/analise', label: 'Análise de Evento', icone: Calculator },
  { to: '/times', label: 'Times', icone: Users },
  { to: '/ligas', label: 'Ligas', icone: Trophy },
];

export default function Layout({ children }) {
  const [menuAberto, setMenuAberto] = useState(false);
  const navigate = useNavigate();
  const { session } = useAuth();

  const sair = async () => {
    if (supabaseAtivo) await supabase.auth.signOut();
    navigate('/login');
  };

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

        {/* Menu desktop */}
        <nav className="hidden md:flex items-center gap-1">
          {ITENS_MENU.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold transition-colors ${
                  isActive ? 'bg-emerald-500/20 text-emerald-400' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50'
                }`
              }
            >
              <item.icone size={16} /> {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="flex items-center gap-3">
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

      {/* Menu mobile (expande abaixo da barra) */}
      {menuAberto && (
        <nav className="md:hidden bg-slate-800 border-b border-slate-700 px-4 py-2 flex flex-col gap-1">
          {ITENS_MENU.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              onClick={() => setMenuAberto(false)}
              className={({ isActive }) =>
                `flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm font-semibold transition-colors ${
                  isActive ? 'bg-emerald-500/20 text-emerald-400' : 'text-slate-400 hover:text-slate-200'
                }`
              }
            >
              <item.icone size={18} /> {item.label}
            </NavLink>
          ))}
        </nav>
      )}

      <div className="p-2 md:p-6">
        {children}
      </div>
    </div>
  );
}
