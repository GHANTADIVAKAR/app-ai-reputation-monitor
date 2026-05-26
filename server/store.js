import fs from "node:fs/promises";
import path from "node:path";
import { dataDir } from "./config.js";
import { buildSearchMap } from "./services/keywords.js";

const dbPath = path.join(dataDir, "db.json");

const defaultDb = {
  targets: [],
  scans: [],
  mentions: [],
  alerts: []
};

export async function readDb() {
  await fs.mkdir(dataDir, { recursive: true });
  try {
    const raw = await fs.readFile(dbPath, "utf8");
    return { ...defaultDb, ...JSON.parse(raw) };
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    await writeDb(defaultDb);
    return structuredClone(defaultDb);
  }
}

export async function writeDb(db) {
  await fs.mkdir(dataDir, { recursive: true });
  await fs.writeFile(dbPath, `${JSON.stringify(db, null, 2)}\n`);
}

export function newId(prefix) {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export async function createTarget(input) {
  const db = await readDb();
  const now = new Date().toISOString();
  const target = {
    id: newId("target"),
    name: String(input.name || "").trim(),
    type: input.type || "person",
    description: String(input.description || "").trim(),
    queries: Array.isArray(input.queries) ? input.queries.filter(Boolean) : [],
    searchMap: null,
    createdAt: now,
    updatedAt: now
  };
  target.searchMap = buildSearchMap(target);
  db.targets.unshift(target);
  await writeDb(db);
  return target;
}

export async function upsertMention(mention) {
  const db = await readDb();
  const existingIndex = db.mentions.findIndex((item) => item.url === mention.url && item.targetId === mention.targetId);
  if (existingIndex >= 0) {
    db.mentions[existingIndex] = { ...db.mentions[existingIndex], ...mention };
  } else {
    db.mentions.unshift(mention);
  }
  await writeDb(db);
  return mention;
}
