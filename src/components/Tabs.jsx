// src/components/Tabs.jsx
// Menu de abas reutilizável -- usado em /configuracoes (consolida Cadastro +
// importações + cobertura) e na página consolidada de Modelos. Só a aba
// ativa fica montada (troca de aba refaz o fetch de dados de cada uma, o
// que é aceitável pra páginas administrativas como essas).
import React, { useState } from 'react';

export default function Tabs({ tabs, defaultTab }) {
  const [ativa, setAtiva] = useState(defaultTab || tabs[0]?.id);
  const tabAtiva = tabs.find((t) => t.id === ativa) || tabs[0];

  return (
    <div>
      <div className="flex flex-wrap gap-1 border-b border-slate-700 mb-4 -mx-1 px-1">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setAtiva(t.id)}
            className={`flex items-center gap-1.5 px-3 py-2 text-sm font-bold rounded-t-lg transition-colors whitespace-nowrap ${
              ativa === t.id
                ? 'bg-slate-800 text-emerald-400 border-b-2 border-emerald-400'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50 border-b-2 border-transparent'
            }`}
          >
            {t.icone && <t.icone size={15} />} {t.label}
          </button>
        ))}
      </div>
      <div>{tabAtiva?.conteudo}</div>
    </div>
  );
}
