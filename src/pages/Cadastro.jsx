// src/pages/Cadastro.jsx
// Página de criação de conta. Pensada pra só você e uma pessoa de confiança
// usarem — depois que os dois criarem conta, considere restringir novos
// cadastros (Supabase: Authentication > Settings > desative "Allow new users
// to sign up", ou me avise que eu ajusto isso de outra forma).
import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Zap, UserPlus, AlertTriangle, Check } from 'lucide-react';
import { supabase, supabaseAtivo } from '../supabaseClient';

export default function Cadastro() {
  const [email, setEmail] = useState('');
  const [senha, setSenha] = useState('');
  const [erro, setErro] = useState('');
  const [sucesso, setSucesso] = useState(false);
  const [carregando, setCarregando] = useState(false);
  const navigate = useNavigate();

  const criarConta = async (e) => {
    e.preventDefault();
    setErro('');
    setCarregando(true);
    const { error } = await supabase.auth.signUp({ email, password: senha });
    setCarregando(false);
    if (error) { setErro(error.message); return; }
    setSucesso(true);
  };

  if (!supabaseAtivo) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center p-4">
        <div className="bg-slate-800 border border-red-500/30 rounded-2xl p-6 max-w-md text-center">
          <AlertTriangle className="text-red-400 mx-auto mb-3" size={32} />
          <p className="text-slate-300">Supabase não configurado — cadastro indisponível.</p>
        </div>
      </div>
    );
  }

  if (sucesso) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center p-4">
        <div className="bg-slate-800 border border-emerald-500/30 rounded-2xl p-8 max-w-sm text-center">
          <Check className="text-emerald-400 mx-auto mb-3" size={32} />
          <p className="text-slate-200 font-bold mb-2">Conta criada!</p>
          <p className="text-slate-400 text-sm mb-5">
            Dependendo da configuração do Supabase, pode ser necessário confirmar o email antes de entrar.
          </p>
          <Link to="/login" className="text-emerald-400 hover:underline text-sm font-bold">Ir para o login</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-900 flex items-center justify-center p-4">
      <form onSubmit={criarConta} className="bg-slate-800 border border-slate-700 rounded-2xl p-8 w-full max-w-sm shadow-xl">
        <div className="flex items-center gap-2 mb-6 justify-center">
          <Zap className="text-emerald-400" size={28} />
          <span className="text-xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-blue-500">
            Criar Conta
          </span>
        </div>

        <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Email</label>
        <input
          type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
          className="w-full bg-slate-900 border border-slate-600 rounded-lg p-3 text-sm text-slate-100 mb-4"
        />

        <label className="block text-xs font-bold text-slate-400 uppercase mb-1">Senha (mínimo 6 caracteres)</label>
        <input
          type="password" required minLength={6} value={senha} onChange={(e) => setSenha(e.target.value)}
          className="w-full bg-slate-900 border border-slate-600 rounded-lg p-3 text-sm text-slate-100 mb-4"
        />

        {erro && <p className="text-red-400 text-xs mb-4">{erro}</p>}

        <button
          type="submit" disabled={carregando}
          className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold py-3 rounded-lg flex items-center justify-center gap-2 transition-colors"
        >
          <UserPlus size={18} /> {carregando ? 'Criando...' : 'Criar conta'}
        </button>

        <p className="text-center text-xs text-slate-500 mt-5">
          Já tem conta? <Link to="/login" className="text-emerald-400 hover:underline">Entrar</Link>
        </p>
      </form>
    </div>
  );
}
