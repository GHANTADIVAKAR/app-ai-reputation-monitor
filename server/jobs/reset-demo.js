import { writeDb } from "../store.js";
import { runScan } from "../services/scanner.js";
import { buildSearchMap } from "../services/keywords.js";

const target = {
  id: "target_demo_vijay",
  name: "Vijay Deverakonda",
  type: "celebrity",
  description: "Telugu film actor public reputation monitoring demo.",
  queries: ["latest news", "movie review", "public reaction", "interview controversy"],
  searchMap: null,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString()
};
target.searchMap = buildSearchMap(target);

await writeDb({
  targets: [target],
  scans: [],
  mentions: [],
  alerts: []
});

await runScan(target.id);
console.log("Demo database reset.");
