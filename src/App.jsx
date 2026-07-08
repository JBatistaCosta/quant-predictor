// src/App.jsx
// Agora só orquestra as rotas — o conteúdo de cada página mora em src/pages/.
import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import Layout from './components/Layout';

import Login from './pages/Login';
import Cadastro from './pages/Cadastro';
import EventosLista from './pages/EventosLista';
import EventoNovo from './pages/EventoNovo';
import AnaliseEvento from './pages/AnaliseEvento';
import Times from './pages/Times';
import Ligas from './pages/Ligas';

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/cadastro" element={<Cadastro />} />

          <Route path="/eventos" element={
            <ProtectedRoute><Layout><EventosLista /></Layout></ProtectedRoute>
          } />
          <Route path="/eventos/novo" element={
            <ProtectedRoute><Layout><EventoNovo /></Layout></ProtectedRoute>
          } />
          <Route path="/analise" element={
            <ProtectedRoute><Layout><AnaliseEvento /></Layout></ProtectedRoute>
          } />
          <Route path="/times" element={
            <ProtectedRoute><Layout><Times /></Layout></ProtectedRoute>
          } />
          <Route path="/ligas" element={
            <ProtectedRoute><Layout><Ligas /></Layout></ProtectedRoute>
          } />

          <Route path="*" element={<Navigate to="/analise" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
