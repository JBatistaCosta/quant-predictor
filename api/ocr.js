// api/ocr.js
// Roda no SERVIDOR do Vercel — as chaves de API ficam escondidas e seguras aqui.
//
// Aceita os DOIS provedores de IA com visão computacional, escolhendo pela
// variável de ambiente que estiver configurada:
//   1º) GEMINI_API_KEY presente    -> usa o Gemini (Google)
//   2º) senão, ANTHROPIC_API_KEY   -> usa o Claude (comportamento original)
//   nenhuma das duas               -> erro claro explicando as duas opções
//
// O FRONT-END NÃO PRECISA SABER QUAL FOI USADO: a resposta sempre volta no
// mesmo formato (o formato nativo do Claude: {content: [{type:"text", text}]}),
// porque a resposta do Gemini é "traduzida" pra esse formato aqui dentro.

import { applyCors } from './_lib/cors.js';

async function chamarClaude(apiKey, image, mediaType, prompt) {
  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01'
    },
    body: JSON.stringify({
      model: 'claude-haiku-4-5-20251001', // rápido e barato, ótimo para extrair dados de imagens
      max_tokens: 1000,
      messages: [
        {
          role: 'user',
          content: [
            { type: 'image', source: { type: 'base64', media_type: mediaType, data: image } },
            { type: 'text', text: prompt }
          ]
        }
      ]
    })
  });
  const data = await response.json();
  return { ok: response.ok, status: response.status, data };
}

async function chamarGemini(apiKey, image, mediaType, prompt) {
  const modelo = 'gemini-2.5-flash'; // rápido e barato, equivalente ao claude-haiku aqui usado
  const response = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${modelo}:generateContent`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-goog-api-key': apiKey },
      body: JSON.stringify({
        contents: [{
          parts: [
            { inline_data: { mime_type: mediaType, data: image } },
            { text: prompt }
          ]
        }]
      })
    }
  );
  const raw = await response.json();

  if (!response.ok) {
    return { ok: false, status: response.status, data: { error: { message: raw?.error?.message || 'Erro desconhecido do Gemini.' } } };
  }

  const texto = raw?.candidates?.[0]?.content?.parts?.map(p => p.text).filter(Boolean).join('\n') || '';
  return { ok: true, status: 200, data: { content: [{ type: 'text', text: texto }] } };
}

export default async function handler(req, res) {
  if (applyCors(req, res)) return;
  if (req.method !== 'POST') {
    return res.status(405).json({ error: { message: 'Método não permitido.' } });
  }

  const { image, mediaType, prompt } = req.body || {};
  if (!image || !prompt) {
    return res.status(400).json({ error: { message: 'Imagem ou prompt ausente na requisição.' } });
  }
  const tipoDeImagem = mediaType || 'image/jpeg';

  const geminiKey = process.env.GEMINI_API_KEY;
  const anthropicKey = process.env.ANTHROPIC_API_KEY;

  if (!geminiKey && !anthropicKey) {
    return res.status(500).json({
      error: { message: 'Nenhuma chave de IA configurada. Configure GEMINI_API_KEY ou ANTHROPIC_API_KEY nas variáveis de ambiente do Vercel.' }
    });
  }

  try {
    const resultado = geminiKey
      ? await chamarGemini(geminiKey, image, tipoDeImagem, prompt)
      : await chamarClaude(anthropicKey, image, tipoDeImagem, prompt);

    return res.status(resultado.ok ? 200 : resultado.status).json(resultado.data);
  } catch (err) {
    return res.status(500).json({ error: { message: err.message || 'Erro desconhecido no servidor.' } });
  }
}
