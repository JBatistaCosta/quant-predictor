// src/utils/ocr.js
// Núcleo compartilhado de OCR via IA (imagem -> base64 normalizado -> /api/ocr
// -> JSON): mesma lógica usada em AnaliseEvento.jsx, extraída pra util pra
// AnaliseEstatisticaJogo.jsx poder reaproveitar sem duplicar o parsing/erro.
// Não muda nenhum comportamento — é a função original, só movida.

import { apiUrl } from './apiUrl';

export const normalizeImageToJpeg = (file, maxWidth = 1400, quality = 0.85) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => {
    const img = new window.Image();
    img.onload = () => {
      let { width, height } = img;
      if (width > maxWidth) {
        height = Math.round(height * (maxWidth / width));
        width = maxWidth;
      }
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0, width, height);
      try {
        const dataUrl = canvas.toDataURL('image/jpeg', quality);
        resolve(dataUrl.split(',')[1]);
      } catch (e) {
        reject(new Error('Não foi possível converter a imagem (canvas bloqueado pelo navegador).'));
      }
    };
    img.onerror = () => reject(new Error('Formato de imagem não suportado pelo navegador. Tente exportar como JPG ou PNG antes de enviar.'));
    img.src = reader.result;
  };
  reader.onerror = () => reject(new Error('Falha ao ler o ficheiro do celular/computador.'));
  reader.readAsDataURL(file);
});

export async function extractJsonFromImage(file, prompt) {
  const base64Data = await normalizeImageToJpeg(file);
  const mediaType = 'image/jpeg';

  let response;
  try {
    response = await fetch(apiUrl('/api/ocr'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: base64Data, mediaType, prompt }),
    });
  } catch (networkErr) {
    throw new Error('Falha de rede ao contactar o servidor. Verifique a conexão e tente novamente.');
  }

  let data;
  try {
    data = await response.json();
  } catch (e) {
    throw new Error(`Resposta inválida da API (status ${response.status}).`);
  }

  if (!response.ok || data.error) {
    const msg = data?.error?.message || `status HTTP ${response.status}`;
    throw new Error(`Erro da API: ${msg}`);
  }

  const rawText = (data.content || [])
    .filter(item => item.type === 'text')
    .map(item => item.text)
    .join('\n');

  if (!rawText.trim()) {
    throw new Error('A IA não devolveu texto. Tente novamente ou use outra imagem.');
  }

  const cleanText = rawText.replace(/```json|```/g, '').trim();

  try {
    return JSON.parse(cleanText);
  } catch (e) {
    throw new Error(`A IA não devolveu um JSON válido. Início da resposta: "${cleanText.slice(0, 120)}..."`);
  }
}
