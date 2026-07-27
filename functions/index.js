import "./polyfill.js";
import { onRequest } from "firebase-functions/v2/https";
import { onSchedule } from "firebase-functions/v2/scheduler";
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

// Função auxiliar para simular respostas HTTP durante a execução dos Crons
function createMockResponse() {
  const res = {
    statusCode: 200,
    headers: {},
    setHeader(name, value) {
      this.headers[name] = value;
      return this;
    },
    getHeader(name) {
      return this.headers[name];
    },
    status(code) {
      this.statusCode = code;
      return this;
    },
    json(data) {
      this.body = data;
      console.log(`[Response JSON Status ${this.statusCode}]:`, JSON.stringify(data).substring(0, 500));
      return this;
    },
    send(data) {
      this.body = data;
      console.log(`[Response Send Status ${this.statusCode}]:`, typeof data === "string" ? data.substring(0, 500) : "[Data]");
      return this;
    },
    end() {
      return this;
    }
  };
  return res;
}

// -------------------------------------------------------------------------
// Tarefas Agendadas (Crons) Migradas do Vercel
// -------------------------------------------------------------------------

// Cron 1: Sincronização de Partidas (Diário às 06h00 Horário de Brasília)
export const cronSyncMatches = onSchedule({
  schedule: "0 6 * * *",
  timeZone: "America/Sao_Paulo",
  memory: "512MiB",
  timeoutSeconds: 120
}, async (event) => {
  console.log("Iniciando cronSyncMatches...");
  const req = {
    method: "GET",
    query: {},
    headers: {
      authorization: `Bearer ${process.env.CRON_SECRET || ""}`
    },
    body: {}
  };
  const res = createMockResponse();
  await syncMatchesHandler(req, res);
  console.log("Finalizado cronSyncMatches com status:", res.statusCode);
});

// Cron 2: Manutenção do Modelo - Odds de Todas as Ligas (A cada 3 dias às 08h00 Horário de Brasília)
export const cronModelMaintenanceOddsTodas = onSchedule({
  schedule: "0 8 */3 * *",
  timeZone: "America/Sao_Paulo",
  memory: "1GiB", // 1GB para cálculos estatísticos mais pesados
  timeoutSeconds: 300 // Limite de 5 minutos
}, async (event) => {
  console.log("Iniciando cronModelMaintenanceOddsTodas...");
  const req = {
    method: "GET",
    query: {
      tarefa: "odds-todas"
    },
    headers: {
      authorization: `Bearer ${process.env.CRON_SECRET || ""}`
    },
    body: {}
  };
  const res = createMockResponse();
  await modelMaintenanceHandler(req, res);
  console.log("Finalizado cronModelMaintenanceOddsTodas com status:", res.statusCode);
});

// Cron 3: Manutenção do Modelo - Elo Rotativo (Diário às 07h00 Horário de Brasília)
export const cronModelMaintenanceEloRotativo = onSchedule({
  schedule: "0 7 * * *",
  timeZone: "America/Sao_Paulo",
  memory: "1GiB",
  timeoutSeconds: 300
}, async (event) => {
  console.log("Iniciando cronModelMaintenanceEloRotativo...");
  const req = {
    method: "GET",
    query: {
      tarefa: "elo-rotativo"
    },
    headers: {
      authorization: `Bearer ${process.env.CRON_SECRET || ""}`
    },
    body: {}
  };
  const res = createMockResponse();
  await modelMaintenanceHandler(req, res);
  console.log("Finalizado cronModelMaintenanceEloRotativo com status:", res.statusCode);
});
