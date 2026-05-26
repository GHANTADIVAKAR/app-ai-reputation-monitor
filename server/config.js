import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
export const rootDir = path.resolve(path.dirname(__filename), "..");
export const dataDir = path.join(rootDir, "data");

loadDotEnv();

export const config = {
  port: Number(process.env.PORT || 3000),
  scanIntervalHours: Number(process.env.SCAN_INTERVAL_HOURS || 6),
  openaiApiKey: process.env.OPENAI_API_KEY || "",
  openaiModel: process.env.OPENAI_MODEL || "gpt-4.1-mini",
  liveNewsEnabled: process.env.LIVE_NEWS_ENABLED !== "false",
  serpApiKey: process.env.SERPAPI_API_KEY || "",
  youtubeApiKey: process.env.YOUTUBE_API_KEY || "",
  redditEnabled: process.env.REDDIT_ENABLED === "true",
  redditUserAgent: process.env.REDDIT_USER_AGENT || "ReputationDashboard/0.1",
  xBearerToken: process.env.X_BEARER_TOKEN || "",
  instagramAccessToken: process.env.INSTAGRAM_ACCESS_TOKEN || "",
  socialListeningWebhookUrl: process.env.SOCIAL_LISTENING_WEBHOOK_URL || "",
  alertWebhookUrl: process.env.ALERT_WEBHOOK_URL || "",
  defaultQueries: (process.env.DEFAULT_QUERIES || "latest news,interview,review,controversy,public reaction")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
};

function loadDotEnv() {
  const envPath = path.join(rootDir, ".env");
  if (!fs.existsSync(envPath)) return;

  const lines = fs.readFileSync(envPath, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const index = trimmed.indexOf("=");
    if (index === -1) continue;
    const key = trimmed.slice(0, index).trim();
    const value = trimmed.slice(index + 1).trim();
    if (!(key in process.env)) process.env[key] = value;
  }
}
