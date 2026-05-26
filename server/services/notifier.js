import { config } from "../config.js";

export async function sendAlert(alert, target) {
  if (!config.alertWebhookUrl) return { skipped: true };

  const response = await fetch(config.alertWebhookUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      target,
      alert,
      sentAt: new Date().toISOString()
    })
  });

  if (!response.ok) {
    throw new Error(`Alert webhook returned ${response.status}`);
  }

  return { sent: true };
}
