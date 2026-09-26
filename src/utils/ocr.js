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

// Aceita 1+ imagens na MESMA chamada de IA (não N chamadas separadas) --
// pedido do usuário: um print de tabela longa às vezes vem cortado/quebrado
// em 2+ capturas (ex.: metade dos jogadores em cada uma), e mandar as duas
// juntas deixa a própria IA reconciliar num JSON só, em vez do cliente ter
// que casar/mesclar dois JSONs parciais (que podem ter jogadores repetidos
// entre as imagens, sem forma confiável de saber isso do lado de fora).
// Claude e Gemini aceitam múltiplos blocos de imagem numa mensagem só —
// `api/ocr.js` já manda todas antes do prompt de texto.
export async function extractJsonFromImages(files, prompt) {
  const arquivos = Array.from(files || []).filter(Boolean);
  if (arquivos.length === 0) throw new Error('Nenhuma imagem selecionada.');
  const images = await Promise.all(
    arquivos.map(async (file) => ({ data: await normalizeImageToJpeg(file), mediaType: 'image/jpeg' }))
  );

  let response;
  try {
    response = await fetch(apiUrl('/api/ocr'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ images, prompt }),
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

// Compatibilidade: chamadores que ainda mandam 1 arquivo só continuam
// funcionando sem mudança (delega pra extractJsonFromImages com array de 1).
export async function extractJsonFromImage(file, prompt) {
  return extractJsonFromImages([file], prompt);
}
