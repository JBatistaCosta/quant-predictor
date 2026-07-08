// src/supabaseClient.js
// Conecta o app ao Supabase, usando a chave PÚBLICA (publishable/anon) — segura
// pra ficar exposta no navegador, já que o controle de acesso vem das políticas
// (RLS) configuradas nas tabelas, não do segredo da chave.
//
// As duas variáveis abaixo precisam existir no Vercel (Settings > Environment
// Variables) E também localmente, num arquivo .env na raiz do projeto:
//   VITE_SUPABASE_URL=https://SEU-PROJETO.supabase.co
//   VITE_SUPABASE_KEY=sb_publishable_xxxxxxxx
//
// Se essas variáveis não estiverem configuradas, o app continua funcionando
// normalmente — só não salva/recupera nada do banco (cai de volta no
// comportamento manual de colar o JSON toda vez).

import { createClient } from '@supabase/supabase-js';

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseKey = import.meta.env.VITE_SUPABASE_KEY;

export const supabase = (supabaseUrl && supabaseKey) ? createClient(supabaseUrl, supabaseKey) : null;
export const supabaseAtivo = !!supabase;
