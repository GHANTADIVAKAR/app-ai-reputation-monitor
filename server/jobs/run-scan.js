import { runScan } from "../services/scanner.js";

const targetId = process.argv[2] || null;
const scans = await runScan(targetId);
console.log(JSON.stringify({ completed: scans.length, scans }, null, 2));
