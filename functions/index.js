import "./polyfill.js";
import { onRequest } from "firebase-functions/v2/https";
import express from "express";

// Importa todos os handlers das APIs existentes
import backtestBettingHandler from "./api/backtest-betting.js";
import cornersModelHandler from "./api/corners-model.js";
import fixturesHandler from "./api/fixtures.js";
import leaguesSearchHandler from "./api/leagues-search.js";
import matchOddsHandler from "./api/match-odds.js";
import modelMaintenanceHandler from "./api/model-maintenance.js";
import modelStatsHandler from "./api/model-stats.js";
import ocrHandler from "./api/ocr.js";
import syncClubeloHandler from "./api/sync-clubelo.js";
import syncMatchStatsHandler from "./api/sync-match-stats.js";
import syncMatchesHandler from "./api/sync-matches.js";
import teamStatsHandler from "./api/team-stats.js";

const app = express();

// Aumenta o limite de tamanho para requisições JSON (ex: upload de imagens no OCR)
app.use(express.json({ limit: "50mb" }));
app.use(express.urlencoded({ extended: true, limit: "50mb" }));

// Cria um router Express para concentrar os endpoints da API
const router = express.Router();

router.all("/backtest-betting", backtestBettingHandler);
router.all("/corners-model", cornersModelHandler);
router.all("/fixtures", fixturesHandler);
router.all("/leagues-search", leaguesSearchHandler);
router.all("/match-odds", matchOddsHandler);
router.all("/model-maintenance", modelMaintenanceHandler);
router.all("/model-stats", modelStatsHandler);
router.all("/ocr", ocrHandler);
router.all("/sync-clubelo", syncClubeloHandler);
router.all("/sync-match-stats", syncMatchStatsHandler);
router.all("/sync-matches", syncMatchesHandler);
router.all("/team-stats", teamStatsHandler);

// Mapeia tanto rotas com prefixo /api quanto sem prefixo, por segurança
app.use("/api", router);
app.use("/", router);

// Exporta a função HTTPS principal
export const api = onRequest({
  timeoutSeconds: 120, // Timeout padrão estendido para endpoints pesados
  memory: "512MiB",    // Memória padrão de 512MB
  cors: true           // Delega tratamento básico de CORS ao Firebase
}, app);
