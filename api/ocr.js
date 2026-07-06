// api/ocr.js
// Esta função roda no SERVIDOR do Vercel, não no celular do usuário.
// Por isso a chave da API (ANTHROPIC_API_KEY) fica escondida e segura.
export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: { message: 'Método não permitido.' } });
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return res.status(500).json({
      error: { message: 'ANTHROPIC_API_KEY não configurada nas variáveis de ambiente do Vercel.' }
    });
  }

  const { image, mediaType, prompt } = req.body || {};
  if (!image || !prompt) {
    return res.status(400).json({ error: { message: 'Imagem ou prompt ausente na requisição.' } });
  }

  try {
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
              { type: 'image', source: { type: 'base64', media_type: mediaType || 'image/jpeg', data: image } },
              { type: 'text', text: prompt }
            ]
          }
        ]
      })
    });

    const data = await response.json();
    return res.status(response.ok ? 200 : response.status).json(data);
  } catch (err) {
    return res.status(500).json({ error: { message: err.message || 'Erro desconhecido no servidor.' } });
  }
}
