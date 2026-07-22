// api/_lib/cors.js
// Compartilhado por todos os endpoints em api/*.js (Vercel ignora arquivos
// dentro de api/_lib/ como rota — mesma convenção já usada por negbin.js).
//
// Necessário desde a migração do front-end pro Firebase Hosting: front e
// back agora vivem em domínios diferentes (front no Firebase, funções aqui
// continuam no Vercel), então toda resposta precisa de CORS ou o navegador
// bloqueia — inclusive o preflight (OPTIONS) que o browser manda sozinho
// antes de POSTs com corpo JSON ou header Authorization.
const ALLOWED_ORIGINS = [
  'https://quant-79e8b.web.app',
  'https://quant-79e8b.firebaseapp.com',
  'https://quant-predictor.vercel.app',
  'http://localhost:5173',
  ...(process.env.CORS_EXTRA_ORIGINS || '').split(',').map((o) => o.trim()).filter(Boolean),
];

// Chamar como primeira linha de cada handler. Retorna true quando já
// respondeu ao preflight (OPTIONS) — o handler deve dar `return` nesse caso.
export function applyCors(req, res) {
  const origin = req.headers.origin;
  if (origin && ALLOWED_ORIGINS.includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  }
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
  if (req.method === 'OPTIONS') {
    res.status(204).end();
    return true;
  }
  return false;
}
