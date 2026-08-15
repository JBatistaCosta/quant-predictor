// src/pages/ModelosAvancados.jsx
// Consolida em abas 4 itens que eram entradas próprias na barra de
// navegação (pedido do usuário, "tal qual um menu de tarefas") -- os
// componentes em si não mudaram, só passaram a ser renderizados aqui em
// vez de terem entrada própria no menu. As rotas antigas (/model-
// benchmarking, /simulacao-carteira, /treino-custom, /modelo-v9) continuam
// existindo, sem uso ativo pela navegação principal.
import React from 'react';
import { Zap, Wallet, FlaskConical, Layers } from 'lucide-react';
import Tabs from '../components/Tabs';
import ModelBenchmarking from './ModelBenchmarking';
import SimulacaoCarteira from './SimulacaoCarteira';
import TreinoCustom from './TreinoCustom';
import ModeloV9 from './ModeloV9';

const ABAS_MODELOS = [
  { id: 'benchmarking', label: 'Model Benchmarking', icone: Zap, conteudo: <ModelBenchmarking /> },
  { id: 'simulacao-carteira', label: 'Simulação de Carteira', icone: Wallet, conteudo: <SimulacaoCarteira /> },
  { id: 'treino-custom', label: 'Treino Customizado', icone: FlaskConical, conteudo: <TreinoCustom /> },
  { id: 'modelo-v9', label: 'Modelo v9', icone: Layers, conteudo: <ModeloV9 /> },
];

export default function ModelosAvancados() {
  return (
    <div className="max-w-6xl mx-auto space-y-4">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6">
        <h1 className="text-2xl font-extrabold flex items-center gap-3 text-slate-100">
          <Zap className="text-emerald-400" size={28} /> Modelos Avançados
        </h1>
        <p className="text-slate-400 mt-1 text-sm">Benchmarking, simulação de carteira, treino customizado e o modelo v9.</p>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-4 md:p-6">
        <Tabs tabs={ABAS_MODELOS} />
      </div>
    </div>
  );
}
