// src/AuthContext.jsx
// Guarda a sessão de login atual e deixa qualquer página saber "quem está logado".
import React, { createContext, useContext, useEffect, useState } from 'react';
import { supabase, supabaseAtivo } from './supabaseClient';

const AuthContext = createContext({ session: null, carregando: true });

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null);
  const [carregando, setCarregando] = useState(true);

  useEffect(() => {
    if (!supabaseAtivo) { setCarregando(false); return; }

    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setCarregando(false);
    });

    const { data: listener } = supabase.auth.onAuthStateChange((_event, novaSessao) => {
      setSession(novaSessao);
    });

    return () => listener.subscription.unsubscribe();
  }, []);

  return (
    <AuthContext.Provider value={{ session, carregando }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
