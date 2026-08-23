// server/app.js
// Servidor Express único, usado no deploy AWS (Elastic Beanstalk) — serve o
// front-end compilado (dist/) e as mesmas rotas de api/*.js NUM SÓ DOMÍNIO
// (ver README.md, seção "Publicar na AWS"). Isso é o que dá "navegação
// fluida": sem hop entre domínios (como acontece no par Firebase+Vercel,
// que precisa de CORS porque front e API vivem em origens diferentes).
//
// Reaproveita os handlers originais de api/*.js DIRETO (mesma assinatura
// (req, res) do runtime Node do Vercel, compatível com Express) em vez de
// duplicar o código, como aconteceu em functions/api/ (cópia manual pro
// Firebase que já divergiu do original em 4 dos 12 arquivos).
import path from 'path';
import { fileURLToPath } from 'url';
import express from 'express';

import backtestBettingHandler from '../api/backtest-betting.js';
import cornersModelHandler from '../api/corners-model.js';
import fixturesHandler from '../api/fixtures.js';
import leaguesSearchHandler from '../api/leagues-search.js';
import matchOddsHandler from '../api/match-odds.js';
import modelMaintenanceHandler from '../api/model-maintenance.js';
import modelStatsHandler from '../api/model-stats.js';
import ocrHandler from '../api/ocr.js';
import syncClubeloHandler from '../api/sync-clubelo.js';
import syncMatchStatsHandler from '../api/sync-match-stats.js';
import syncMatchesHandler from '../api/sync-matches.js';
import teamStatsHandler from '../api/team-stats.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.join(__dirname, '..', 'dist');

const app = express();

// Mesmo limite usado em functions/index.js (upload de imagens no OCR)
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ extended: true, limit: '50mb' }));

const router = express.Router();
router.all('/backtest-betting', backtestBettingHandler);
router.all('/corners-model', cornersModelHandler);
router.all('/fixtures', fixturesHandler);
router.all('/leagues-search', leaguesSearchHandler);
router.all('/match-odds', matchOddsHandler);
router.all('/model-maintenance', modelMaintenanceHandler);
router.all('/model-stats', modelStatsHandler);
router.all('/ocr', ocrHandler);
router.all('/sync-clubelo', syncClubeloHandler);
router.all('/sync-match-stats', syncMatchStatsHandler);
router.all('/sync-matches', syncMatchesHandler);
router.all('/team-stats', teamStatsHandler);
app.use('/api', router);

app.use(express.static(distDir));

// SPA: qualquer rota que não seja /api/* cai no index.html (React Router
// cuida da navegação no cliente a partir daí)
app.get(/^(?!\/api\/).*/, (req, res) => {
  res.sendFile(path.join(distDir, 'index.html'));
});

export default app;
