import { config } from "../config.js";

const positiveWords = [
  "praise",
  "support",
  "good",
  "great",
  "excellent",
  "hit",
  "charity",
  "positive",
  "strong",
  "recover",
  "fans"
];

const negativeWords = [
  "negative",
  "arrogant",
  "flop",
  "controversy",
  "criticize",
  "bad",
  "weak",
  "harm",
  "angry",
  "question",
  "risk"
];

export async function analyzeMention(mention, target) {
  if (config.openaiApiKey) {
    try {
      return await analyzeWithOpenAI(mention, target);
    } catch (error) {
      console.warn("OpenAI analysis failed, using local analyzer:", error.message);
    }
  }
  return analyzeLocally(mention);
}

function analyzeLocally(mention) {
  const text = `${mention.title} ${mention.rawText || ""}`.toLowerCase();
  const positiveScore = positiveWords.reduce((score, word) => score + Number(text.includes(word)), 0);
  const negativeScore = negativeWords.reduce((score, word) => score + Number(text.includes(word)), 0);

  let sentiment = "neutral";
  if (positiveScore > negativeScore) sentiment = "positive";
  if (negativeScore > positiveScore) sentiment = "negative";
  if (positiveScore > 0 && negativeScore > 0 && Math.abs(positiveScore - negativeScore) <= 1) sentiment = "mixed";

  const riskLevel = sentiment === "negative" && mention.engagement > 1500 ? "high" : sentiment === "negative" ? "medium" : "low";

  return {
    sentiment,
    confidence: Math.min(0.92, 0.55 + Math.abs(positiveScore - negativeScore) * 0.12),
    emotion: sentiment === "negative" ? "criticism" : sentiment === "positive" ? "support" : "discussion",
    riskLevel,
    summary: mention.rawText || mention.title,
    reason: `Local keyword analysis found ${positiveScore} positive and ${negativeScore} negative reputation signals.`,
    recommendedAction: recommendationFor(sentiment, riskLevel)
  };
}

function recommendationFor(sentiment, riskLevel) {
  if (sentiment === "negative" && riskLevel === "high") {
    return "Prioritize PR review, prepare a short clarification, and monitor whether high-engagement comments repeat the same complaint.";
  }
  if (sentiment === "negative") {
    return "Monitor this source and prepare talking points if the same criticism appears across more channels.";
  }
  if (sentiment === "positive") {
    return "Amplify this through official social channels and consider engaging the creator or publication.";
  }
  if (sentiment === "mixed") {
    return "Separate valid criticism from fan debate, then respond only if the topic keeps gaining engagement.";
  }
  return "Keep monitoring; no immediate PR action is required.";
}

async function analyzeWithOpenAI(mention, target) {
  const response = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${config.openaiApiKey}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      model: config.openaiModel,
      temperature: 0.2,
      response_format: { type: "json_object" },
      messages: [
        {
          role: "system",
          content:
            "You analyze public reputation mentions for PR teams. Return strict JSON with sentiment, confidence, emotion, riskLevel, summary, reason, recommendedAction. sentiment must be positive, negative, neutral, or mixed. riskLevel must be low, medium, or high."
        },
        {
          role: "user",
          content: JSON.stringify({
            target: { name: target.name, type: target.type, description: target.description },
            mention: {
              title: mention.title,
              source: mention.source,
              author: mention.author,
              engagement: mention.engagement,
              text: mention.rawText
            }
          })
        }
      ]
    })
  });

  if (!response.ok) {
    throw new Error(`OpenAI returned ${response.status}`);
  }
  const payload = await response.json();
  return JSON.parse(payload.choices?.[0]?.message?.content || "{}");
}
