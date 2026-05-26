const cache = new Map();

export async function findTargetImages(target) {
  if (!target?.name) return [];
  const cacheKey = `${target.name}:${target.type}`;
  if (cache.has(cacheKey)) return cache.get(cacheKey);

  const images = await findFromWikipedia(target).catch(() => []);
  const result = images.slice(0, 2);
  cache.set(cacheKey, result);
  return result;
}

async function findFromWikipedia(target) {
  const exact = await findExactWikipediaPage(target);
  if (exact.length) return exact;

  const searchUrl = new URL("https://en.wikipedia.org/w/api.php");
  searchUrl.searchParams.set("action", "query");
  searchUrl.searchParams.set("generator", "search");
  searchUrl.searchParams.set("gsrsearch", `${target.name} ${target.type || ""}`.trim());
  searchUrl.searchParams.set("gsrlimit", "5");
  searchUrl.searchParams.set("prop", "pageimages|info");
  searchUrl.searchParams.set("pithumbsize", "500");
  searchUrl.searchParams.set("inprop", "url");
  searchUrl.searchParams.set("format", "json");
  searchUrl.searchParams.set("origin", "*");

  const response = await fetchWithTimeout(searchUrl, 10000);
  if (!response.ok) return [];

  const data = await response.json();
  const targetTokens = normalizeTitle(target.name).split(" ").filter(Boolean);
  return Object.values(data.query?.pages || {})
    .filter((page) => isCloseTitleMatch(page.title, targetTokens))
    .filter((page) => page.thumbnail?.source)
    .map((page) => ({
      title: page.title,
      url: page.thumbnail.source,
      sourceUrl: page.fullurl,
      source: "Wikipedia"
    }));
}

async function findExactWikipediaPage(target) {
  const url = new URL("https://en.wikipedia.org/w/api.php");
  url.searchParams.set("action", "query");
  url.searchParams.set("titles", target.name);
  url.searchParams.set("prop", "pageimages|info");
  url.searchParams.set("pithumbsize", "500");
  url.searchParams.set("inprop", "url");
  url.searchParams.set("redirects", "1");
  url.searchParams.set("format", "json");
  url.searchParams.set("origin", "*");

  const response = await fetchWithTimeout(url, 10000);
  if (!response.ok) return [];
  const data = await response.json();
  return Object.values(data.query?.pages || {})
    .filter((page) => page.thumbnail?.source)
    .map((page) => ({
      title: page.title,
      url: page.thumbnail.source,
      sourceUrl: page.fullurl,
      source: "Wikipedia"
    }));
}

function isCloseTitleMatch(title, targetTokens) {
  const normalized = normalizeTitle(title);
  return targetTokens.length > 0 && targetTokens.every((token) => normalized.includes(token));
}

function normalizeTitle(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

async function fetchWithTimeout(url, timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {
      signal: controller.signal,
      headers: {
        "User-Agent": "ReputationIntelligenceDashboard/0.1"
      }
    });
  } finally {
    clearTimeout(timeout);
  }
}
