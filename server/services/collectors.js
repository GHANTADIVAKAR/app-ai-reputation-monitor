import { config } from "../config.js";
import { createDemoMentions } from "./demo-data.js";

export async function collectMentions(target) {
  const results = [];
  const sourceStatus = [];
  const queries = target.searchMap?.searchPhrases?.length
    ? target.searchMap.searchPhrases
    : target.queries?.length
      ? target.queries
      : config.defaultQueries;

  const collectors = [
    ["Google News Live", () => collectFromGoogleNews(target, queries)],
    ["SerpAPI Web", () => collectFromSerpApi(target, queries)],
    ["YouTube", () => collectFromYouTube(target)],
    ["Reddit", () => collectFromReddit(target)],
    ["X Posts", () => collectFromX(target)],
    ["Instagram Public Media", () => collectFromInstagram(target)],
    ["Social Listening Provider", () => collectFromSocialListeningProvider(target)]
  ].filter(Array.isArray);

  for (const [name, collector] of collectors) {
    try {
      const items = await collector();
      sourceStatus.push({ name, ok: true, count: items.length });
      results.push(...items);
    } catch (error) {
      sourceStatus.push({ name, ok: false, count: 0, error: error.message });
      console.warn(`${name} collector failed:`, error.message);
    }
  }

  const liveResults = dedupe(results);
  if (liveResults.length) {
    return {
      mentions: liveResults,
      mode: "live",
      sourceStatus
    };
  }

  const demoMentions = createDemoMentions(target);
  sourceStatus.push({ name: "Demo Fallback", ok: true, count: demoMentions.length });
  return {
    mentions: demoMentions,
    mode: "demo",
    sourceStatus
  };
}

async function collectFromGoogleNews(target, queries) {
  if (!config.liveNewsEnabled) return [];
  const collected = [];
  const searchPhrases = queries.slice(0, 8).map((query) => query.includes(target.name) ? query : `${target.name} ${query}`);
  if (!searchPhrases.length) searchPhrases.push(target.name);

  for (const phrase of searchPhrases) {
    const url = new URL("https://news.google.com/rss/search");
    url.searchParams.set("q", phrase);
    url.searchParams.set("hl", "en-IN");
    url.searchParams.set("gl", "IN");
    url.searchParams.set("ceid", "IN:en");

    const response = await fetchWithTimeout(url, {}, 12000);
    if (!response.ok) continue;
    const xml = await response.text();
    for (const item of parseRssItems(xml).slice(0, 8)) {
      collected.push(baseMention(target, {
        title: item.title,
        url: normalizeGoogleNewsUrl(item.link),
        source: item.source || "Google News",
        author: item.source || "Google News",
        rawText: stripHtml(item.description || item.title),
        publishedAt: item.pubDate ? new Date(item.pubDate).toISOString() : new Date().toISOString(),
        engagement: 0
      }));
    }
  }

  return collected;
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 10000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

function parseRssItems(xml) {
  return [...xml.matchAll(/<item>([\s\S]*?)<\/item>/g)].map((match) => {
    const itemXml = match[1];
    return {
      title: decodeXml(readTag(itemXml, "title")),
      link: decodeXml(readTag(itemXml, "link")),
      description: decodeXml(readTag(itemXml, "description")),
      pubDate: decodeXml(readTag(itemXml, "pubDate")),
      source: decodeXml(readTag(itemXml, "source"))
    };
  }).filter((item) => item.title && item.link);
}

function readTag(xml, tag) {
  const match = xml.match(new RegExp(`<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${tag}>`, "i"));
  return match?.[1]?.trim() || "";
}

function decodeXml(value) {
  return String(value || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, "\"")
    .replace(/&#39;/g, "'")
    .replace(/&#x27;/g, "'");
}

function stripHtml(value) {
  return String(value || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
}

function normalizeGoogleNewsUrl(value) {
  return value || "";
}

async function collectFromSerpApi(target, queries) {
  if (!config.serpApiKey) return [];
  const collected = [];
  for (const query of queries.slice(0, 8)) {
    const url = new URL("https://serpapi.com/search.json");
    url.searchParams.set("engine", "google");
    url.searchParams.set("q", query.includes(target.name) ? query : `${target.name} ${query}`);
    url.searchParams.set("api_key", config.serpApiKey);
    url.searchParams.set("num", "10");

    const response = await fetch(url);
    if (!response.ok) continue;
    const data = await response.json();
    for (const item of data.organic_results || []) {
      collected.push(baseMention(target, {
        title: item.title,
        url: item.link,
        source: item.source || "Web",
        rawText: item.snippet || item.title,
        publishedAt: new Date().toISOString(),
        engagement: 0
      }));
    }
  }
  return collected;
}

async function collectFromYouTube(target) {
  if (!config.youtubeApiKey) return [];
  const query = target.searchMap?.searchPhrases?.slice(0, 8).join(" OR ") || target.name;
  const url = new URL("https://www.googleapis.com/youtube/v3/search");
  url.searchParams.set("part", "snippet");
  url.searchParams.set("q", query);
  url.searchParams.set("type", "video");
  url.searchParams.set("maxResults", "10");
  url.searchParams.set("order", "date");
  url.searchParams.set("key", config.youtubeApiKey);

  const response = await fetch(url);
  if (!response.ok) return [];
  const data = await response.json();
  return (data.items || []).map((item) => {
    const videoId = item.id?.videoId;
    return baseMention(target, {
      title: item.snippet?.title,
      url: `https://www.youtube.com/watch?v=${videoId}`,
      source: "YouTube",
      author: item.snippet?.channelTitle,
      rawText: item.snippet?.description || item.snippet?.title,
      publishedAt: item.snippet?.publishedAt,
      engagement: 0,
      mediaType: "video",
      platform: "youtube",
      video: {
        id: videoId,
        watched: false,
        analysisMethod: "metadata",
        note: "Video-level watching/transcript analysis needs captions, transcript access, or a video intelligence provider."
      }
    });
  });
}

async function collectFromReddit(target) {
  if (!config.redditEnabled) return [];
  const url = new URL("https://www.reddit.com/search.json");
  url.searchParams.set("q", target.searchMap?.searchPhrases?.slice(0, 8).join(" OR ") || target.name);
  url.searchParams.set("sort", "new");
  url.searchParams.set("limit", "10");

  const response = await fetch(url, {
    headers: { "User-Agent": config.redditUserAgent }
  });
  if (!response.ok) return [];
  const data = await response.json();
  return (data.data?.children || []).map(({ data: item }) => baseMention(target, {
    title: item.title,
    url: `https://reddit.com${item.permalink}`,
    source: "Reddit",
    author: item.author,
    rawText: item.selftext || item.title,
    publishedAt: new Date(item.created_utc * 1000).toISOString(),
    engagement: Number(item.score || 0) + Number(item.num_comments || 0)
  }));
}

async function collectFromX(target) {
  if (!config.xBearerToken) return [];

  const url = new URL("https://api.twitter.com/2/tweets/search/recent");
  url.searchParams.set("query", buildXQuery(target));
  url.searchParams.set("max_results", "20");
  url.searchParams.set("tweet.fields", "created_at,public_metrics,author_id,lang");

  const response = await fetchWithTimeout(url, {
    headers: { Authorization: `Bearer ${config.xBearerToken}` }
  }, 12000);

  if (!response.ok) return [];
  const data = await response.json();
  return (data.data || []).map((tweet) => baseMention(target, {
    title: tweet.text.slice(0, 120),
    url: `https://x.com/i/web/status/${tweet.id}`,
    source: "X",
    author: tweet.author_id,
    rawText: tweet.text,
    publishedAt: tweet.created_at,
    engagement: Object.values(tweet.public_metrics || {}).reduce((sum, value) => sum + Number(value || 0), 0),
    mediaType: "post",
    platform: "x"
  }));
}

function buildXQuery(target) {
  const terms = [
    ...(target.searchMap?.names || []),
    ...(target.searchMap?.handles || []),
    ...(target.searchMap?.hashtags || [])
  ].slice(0, 12);

  return `(${terms.map((term) => term.startsWith("@") || term.startsWith("#") ? term : `"${term}"`).join(" OR ")}) -is:retweet`;
}

async function collectFromInstagram() {
  if (!config.instagramAccessToken) return [];

  // Instagram hashtag, reels, comments, and stories need Meta-approved app permissions
  // and account-level access. This collector is intentionally credential-gated.
  return [];
}

async function collectFromSocialListeningProvider(target) {
  if (!config.socialListeningWebhookUrl) return [];

  const url = new URL(config.socialListeningWebhookUrl);
  url.searchParams.set("target", target.name);

  const response = await fetchWithTimeout(url, {}, 15000);
  if (!response.ok) return [];
  const data = await response.json();

  return (data.mentions || []).map((item) => baseMention(target, {
    title: item.title || item.text?.slice(0, 120) || "Social mention",
    url: item.url,
    source: item.source || item.platform || "Social",
    author: item.author || item.username || "Unknown",
    rawText: item.text || item.summary || item.title || "",
    publishedAt: item.publishedAt || item.createdAt,
    engagement: item.engagement || item.views || item.likes || 0,
    mediaType: item.mediaType || "post",
    platform: item.platform || "social",
    video: item.video
  }));
}

function baseMention(target, input) {
  return {
    id: `mention_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
    targetId: target.id,
    title: input.title || "Untitled mention",
    url: input.url,
    source: input.source || "Web",
    author: input.author || input.source || "Unknown",
    platform: input.platform || input.source || "web",
    mediaType: input.mediaType || "article",
    publishedAt: input.publishedAt || new Date().toISOString(),
    engagement: Number(input.engagement || 0),
    rawText: input.rawText || input.title || "",
    video: input.video || null,
    discoveredAt: new Date().toISOString()
  };
}

function dedupe(items) {
  const seen = new Set();
  return items.filter((item) => {
    if (!item.url || seen.has(item.url)) return false;
    seen.add(item.url);
    return true;
  });
}
