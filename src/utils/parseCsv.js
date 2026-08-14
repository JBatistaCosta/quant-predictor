// src/utils/parseCsv.js
// Parser CSV mínimo (RFC4180: campos entre aspas podem conter vírgula/quebra
// de linha, aspas duplicadas dentro de um campo viram uma aspa literal) --
// suficiente pro formato exportado pela Footiqo, sem puxar uma lib externa
// só pra isso. Devolve um array de objetos, chave = cabeçalho da 1ª linha.
export function parseCsv(texto) {
  const linhas = [];
  let campo = '', linhaAtual = [], dentroDeAspas = false;

  for (let i = 0; i < texto.length; i++) {
    const c = texto[i];
    if (dentroDeAspas) {
      if (c === '"') {
        if (texto[i + 1] === '"') { campo += '"'; i++; }
        else dentroDeAspas = false;
      } else {
        campo += c;
      }
      continue;
    }
    if (c === '"') { dentroDeAspas = true; continue; }
    if (c === ',') { linhaAtual.push(campo); campo = ''; continue; }
    if (c === '\r') continue;
    if (c === '\n') {
      linhaAtual.push(campo); campo = '';
      linhas.push(linhaAtual); linhaAtual = [];
      continue;
    }
    campo += c;
  }
  if (campo.length > 0 || linhaAtual.length > 0) { linhaAtual.push(campo); linhas.push(linhaAtual); }

  const linhasNaoVazias = linhas.filter((l) => !(l.length === 1 && l[0] === ''));
  if (linhasNaoVazias.length === 0) return [];

  const cabecalho = linhasNaoVazias[0];
  return linhasNaoVazias.slice(1).map((linha) => {
    const obj = {};
    cabecalho.forEach((chave, idx) => { obj[chave] = linha[idx] ?? ''; });
    return obj;
  });
}
