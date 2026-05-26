import { createTarget, readDb, writeDb } from "../store.js";
import { runScan } from "../services/scanner.js";

const db = await readDb();
if (!db.targets.length) {
  const target = await createTarget({
    name: "Vijay Deverakonda",
    type: "celebrity",
    description: "Telugu film actor public reputation monitoring demo.",
    queries: ["latest news", "movie review", "public reaction", "interview controversy"]
  });
  await runScan(target.id);
  console.log(`Seeded target: ${target.name}`);
} else {
  await writeDb(db);
  console.log("Seed skipped: database already has targets.");
}
