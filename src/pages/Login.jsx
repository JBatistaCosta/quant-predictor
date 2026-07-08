// src/pages/Login.jsx
import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Zap, LogIn, AlertTriangle } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

export default function Login() {
  const [email, setEmail] = useState('');
  const [senha, setSenha] = useState('');
  const [erro, setErro] = useState('');
  const [carregando, setCarregando] = useState(false);
  const navigate = useNavigate();

  const entrar = async (e) => {
    e.preventDefault();
    setErro('');
    setCarregando(true);
    const { error } = await supabase.auth.signInWithPassword({ email, password: senha });
    setCarregando(false);
    if (error) { setErro(error.message); return; }
    navigate('/analise');
  };

  if (!supabaseAtivo) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center p-4">
        <div className="bg-slate-800 border border-red-500/30 rounded-2xl p-6 max-w-md text-center">
          <AlertTriangle className="text-red-400 mx-auto mb-3" size={32} />
          <p className="text-slate-300">Supabase não configurado — login indisponível.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-900 flex items-center justify-center p-4">
      <form onSubmit={entrar} className="bg-slate-800 border border-slate-700 rounded-2xl p-8 w-full max-w-sm shadow-xl">
        <div className="flex items-center gap-2 mb-6 justify-center">
          <Zap className="text-emerald-400" size={28} />
          <span className="text-xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-blue-500">
            Quant System Predictor
          </span>
        </div>

        <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Email</label>
        <input
          type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
          className="w-full bg-slate-900 border border-slate-600 rounded-lg p-3 text-sm text-slate-100 mb-4"
        />

        <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Senha</label>
        <input
          type="password" required value={senha} onChange={(e) => setSenha(e.target.value)}
          className="w-full bg-slate-900 border border-slate-600 rounded-lg p-3 text-sm text-slate-100 mb-4"
        />

        {erro && <p className="text-red-400 text-xs mb-4">{erro}</p>}

        <button
          type="submit" disabled={carregando}
          className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-bold py-3 rounded-lg flex items-center justify-center gap-2 transition-colors"
        >
          <LogIn size={18} /> {carregando ? 'Entrando...' : 'Entrar'}
        </button>

        <p className="text-center text-xs text-slate-500 mt-5">
          Ainda não tem conta? <Link to="/cadastro" className="text-emerald-400 hover:underline">Criar conta</Link>
        </p>
      </form>
    </div>
  );
}
