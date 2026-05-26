import { analyzeMention } from "./analyzer.js";
import { collectMentions } from "./collectors.js";
import { findTargetImages } from "./images.js";
import { ensureSearchMap } from "./keywords.js";
import { sendAlert } from "./notifier.js";
import { newId, readDb, writeDb } from "../store.js";

export async function runScan(targetId = null) {
  const db = await readDb();
  const targets = targetId ? db.targets.filter((target) => target.id === targetId) : db.targets;
  const completed = [];

  for (const target of targets) {
    target.searchMap = ensureSearchMap(target);
    target.updatedAt = new Date().toISOString();

    const scan = {
      id: newId("scan"),
      targetId: target.id,
      startedAt: new Date().toISOString(),
      completedAt: null,
      mentionCount: 0,
      positive: 0,
      negative: 0,
      neutral: 0,
      mixed: 0,
      riskScore: 0
    };

    const collection = await collectMentions(target);
    const mentions = collection.mentions;
    for (const mention of mentions) {
      const analysis = await analyzeMention(mention, target);
      const enriched = { ...mention, analysis, scanId: scan.id, dataMode: collection.mode };
      const existingIndex = db.mentions.findIndex((item) => item.url === enriched.url && item.targetId === enriched.targetId);
      if (existingIndex >= 0) db.mentions[existingIndex] = { ...db.mentions[existingIndex], ...enriched };
      else db.mentions.unshift(enriched);
    }

    const scanMentions = db.mentions.filter((mention) => mention.scanId === scan.id);
    scan.mentionCount = scanMentions.length;
    for (const mention of scanMentions) {
      const sentiment = mention.analysis?.sentiment || "neutral";
      if (sentiment in scan) scan[sentiment] += 1;
      if (mention.analysis?.riskLevel === "high") scan.riskScore += 30;
      if (mention.analysis?.riskLevel === "medium") scan.riskScore += 15;
      if (sentiment === "negative") scan.riskScore += 10;
    }
    scan.riskScore = Math.min(100, scan.riskScore);
    scan.dataMode = collection.mode;
    scan.sourceStatus = collection.sourceStatus;
    scan.completedAt = new Date().toISOString();
    db.scans.unshift(scan);

    if (scan.riskScore >= 50) {
      const alert = {
        id: newId("alert"),
        targetId: target.id,
        scanId: scan.id,
        severity: scan.riskScore >= 75 ? "high" : "medium",
        title: `${target.name} has elevated negative sentiment`,
        body: `Latest scan found ${scan.negative} negative mentions with a risk score of ${scan.riskScore}.`,
        createdAt: new Date().toISOString(),
        read: false
      };
      db.alerts.unshift(alert);
      sendAlert(alert, target).catch((error) => console.warn("Alert webhook failed:", error.message));
    }

    completed.push(scan);
  }

  await writeDb(db);
  return completed;
}

export async function buildDashboard(targetId, db) {
  const target = db.targets.find((item) => item.id === targetId) || db.targets[0];
  if (!target) return null;

  const mentions = db.mentions
    .filter((item) => item.targetId === target.id)
    .sort((a, b) => new Date(b.discoveredAt) - new Date(a.discoveredAt));
  const scans = db.scans
    .filter((item) => item.targetId === target.id)
    .sort((a, b) => new Date(b.startedAt) - new Date(a.startedAt));
  const alerts = db.alerts.filter((item) => item.targetId === target.id);
  const latestScan = scans[0] || null;
  const currentMentions = latestScan
    ? mentions.filter((mention) => mention.scanId === latestScan.id)
    : mentions;

  const totals = currentMentions.reduce(
    (acc, mention) => {
      const sentiment = mention.analysis?.sentiment || "neutral";
      acc.total += 1;
      acc[sentiment] = (acc[sentiment] || 0) + 1;
      return acc;
    },
    { total: 0, positive: 0, negative: 0, neutral: 0, mixed: 0 }
  );

  const topNegative = topBySentiment(currentMentions, "negative");
  const topPositive = topBySentiment(currentMentions, "positive");
    const topNeutral = topBySentiment(currentMentions, "neutral");

  return {
    target,
    searchMap: ensureSearchMap(target),
    targetImages: await findTargetImages(target),
    dataMode: scans[0]?.dataMode || mentions[0]?.dataMode || "unknown",
    sourceStatus: scans[0]?.sourceStatus || [],
    lastScanAt: scans[0]?.completedAt || null,
    totals,
    scans: scans.slice(0, 12),
    mentions: currentMentions.slice(0, 50),
    topNegative,
    topPositive,
    topNeutral,
    alerts: alerts.slice(0, 10)
  };
}

function topBySentiment(mentions, sentiment) {
  return mentions
    .filter((mention) => mention.analysis?.sentiment === sentiment)
    .sort((a, b) => {
      const engagementDelta = (b.engagement || 0) - (a.engagement || 0);
      if (engagementDelta !== 0) return engagementDelta;
      return new Date(b.publishedAt || b.discoveredAt) - new Date(a.publishedAt || a.discoveredAt);
    })
    .slice(0, 20);
}
