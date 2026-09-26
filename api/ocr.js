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

async function chamarClaude(apiKey, images, prompt) {
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
            // Todas as imagens ANTES do texto, na mesma mensagem -- deixa o
            // modelo reconciliar um print de tabela quebrado em 2+ capturas
            // (ou uma leitura ambígua numa imagem, confirmada pela outra)
            // num JSON só, em vez de mesclar respostas parciais no cliente.
            ...images.map((img) => ({ type: 'image', source: { type: 'base64', media_type: img.mediaType, data: img.data } })),
            { type: 'text', text: prompt }
          ]
        }
      ]
    })
  });
  const data = await response.json();
  return { ok: response.ok, status: response.status, data };
}

async function chamarGemini(apiKey, images, prompt) {
  const modelo = 'gemini-2.5-flash'; // rápido e barato, equivalente ao claude-haiku aqui usado
  const response = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${modelo}:generateContent`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-goog-api-key': apiKey },
      body: JSON.stringify({
        contents: [{
          parts: [
            ...images.map((img) => ({ inline_data: { mime_type: img.mediaType, data: img.data } })),
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

  // Aceita `images: [{data, mediaType}]` (1+ imagens, mesma chamada de IA --
  // ver src/utils/ocr.js) e, por compatibilidade, o formato antigo de uma
  // imagem só (`image`/`mediaType`), normalizado pro mesmo array aqui.
  const { image, mediaType, images: imagensBrutas, prompt } = req.body || {};
  const images = Array.isArray(imagensBrutas) && imagensBrutas.length > 0
    ? imagensBrutas.map((img) => ({ data: img.data, mediaType: img.mediaType || 'image/jpeg' }))
    : image
      ? [{ data: image, mediaType: mediaType || 'image/jpeg' }]
      : [];
  if (images.length === 0 || !prompt) {
    return res.status(400).json({ error: { message: 'Imagem ou prompt ausente na requisição.' } });
  }

  const geminiKey = process.env.GEMINI_API_KEY;
  const anthropicKey = process.env.ANTHROPIC_API_KEY;

  if (!geminiKey && !anthropicKey) {
    return res.status(500).json({
      error: { message: 'Nenhuma chave de IA configurada. Configure GEMINI_API_KEY ou ANTHROPIC_API_KEY nas variáveis de ambiente do Vercel.' }
    });
  }

  try {
    const resultado = geminiKey
      ? await chamarGemini(geminiKey, images, prompt)
      : await chamarClaude(anthropicKey, images, prompt);

    return res.status(resultado.ok ? 200 : resultado.status).json(resultado.data);
  } catch (err) {
    return res.status(500).json({ error: { message: err.message || 'Erro desconhecido no servidor.' } });
  }
}
