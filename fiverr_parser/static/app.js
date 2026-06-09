"use strict";

const $ = (sel) => document.querySelector(sel);
const api = async (path, opts) => {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
};

const state = {
  running: false,
  lastLogTs: null,
  filters: { search: "", query: "", onlyNew: false, noReviews: false, newSeller: false },
};

function filterParams(extra = {}) {
  const p = new URLSearchParams({ limit: "120", ...extra });
  if (state.filters.search) p.set("search", state.filters.search);
  if (state.filters.query) p.set("query", state.filters.query);
  if (state.filters.onlyNew) p.set("only_new", "true");
  if (state.filters.noReviews) p.set("no_reviews", "true");
  if (state.filters.newSeller) p.set("new_seller", "true");
  return p;
}

const AVATAR_COLORS = ["#1dbf73","#4aa8f5","#f5b94a","#b478ff","#ff5f6d","#19a463","#ef6cb5","#2dd4bf"];
const colorFor = (s) => {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) & 0xffff;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
};
const initials = (s) => (s || "?").slice(0, 2).toUpperCase();
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toast(msg, kind = "info") {
  const host = $("#toast-host");
  const el = document.createElement("div");
  el.className = "toast";
  el.style.borderLeftColor = kind === "error" ? "#ff5f6d" : kind === "warn" ? "#f5b94a" : "#1dbf73";
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 300); }, 3500);
}

/* ---------- Rendering ---------- */
function renderStats(s) {
  $("#stat-gigs").textContent = s.gigs.toLocaleString();
  $("#stat-sellers").textContent = s.sellers.toLocaleString();
  $("#stat-pro").textContent = s.pro_sellers.toLocaleString();
  $("#stat-new").textContent = s.new_24h.toLocaleString();

  const sel = $("#filter-query");
  const current = sel.value;
  const queries = (s.by_query || []).filter((q) => q.query);
  sel.innerHTML = '<option value="">All queries</option>' +
    queries.map((q) => `<option value="${esc(q.query)}">${esc(q.query)} (${q.c})</option>`).join("");
  sel.value = current;
}

function gigCard(g) {
  const seller = g.seller_username || "unknown";
  const price = (g.price_cents / 100).toFixed(g.price_cents % 100 ? 2 : 0);
  const tags = [];
  if (g.seller_pro) tags.push('<span class="tag pro">Pro</span>');
  if (g.seller_level) tags.push(`<span class="tag level">${esc(g.seller_level)}</span>`);
  const rating = g.reviews_count
    ? `<div class="rating">★ ${g.rating.toFixed(1)} <span class="rc">(${g.reviews_count})</span></div>`
    : `<div class="rating rc" style="color:var(--muted)">no reviews yet</div>`;
  const title = g.url
    ? `<a href="${esc(g.url)}" target="_blank" rel="noopener">${esc(g.title)}</a>`
    : esc(g.title);
  const profile = g.seller_profile || "#";
  return `
    <article class="gig ${g.is_new ? "is-new" : ""}">
      <div class="gig-title">${title}</div>
      <div class="seller-row">
        <div class="avatar" style="background:${colorFor(seller)}">${esc(initials(seller))}</div>
        <div class="seller-info">
          <div class="seller-name"><a href="${esc(profile)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">@${esc(seller)}</a></div>
          <div class="seller-sub">${esc(g.seller_country || "—")} ${tags.join(" ")}</div>
        </div>
      </div>
      <div class="gig-foot">
        <div class="price">$${price} <small>${esc(g.currency || "USD")}</small></div>
        ${rating}
      </div>
    </article>`;
}

async function refreshFeed() {
  const p = filterParams();
  try {
    const { gigs, count } = await api(`/api/gigs?${p}`);
    $("#feed-count").textContent = count;
    $("#feed").innerHTML = gigs.length
      ? gigs.map(gigCard).join("")
      : '<div class="empty">No listings yet — hit <b>Run now</b> or start the scheduler.</div>';
  } catch (e) { /* keep last view */ }
}

function renderLogs(logs) {
  const box = $("#console");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.innerHTML = logs.map((l) => {
    const t = (l.ts || "").slice(11, 19);
    return `<div class="log-line ${esc(l.level)}"><span class="lts">${t}</span><span class="lvl">${esc(l.level)}</span><span class="msg">${esc(l.message)}</span></div>`;
  }).join("");
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function renderStatus(st) {
  state.running = st.running;
  const dot = $("#status-dot");
  const txt = $("#status-text");
  dot.className = "dot" + (st.busy ? " busy" : st.running ? " live" : "");
  txt.textContent = st.busy ? "scraping…" : st.running ? "running" : "idle";
  $("#btn-toggle").textContent = st.running ? "⏸ Stop" : "▶ Start";
  const badge = $("#mode-badge");
  badge.textContent = st.demo_mode ? "demo" : "live";
  badge.classList.toggle("live", !st.demo_mode);
}

/* ---------- Polling ---------- */
async function tick() {
  try {
    const [stats, status, logs] = await Promise.all([
      api("/api/stats"), api("/api/status"), api("/api/logs?limit=120"),
    ]);
    renderStats(stats);
    renderStatus(status);
    renderLogs(logs.logs);
  } catch (e) { /* transient */ }
  await refreshFeed();
}

/* ---------- Settings ---------- */
async function loadSettings() {
  const s = await api("/api/settings");
  $("#set-queries").value = (s.queries || []).join(", ");
  $("#set-interval").value = s.interval_seconds;
  $("#set-max").value = s.max_per_query;
  $("#set-proxy").value = s.proxy || "";
  $("#set-demo").checked = !!s.demo_mode;
  $("#set-autostart").checked = !!s.autostart;
}

async function saveSettings() {
  const payload = {
    queries: $("#set-queries").value.split(",").map((q) => q.trim()).filter(Boolean),
    interval_seconds: parseInt($("#set-interval").value, 10),
    max_per_query: parseInt($("#set-max").value, 10),
    proxy: $("#set-proxy").value.trim(),
    demo_mode: $("#set-demo").checked,
    autostart: $("#set-autostart").checked,
  };
  await api("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  toast("Settings saved");
  $("#settings-drawer").hidden = true;
  tick();
}

/* ---------- Wiring ---------- */
function wire() {
  $("#btn-toggle").addEventListener("click", async () => {
    const ep = state.running ? "/api/scheduler/stop" : "/api/scheduler/start";
    const st = await api(ep, { method: "POST" });
    renderStatus(st);
    toast(state.running ? "Scheduler running" : "Scheduler stopped");
  });
  $("#btn-run").addEventListener("click", async () => {
    await api("/api/scheduler/run-now", { method: "POST" });
    toast("Scrape cycle triggered ⚡");
    setTimeout(tick, 600);
  });
  $("#btn-settings").addEventListener("click", () => {
    const d = $("#settings-drawer");
    d.hidden = !d.hidden;
    if (!d.hidden) loadSettings();
  });
  $("#btn-settings-cancel").addEventListener("click", () => { $("#settings-drawer").hidden = true; });
  $("#btn-settings-save").addEventListener("click", saveSettings);
  $("#btn-clearlog").addEventListener("click", () => { $("#console").innerHTML = ""; });

  let searchTimer;
  $("#filter-search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.filters.search = e.target.value.trim(); refreshFeed(); }, 250);
  });
  $("#filter-query").addEventListener("change", (e) => { state.filters.query = e.target.value; refreshFeed(); });
  $("#filter-new").addEventListener("change", (e) => { state.filters.onlyNew = e.target.checked; refreshFeed(); });
  $("#filter-noreviews").addEventListener("change", (e) => { state.filters.noReviews = e.target.checked; refreshFeed(); });
  $("#filter-newseller").addEventListener("change", (e) => { state.filters.newSeller = e.target.checked; refreshFeed(); });

  $("#btn-export").addEventListener("click", () => {
    const p = filterParams({ limit: "500" });
    window.location.href = `/api/export.txt?${p}`;
    toast("Экспорт URL в .txt ⬇");
  });
}

wire();
tick();
setInterval(tick, 4000);
