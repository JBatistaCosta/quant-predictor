// src/App.jsx
// Orquestra as rotas — cada página só é baixada quando o usuário navega até ela
// (code splitting via React.lazy), em vez de tudo ir junto no primeiro carregamento.
import React, { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import Layout from './components/Layout';

const Login = lazy(() => import('./pages/Login'));
const Cadastro = lazy(() => import('./pages/Cadastro'));
const EventosLista = lazy(() => import('./pages/EventosLista'));
const EventoNovo = lazy(() => import('./pages/EventoNovo'));
const ImportarJogos = lazy(() => import('./pages/ImportarJogos'));
const AnaliseEvento = lazy(() => import('./pages/AnaliseEvento'));
const Times = lazy(() => import('./pages/Times'));
const TimeDetalhe = lazy(() => import('./pages/TimeDetalhe'));
const Ligas = lazy(() => import('./pages/Ligas'));
const LigaDetalhe = lazy(() => import('./pages/LigaDetalhe'));
const ModelosStats = lazy(() => import('./pages/ModelosStats'));
const AnaliseHistorica = lazy(() => import('./pages/AnaliseHistorica'));
const AnaliseEstatisticaJogo = lazy(() => import('./pages/AnaliseEstatisticaJogo'));
const RatingClubes = lazy(() => import('./pages/RatingClubes'));
const Jogadores = lazy(() => import('./pages/Jogadores'));
const JogadorDetalhe = lazy(() => import('./pages/JogadorDetalhe'));
const ModelBenchmarking = lazy(() => import('./pages/ModelBenchmarking'));
const SimulacaoCarteira = lazy(() => import('./pages/SimulacaoCarteira'));
const RodadaPrevisoes = lazy(() => import('./pages/RodadaPrevisoes'));
const TreinoCustom = lazy(() => import('./pages/TreinoCustom'));
const ModeloV9 = lazy(() => import('./pages/ModeloV9'));
const ExploracaoDados = lazy(() => import('./pages/ExploracaoDados'));
const Configuracoes = lazy(() => import('./pages/Configuracoes'));

function CarregandoPagina() {
  return (
    <div className="min-h-screen bg-slate-900 flex items-center justify-center text-slate-500 text-sm">
      Carregando...
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Suspense fallback={<CarregandoPagina />}>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/cadastro" element={<Cadastro />} />

            <Route path="/eventos" element={
              <ProtectedRoute><Layout><EventosLista /></Layout></ProtectedRoute>
            } />
            <Route path="/eventos/novo" element={
              <ProtectedRoute><Layout><EventoNovo /></Layout></ProtectedRoute>
            } />
            <Route path="/importar" element={
              <ProtectedRoute><Layout><ImportarJogos /></Layout></ProtectedRoute>
            } />
            <Route path="/analise" element={
              <ProtectedRoute><Layout><AnaliseEvento /></Layout></ProtectedRoute>
            } />
            <Route path="/times" element={
              <ProtectedRoute><Layout><Times /></Layout></ProtectedRoute>
            } />
            <Route path="/times/:id" element={
              <ProtectedRoute><Layout><TimeDetalhe /></Layout></ProtectedRoute>
            } />
            <Route path="/ligas" element={
              <ProtectedRoute><Layout><Ligas /></Layout></ProtectedRoute>
            } />
            <Route path="/ligas/:id" element={
              <ProtectedRoute><Layout><LigaDetalhe /></Layout></ProtectedRoute>
            } />
            <Route path="/modelos" element={
              <ProtectedRoute><Layout><ModelosStats /></Layout></ProtectedRoute>
            } />
            <Route path="/historico/:matchId" element={
              <ProtectedRoute><Layout><AnaliseHistorica /></Layout></ProtectedRoute>
            } />
            <Route path="/estatisticas/:matchId" element={
              <ProtectedRoute><Layout><AnaliseEstatisticaJogo /></Layout></ProtectedRoute>
            } />
            <Route path="/ratings" element={
              <ProtectedRoute><Layout><RatingClubes /></Layout></ProtectedRoute>
            } />
            <Route path="/jogadores" element={
              <ProtectedRoute><Layout><Jogadores /></Layout></ProtectedRoute>
            } />
            <Route path="/jogadores/:id" element={
              <ProtectedRoute><Layout><JogadorDetalhe /></Layout></ProtectedRoute>
            } />
            <Route path="/model-benchmarking" element={
              <ProtectedRoute><Layout><ModelBenchmarking /></Layout></ProtectedRoute>
            } />
            <Route path="/simulacao-carteira" element={
              <ProtectedRoute><Layout><SimulacaoCarteira /></Layout></ProtectedRoute>
            } />
            <Route path="/rodada-atual" element={
              <ProtectedRoute><Layout><RodadaPrevisoes /></Layout></ProtectedRoute>
            } />
            <Route path="/treino-custom" element={
              <ProtectedRoute><Layout><TreinoCustom /></Layout></ProtectedRoute>
            } />
            <Route path="/modelo-v9" element={
              <ProtectedRoute><Layout><ModeloV9 /></Layout></ProtectedRoute>
            } />
            <Route path="/explorar-dados" element={
              <ProtectedRoute><Layout><ExploracaoDados /></Layout></ProtectedRoute>
            } />
            <Route path="/configuracoes" element={
              <ProtectedRoute><Layout><Configuracoes /></Layout></ProtectedRoute>
            } />

            <Route path="*" element={<Navigate to="/analise" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </AuthProvider>
  );
}
