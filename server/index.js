import http from "node:http";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { config, rootDir } from "./config.js";
import { createTarget, readDb, writeDb } from "./store.js";
import { buildDashboard, runScan } from "./services/scanner.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const publicDir = path.join(rootDir, "public");

let scanInProgress = false;

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);

    if (url.pathname.startsWith("/api/")) {
      await handleApi(req, res, url);
      return;
    }

    await serveStatic(req, res, url);
  } catch (error) {
    console.error(error);
    sendJson(res, 500, { error: "Internal server error", detail: error.message });
  }
});

server.listen(config.port, () => {
  console.log(`Reputation dashboard running on http://localhost:${config.port}`);
  scheduleScans();
});

async function handleApi(req, res, url) {
  if (req.method === "GET" && url.pathname === "/api/health") {
    sendJson(res, 200, { ok: true, time: new Date().toISOString() });
    return;
  }

  if (req.method === "GET" && url.pathname === "/api/targets") {
    const db = await readDb();
    sendJson(res, 200, db.targets);
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/targets") {
    const body = await readBody(req);
    if (!body.name || body.name.trim().length < 2) {
      sendJson(res, 400, { error: "Target name is required." });
      return;
    }
    const target = await createTarget(body);
    sendJson(res, 201, target);
    return;
  }

  if (req.method === "GET" && url.pathname === "/api/dashboard") {
    const db = await readDb();
    const dashboard = await buildDashboard(url.searchParams.get("targetId"), db);
    sendJson(res, dashboard ? 200 : 404, dashboard || { error: "No target found." });
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/scan") {
    if (scanInProgress) {
      sendJson(res, 409, { error: "A scan is already running." });
      return;
    }
    scanInProgress = true;
    try {
      const body = await readBody(req);
      const scans = await runScan(body.targetId || null);
      sendJson(res, 200, { scans });
    } finally {
      scanInProgress = false;
    }
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/alerts/read") {
    const body = await readBody(req);
    const db = await readDb();
    db.alerts = db.alerts.map((alert) => alert.id === body.alertId ? { ...alert, read: true } : alert);
    await writeDb(db);
    sendJson(res, 200, { ok: true });
    return;
  }

  sendJson(res, 404, { error: "Not found." });
}

async function serveStatic(req, res, url) {
  const safePath = url.pathname === "/" ? "/index.html" : url.pathname;
  const filePath = path.normalize(path.join(publicDir, safePath));
  if (!filePath.startsWith(publicDir)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  try {
    const content = await fs.readFile(filePath);
    res.writeHead(200, { "Content-Type": contentType(filePath) });
    res.end(content);
  } catch {
    const fallback = await fs.readFile(path.join(publicDir, "index.html"));
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(fallback);
  }
}

function scheduleScans() {
  const intervalMs = Math.max(1, config.scanIntervalHours) * 60 * 60 * 1000;
  setInterval(async () => {
    if (scanInProgress) return;
    scanInProgress = true;
    try {
      await runScan();
      console.log("Scheduled scan completed.");
    } catch (error) {
      console.error("Scheduled scan failed:", error);
    } finally {
      scanInProgress = false;
    }
  }, intervalMs);
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function sendJson(res, status, data) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(data));
}

function contentType(filePath) {
  if (filePath.endsWith(".html")) return "text/html; charset=utf-8";
  if (filePath.endsWith(".css")) return "text/css; charset=utf-8";
  if (filePath.endsWith(".js")) return "text/javascript; charset=utf-8";
  if (filePath.endsWith(".svg")) return "image/svg+xml";
  return "application/octet-stream";
}
