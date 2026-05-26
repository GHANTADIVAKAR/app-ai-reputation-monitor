const baseRiskTerms = [
  "review",
  "public reaction",
  "controversy",
  "interview",
  "troll",
  "criticism",
  "fans",
  "flop",
  "hit",
  "movie",
  "trailer"
];

export function buildSearchMap(target) {
  const name = clean(target.name);
  const providedQueries = Array.isArray(target.queries) ? target.queries.map(clean).filter(Boolean) : [];
  const tokens = name.split(/\s+/).filter(Boolean);
  const initials = tokens.map((token) => token[0]).join("").toUpperCase();
  const compactName = name.replace(/\s+/g, "");
  const lowerCompact = compactName.toLowerCase();

  const names = unique([
    name,
    compactName,
    initials.length > 1 ? initials : "",
    ...nameVariants(name)
  ]);

  const hashtags = unique([
    `#${compactName}`,
    initials.length > 1 ? `#${initials}` : "",
    ...providedQueries.map((query) => `#${toHashTag(query)}`),
    ...baseRiskTerms.slice(0, 6).map((term) => `#${toHashTag(`${compactName} ${term}`)}`)
  ]);

  const handles = unique([
    `@${lowerCompact}`,
    initials.length > 1 ? `@${initials.toLowerCase()}` : "",
    `@the${lowerCompact}`,
    `@${lowerCompact}official`
  ]);

  const topics = unique([
    ...providedQueries,
    ...baseRiskTerms
  ]);

  const searchPhrases = unique([
    ...names,
    ...providedQueries.map((query) => `${name} ${query}`),
    ...hashtags,
    ...handles
  ]).slice(0, 40);

  return {
    names,
    hashtags,
    handles,
    topics,
    searchPhrases,
    generatedAt: new Date().toISOString(),
    strategy: "local-keyword-expansion-v1"
  };
}

export function ensureSearchMap(target) {
  if (target.searchMap?.searchPhrases?.length) return target.searchMap;
  return buildSearchMap(target);
}

function nameVariants(name) {
  const variants = [];
  if (/deverakonda/i.test(name)) variants.push(name.replace(/deverakonda/ig, "Devarakonda"));
  if (/devarakonda/i.test(name)) variants.push(name.replace(/devarakonda/ig, "Deverakonda"));
  return variants;
}

function toHashTag(value) {
  return clean(value).replace(/[^a-zA-Z0-9]+/g, "");
}

function clean(value) {
  return String(value || "").trim().replace(/\s+/g, " ");
}

function unique(values) {
  const seen = new Set();
  return values
    .map((value) => String(value || "").trim())
    .filter(Boolean)
    .filter((value) => {
      const key = value.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}
