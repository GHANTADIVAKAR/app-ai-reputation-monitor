let state = {
  targets: [],
  selectedTargetId: null,
  dashboard: null
};

const els = {
  targetForm: document.querySelector("#target-form"),
  targetList: document.querySelector("#target-list"),
  targetName: document.querySelector("#target-name"),
  targetType: document.querySelector("#target-type"),
  targetQueries: document.querySelector("#target-queries"),
  title: document.querySelector("#page-title"),
  targetImages: document.querySelector("#target-images"),
  sourceStatus: document.querySelector("#source-status"),
  scanButton: document.querySelector("#scan-button"),
  metrics: {
    total: document.querySelector("#metric-total"),
    positive: document.querySelector("#metric-positive"),
    negative: document.querySelector("#metric-negative"),
    neutral: document.querySelector("#metric-neutral")
  },
  alerts: document.querySelector("#alerts"),
  searchMap: document.querySelector("#search-map"),
  topNegative: document.querySelector("#top-negative"),
  topPositive: document.querySelector("#top-positive"),
  topNeutral: document.querySelector("#top-neutral"),
  mentions: document.querySelector("#mentions"),
  chart: document.querySelector("#trend-chart")
};

init();

async function init() {
  bindEvents();
  await loadTargets();
  await loadDashboard();
}

function bindEvents() {
  els.targetForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      name: els.targetName.value.trim(),
      type: els.targetType.value,
      queries: els.targetQueries.value.split(",").map((item) => item.trim()).filter(Boolean)
    };
    const target = await api("/api/targets", { method: "POST", body: payload });
    state.selectedTargetId = target.id;
    els.targetForm.reset();
    await loadTargets();
    await runScan();
  });

  els.scanButton.addEventListener("click", runScan);
}

async function loadTargets() {
  state.targets = await api("/api/targets");
  if (!state.selectedTargetId && state.targets[0]) {
    state.selectedTargetId = state.targets[0].id;
  }
  renderTargets();
}

async function loadDashboard() {
  const query = state.selectedTargetId ? `?targetId=${encodeURIComponent(state.selectedTargetId)}` : "";
  try {
    state.dashboard = await api(`/api/dashboard${query}`);
  } catch {
    state.dashboard = null;
  }
  renderDashboard();
}

async function runScan() {
  if (!state.selectedTargetId) return;
  els.scanButton.disabled = true;
  els.scanButton.textContent = "Scanning...";
  try {
    await api("/api/scan", { method: "POST", body: { targetId: state.selectedTargetId } });
    await loadDashboard();
  } finally {
    els.scanButton.disabled = false;
    els.scanButton.textContent = "Run Scan";
  }
}

function renderTargets() {
  els.targetList.innerHTML = "";
  if (!state.targets.length) {
    els.targetList.innerHTML = `<p class="empty">Add your first tracking target.</p>`;
    return;
  }

  for (const target of state.targets) {
    const button = document.createElement("button");
    button.className = `target-item ${target.id === state.selectedTargetId ? "active" : ""}`;
    button.innerHTML = `<strong>${escapeHtml(target.name)}</strong><br><span class="meta">${escapeHtml(target.type)}</span>`;
    button.addEventListener("click", async () => {
      state.selectedTargetId = target.id;
      renderTargets();
      await loadDashboard();
    });
    els.targetList.append(button);
  }
}

function renderDashboard() {
  const dashboard = state.dashboard;
  if (!dashboard) {
    els.title.textContent = "Public Sentiment Monitor";
    els.metrics.total.textContent = "0";
    els.metrics.positive.textContent = "0";
    els.metrics.negative.textContent = "0";
    els.metrics.neutral.textContent = "0";
    els.targetImages.innerHTML = "";
    els.sourceStatus.innerHTML = `<span class="status-dot demo"></span>Waiting for first scan`;
    els.alerts.innerHTML = `<p class="empty">No alerts yet.</p>`;
    els.searchMap.innerHTML = `<p class="empty">No search map yet.</p>`;
    els.topNegative.innerHTML = `<p class="empty">No negative mentions yet.</p>`;
    els.topPositive.innerHTML = `<p class="empty">No positive mentions yet.</p>`;
    els.topNeutral.innerHTML = `<p class="empty">No neutral mentions yet.</p>`;
    els.mentions.innerHTML = `<p class="empty">Add a target and run the first scan.</p>`;
    drawChart([]);
    return;
  }

  els.title.textContent = dashboard.target.name;
  els.targetImages.innerHTML = renderTargetImages(dashboard);
  els.sourceStatus.innerHTML = renderSourceStatus(dashboard);
  els.metrics.total.textContent = dashboard.totals.total;
  els.metrics.positive.textContent = dashboard.totals.positive;
  els.metrics.negative.textContent = dashboard.totals.negative;
  els.metrics.neutral.textContent = dashboard.totals.neutral + dashboard.totals.mixed;

  renderCards(els.alerts, dashboard.alerts, renderAlert, "No alerts yet.");
  els.searchMap.innerHTML = renderSearchMap(dashboard.searchMap);
  renderCards(els.topNegative, dashboard.topNegative, renderMentionCompact, "No negative mentions yet.");
  renderCards(els.topPositive, dashboard.topPositive, renderMentionCompact, "No positive mentions yet.");
  renderCards(els.topNeutral, dashboard.topNeutral, renderMentionCompact, "No neutral mentions yet.");
  renderCards(els.mentions, dashboard.mentions, renderMention, "No mentions yet.");
  drawChart(dashboard.scans.slice().reverse());
}

function renderSearchMap(searchMap) {
  if (!searchMap) return `<p class="empty">No search map yet.</p>`;
  const groups = [
    ["Names", searchMap.names],
    ["Hashtags", searchMap.hashtags],
    ["Handles", searchMap.handles],
    ["Topics", searchMap.topics],
    ["Search phrases", searchMap.searchPhrases]
  ];

  return groups.map(([label, values]) => `
    <div class="keyword-group">
      <strong>${escapeHtml(label)}</strong>
      <div class="keyword-list">
        ${(values || []).slice(0, 24).map((value) => `<span class="keyword-chip">${escapeHtml(value)}</span>`).join("") || `<span class="meta">None</span>`}
      </div>
    </div>
  `).join("");
}

function renderTargetImages(dashboard) {
  const images = dashboard.targetImages || [];
  if (!images.length) {
    const initials = dashboard.target.name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join("");
    return `
      <div class="person-image">
        <div class="person-image-fallback">${escapeHtml(initials || "?")}</div>
        <span>No image found</span>
      </div>
    `;
  }

  return images.slice(0, 2).map((image) => `
    <a class="person-image" href="${escapeAttr(image.sourceUrl || image.url)}" target="_blank" rel="noreferrer" title="${escapeAttr(image.title || "Image source")}">
      <img src="${escapeAttr(image.url)}" alt="${escapeAttr(image.title || dashboard.target.name)}" loading="lazy" />
      <span>${escapeHtml(image.title || image.source || "Source")}</span>
    </a>
  `).join("");
}

function renderSourceStatus(dashboard) {
  const mode = dashboard.dataMode || "unknown";
  const liveCount = (dashboard.sourceStatus || []).reduce((sum, source) => sum + Number(source.count || 0), 0);
  const lastScan = dashboard.lastScanAt ? `Last scan ${formatDate(dashboard.lastScanAt)}` : "No scan yet";
  const sourceNames = (dashboard.sourceStatus || [])
    .filter((source) => source.ok && source.count > 0)
    .map((source) => `${source.name}: ${source.count}`)
    .join(", ");
  const detail = sourceNames || "No live sources returned mentions";
  return `<span class="status-dot ${escapeAttr(mode)}"></span>${escapeHtml(mode.toUpperCase())} data · ${escapeHtml(detail)} · ${escapeHtml(lastScan)}`;
}

function renderCards(container, items, renderer, emptyText) {
  container.innerHTML = "";
  if (!items.length) {
    container.innerHTML = `<p class="empty">${emptyText}</p>`;
    return;
  }
  for (const item of items) {
    container.insertAdjacentHTML("beforeend", renderer(item));
  }
}

function renderAlert(alert) {
  return `
    <article class="alert">
      <div class="mention-head">
        <strong>${escapeHtml(alert.title)}</strong>
        <span class="pill negative">${escapeHtml(alert.severity)}</span>
      </div>
      <p class="summary">${escapeHtml(alert.body)}</p>
      <span class="meta">${formatDate(alert.createdAt)}</span>
    </article>
  `;
}

function renderMentionCompact(mention) {
  const sentiment = mention.analysis?.sentiment || "neutral";
  return `
    <article class="mention">
      <div class="mention-head">
        <a href="${escapeAttr(mention.url)}" target="_blank" rel="noreferrer">${escapeHtml(mention.title)}</a>
        <span class="pill ${sentiment}">${escapeHtml(sentiment)}</span>
      </div>
      <p class="summary">${escapeHtml(mention.analysis?.summary || mention.rawText || "")}</p>
      ${renderEvidence(mention)}
    </article>
  `;
}

function renderMention(mention) {
  const sentiment = mention.analysis?.sentiment || "neutral";
  return `
    <article class="mention">
      <div class="mention-head">
        <a href="${escapeAttr(mention.url)}" target="_blank" rel="noreferrer">${escapeHtml(mention.title)}</a>
        <span class="pill ${sentiment}">${escapeHtml(sentiment)}</span>
      </div>
      <p class="summary">${escapeHtml(mention.analysis?.summary || mention.rawText || "")}</p>
      <p class="action"><strong>Action:</strong> ${escapeHtml(mention.analysis?.recommendedAction || "Monitor this mention.")}</p>
      ${renderEvidence(mention)}
    </article>
  `;
}

function renderEvidence(mention) {
  const watched = mention.video?.watched ? "video watched" : mention.mediaType === "video" ? "video metadata" : mention.mediaType;
  return `
    <div class="evidence-row">
      <span class="chip">${escapeHtml(mention.source)}</span>
      <span class="chip">${escapeHtml(watched || "article")}</span>
      <span class="chip">${escapeHtml(mention.author || "Unknown")}</span>
      <span class="chip">engagement ${Number(mention.engagement || 0)}</span>
      <span class="chip">${escapeHtml(formatDate(mention.publishedAt))}</span>
    </div>
  `;
}

function drawChart(scans) {
  const canvas = els.chart;
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#fbfcfe";
  ctx.fillRect(0, 0, width, height);

  if (!scans.length) {
    ctx.fillStyle = "#6b7280";
    ctx.font = "16px system-ui";
    ctx.fillText("No scan trend yet.", 24, 40);
    return;
  }

  const padding = 36;
  const max = Math.max(1, ...scans.flatMap((scan) => [scan.positive, scan.negative, scan.neutral + scan.mixed]));
  drawSeries(ctx, scans, "positive", "#188455", max, padding, width, height);
  drawSeries(ctx, scans, "negative", "#c2413b", max, padding, width, height);
  drawSeries(ctx, scans, "neutral", "#b7791f", max, padding, width, height, (scan) => scan.neutral + scan.mixed);

  ctx.fillStyle = "#6b7280";
  ctx.font = "12px system-ui";
  ctx.fillText("Positive", padding, height - 12);
  ctx.fillText("Negative", padding + 90, height - 12);
  ctx.fillText("Mixed/Neutral", padding + 185, height - 12);
}

function drawSeries(ctx, scans, key, color, max, padding, width, height, accessor = (scan) => scan[key]) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.beginPath();
  scans.forEach((scan, index) => {
    const x = padding + (index * (width - padding * 2)) / Math.max(1, scans.length - 1);
    const y = height - padding - (accessor(scan) / max) * (height - padding * 2);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: { "Content-Type": "application/json" },
    body: options.body ? JSON.stringify(options.body) : undefined
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  })[char]);
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/"/g, "&quot;");
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}
