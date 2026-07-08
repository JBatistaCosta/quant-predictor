// src/components/ProtectedRoute.jsx
import React from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import { supabaseAtivo } from '../supabaseClient';

export default function ProtectedRoute({ children }) {
  const { session, carregando } = useAuth();

  // Se o Supabase nem está configurado, não dá pra exigir login — deixa passar
  // (comportamento antigo, sem autenticação) em vez de travar o app inteiro.
  if (!supabaseAtivo) return children;

  if (carregando) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center text-slate-400">
        Carregando...
      </div>
    );
  }

  if (!session) {
    return <Navigate to="/login" replace />;
  }

  return children;
}
