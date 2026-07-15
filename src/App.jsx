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

            <Route path="*" element={<Navigate to="/analise" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </AuthProvider>
  );
}
