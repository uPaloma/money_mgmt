"use strict";
// Dashboard. Talks only to /api/* (the phone-reusable contract). Charts: ECharts.

const $ = (id) => document.getElementById(id);
const PAGE = 100;

const dark = matchMedia("(prefers-color-scheme: dark)").matches;
const T = dark
  ? { ink: "#ffffff", sub: "#c3c2b7", line: "#34332f", accent: "#3987e5" }
  : { ink: "#0b0b0b", sub: "#52514e", line: "#e7e6e2", accent: "#2a78d6" };
const GOOD = "#0ca30c", CRIT = "#d03b3b", OTHER = "#8a8a86";

let CUR = "EUR";
let CATS = [];                 // [{id,name,group_name,kind,color}]
const catColor = {};           // name -> color
let ACC, CAT;                  // multi-select handles (accounts, categories)
let offset = 0, total = 0;
const charts = {};             // id -> echarts instance

// List density: 'compact' cards (essentials, tap to expand) vs 'full' table.
// Default to compact on touch devices; remembered across visits.
const touch = matchMedia("(hover: none) and (pointer: coarse)").matches;
let density = localStorage.getItem("density") || (touch ? "compact" : "full");

// --- helpers --------------------------------------------------------------
const money = (a) => a == null ? "" :
  new Intl.NumberFormat(undefined, { style: "currency", currency: CUR, maximumFractionDigits: 0 }).format(a);
const money2 = (a) => a == null ? "" :
  new Intl.NumberFormat(undefined, { style: "currency", currency: CUR }).format(a);
// Compact form for axis ticks and bar labels (225K, 1.2M) — keeps big DKK/EUR
// numbers from colliding along the axis. Full precision stays in tooltips.
const moneyK = (a) => a == null ? "" :
  new Intl.NumberFormat(undefined, { style: "currency", currency: CUR, notation: "compact", maximumFractionDigits: 1 }).format(a);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// --- multi-select (checkbox dropdown) ------------------------------------
// A usable replacement for <select multiple>: a button that opens a checkbox
// panel (optionally grouped + searchable). items: [{value,label,color?,group?}].
const MSELS = [];
const closeAllMsel = () => MSELS.forEach((p) => (p.hidden = true));
addEventListener("click", closeAllMsel);

function multiSelect(mount, items, opts = {}) {
  const state = new Set();
  const el = document.createElement("div"); el.className = "msel";
  const btn = document.createElement("button");
  btn.type = "button"; btn.className = "msel-btn";
  btn.innerHTML = `<span class="cap"></span><span class="chev">▾</span>`;
  const pop = document.createElement("div"); pop.className = "msel-pop"; pop.hidden = true;
  el.append(btn, pop); mount.append(el);
  const cap = btn.querySelector(".cap");
  const labelOf = (v) => (items.find((i) => i.value === v) || {}).label || v;

  const paint = () => {
    const n = state.size;
    cap.textContent = n === 0 ? (opts.allLabel || "All") : n === 1 ? labelOf([...state][0]) : `${n} selected`;
    btn.classList.toggle("active", n > 0);
  };

  const search = opts.search
    ? Object.assign(document.createElement("input"), { type: "search", className: "msel-search", placeholder: "Filter…" })
    : null;
  if (search) pop.append(search);
  const optWrap = document.createElement("div"); pop.append(optWrap);
  const rows = [];
  let lastGroup = null;
  for (const it of items) {
    if (opts.grouped && it.group && it.group !== lastGroup) {
      const g = document.createElement("div"); g.className = "msel-grp"; g.textContent = it.group;
      optWrap.append(g); lastGroup = it.group;
    }
    const row = document.createElement("label"); row.className = "msel-opt";
    const cb = document.createElement("input"); cb.type = "checkbox";
    const dot = it.color ? `<span class="dot" style="background:${it.color}"></span>` : "";
    row.append(cb);
    row.insertAdjacentHTML("beforeend", dot + `<span>${esc(it.label)}</span>`);
    cb.onchange = () => { cb.checked ? state.add(it.value) : state.delete(it.value); paint(); opts.onChange?.(); };
    row._it = it; row._cb = cb; row._text = it.label.toLowerCase();
    optWrap.append(row); rows.push(row);
  }
  const actions = document.createElement("div"); actions.className = "msel-actions";
  const clear = Object.assign(document.createElement("button"), { type: "button", textContent: "Clear" });
  clear.onclick = () => { setVals([]); opts.onChange?.(); };
  actions.append(clear); pop.append(actions);

  if (search) search.oninput = () => {
    const q = search.value.toLowerCase();
    rows.forEach((r) => (r.hidden = q && !r._text.includes(q)));
  };
  btn.onclick = (e) => {
    e.stopPropagation();
    const willOpen = pop.hidden; closeAllMsel(); pop.hidden = !willOpen;
    if (!pop.hidden && search) { search.value = ""; rows.forEach((r) => (r.hidden = false)); search.focus(); }
  };
  pop.onclick = (e) => e.stopPropagation();

  function setVals(vals) {
    state.clear(); vals.forEach((v) => state.add(v));
    rows.forEach((r) => (r._cb.checked = state.has(r._it.value)));
    paint();
  }
  paint(); MSELS.push(pop);
  return { values: () => [...state], set: setVals };
}

function filterParams() {
  const p = new URLSearchParams();
  const { from, to } = presetRange($("preset").value);
  if (from) p.set("date_from", from);
  if (to) p.set("date_to", to);
  if ($("flow").value) p.set("flow", $("flow").value);
  if ($("q").value.trim()) p.set("q", $("q").value.trim());
  for (const a of ACC.values()) p.append("account", a);
  for (const c of CAT.values()) p.append("category", c);
  return p;
}

function presetRange(preset) {
  const today = new Date();
  const iso = (d) => d.toISOString().slice(0, 10);
  const start = (y, m, d) => iso(new Date(Date.UTC(y, m, d)));
  const Y = today.getUTCFullYear(), M = today.getUTCMonth(), D = today.getUTCDate();
  switch (preset) {
    case "month": return { from: start(Y, M, 1), to: iso(today) };
    case "3m": return { from: start(Y, M - 3, D), to: iso(today) };
    case "6m": return { from: start(Y, M - 6, D), to: iso(today) };
    case "12m": return { from: start(Y, M - 12, D), to: iso(today) };
    case "ytd": return { from: start(Y, 0, 1), to: iso(today) };
    case "custom": return { from: $("from").value, to: $("to").value };
    default: return { from: "", to: "" };
  }
}

const api = (path, extra) => {
  const p = filterParams();
  if (extra) for (const [k, v] of Object.entries(extra)) p.set(k, v);
  return fetch(`${path}?${p}`).then((r) => r.json());
};

// --- toast ----------------------------------------------------------------
let toastTimer;
function toast(html) {
  const el = $("toast");
  el.innerHTML = html; el.hidden = false;
  requestAnimationFrame(() => el.classList.add("show"));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => (el.hidden = true), 220);
  }, 3400);
}

// --- init -----------------------------------------------------------------
async function init() {
  const meta = await fetch("/api/filters").then((r) => r.json());
  CUR = meta.accounts[0]?.currency || "EUR";
  $("range").textContent = meta.date_min ? `${meta.date_min} → ${meta.date_max}` : "";

  ACC = multiSelect($("msel-account"),
    meta.accounts.map((a) => ({ value: a.account_key, label: `${a.aspsp_name} — ${a.name || a.account_key.slice(0, 6)}` })),
    { allLabel: "All accounts", onChange: refresh });

  CATS = await fetch("/api/categories").then((r) => r.json());
  for (const c of CATS) catColor[c.name] = c.color;
  CAT = multiSelect($("msel-category"),
    CATS.map((c) => ({ value: c.name, label: c.name, color: c.color, group: c.group_name })),
    { allLabel: "All categories", grouped: true, search: true, onChange: refresh });

  $("preset").onchange = () => {
    document.querySelectorAll(".custom-dates").forEach((e) => (e.hidden = $("preset").value !== "custom"));
    refresh();
  };
  ["flow", "from", "to"].forEach((id) => ($(id).onchange = refresh));
  $("groupby").onchange = () => { offset = 0; renderBottom(); };
  setDensityLabel();
  $("density").onclick = () => {
    density = density === "compact" ? "full" : "compact";
    localStorage.setItem("density", density);
    setDensityLabel();
    if ($("groupby").value === "list") { offset = 0; loadList(false); }
  };
  let t; $("q").oninput = () => { clearTimeout(t); t = setTimeout(refresh, 250); };
  $("reset").onclick = () => {
    $("preset").value = "all"; $("flow").value = ""; $("q").value = "";
    ACC.set([]); CAT.set([]);
    document.querySelectorAll(".custom-dates").forEach((e) => (e.hidden = true));
    refresh();
  };
  $("more").onclick = () => loadList(true);
  addEventListener("resize", () => Object.values(charts).forEach((c) => c.resize()));
  renderBalances();   // point-in-time, so fetched once and NOT tied to filters
  refresh();
}

// --- balances -------------------------------------------------------------
// These come from the bank, not from the transaction table, so they ignore the
// filter bar entirely. Kept visually separate for that reason: everything below
// answers "in this selection", this answers "right now".
async function renderBalances() {
  const accts = await fetch("/api/accounts").then((r) => r.json());
  const withBal = accts.filter((a) => a.balance);
  if (!withBal.length) {
    $("balances").innerHTML = '<span class="muted">No balances stored yet — run poll.py.</span>';
    return;
  }
  // Only total when every account shares one currency; summing EUR+DKK is a lie.
  const curs = new Set(withBal.map((a) => a.balance.currency || a.currency));
  const stale = withBal.map((a) => a.balance.reference_date).filter(Boolean).sort()[0];
  $("bal-note").textContent =
    `· as reported by the bank${stale ? `, oldest ${stale}` : ""} · not affected by filters`;

  const fmt = (v, cur) => new Intl.NumberFormat(undefined,
    { style: "currency", currency: cur || CUR }).format(v);
  const items = withBal.map((a) => {
    const b = a.balance, v = Number(b.amount);
    return `<div class="bal" data-key="${esc(a.account_key)}" title="Filter to this account">
        <div class="bal-k">${esc(a.aspsp_name || "")}</div>
        <div class="bal-n">${esc(a.name || a.iban || "")}</div>
        <div class="bal-v ${v < 0 ? "neg" : "pos"}">${fmt(v, b.currency)}</div>
        <div class="bal-t">${esc(b.balance_type || "?")}${b.reference_date ? " · " + esc(b.reference_date) : ""}</div>
      </div>`;
  });
  if (curs.size === 1) {
    const sum = withBal.reduce((s, a) => s + Number(a.balance.amount), 0);
    items.push(`<div class="bal bal-total">
        <div class="bal-k">Total</div><div class="bal-n">${withBal.length} accounts</div>
        <div class="bal-v ${sum < 0 ? "neg" : "pos"}">${fmt(sum, [...curs][0])}</div>
        <div class="bal-t">net worth</div>
      </div>`);
  }
  $("balances").innerHTML = items.join("");
  $("balances").querySelectorAll(".bal[data-key]").forEach((el) =>
    (el.onclick = () => { ACC.set([el.dataset.key]); refresh(); }));
}

// --- refresh everything ---------------------------------------------------
async function refresh() {
  offset = 0;
  const [summary, byMonth, byCat, byMerch, byAcct] = await Promise.all([
    api("/api/stats/summary"), api("/api/stats/by-month"), api("/api/stats/by-category"),
    api("/api/stats/by-merchant", { limit: 10 }), api("/api/stats/by-account"),
  ]);
  renderTiles(summary, byCat);
  chartMonth(byMonth);
  chartCategory(byCat);
  chartMerchants(byMerch);
  chartAccounts(byAcct);
  renderBottom();
}

function renderTiles(s, byCat) {
  const uncat = (byCat.find((r) => r.category === "Uncategorized") || {}).count || 0;
  const pct = s.count ? Math.round(100 * (s.count - uncat) / s.count) : 100;
  const tile = (k, v, cls = "", title = "") =>
    `<div class="tile"${title ? ` title="${title}"` : ""}><div class="k">${k}</div>` +
    `<div class="v ${cls}">${v}</div></div>`;
  const uncatTile =
    `<div class="tile clickable" id="tile-uncat" title="Show uncategorized">
       <div class="k"><span>Uncategorized</span><span>${pct}% done</span></div>
       <div class="v">${uncat}</div>
       <div class="meter"><span style="width:${pct}%"></span></div>
     </div>`;
  $("tiles").innerHTML =
    tile("Income", money(s.income), "pos", "Money in, excluding transfers between your own accounts") +
    tile("Expenses", money(s.expense), "neg", "Money out, excluding transfers between your own accounts") +
    tile("Net", money(s.net), s.net < 0 ? "neg" : "pos",
         "Income − Expenses. Transfers are excluded on both sides, so moving money between your own accounts never changes this.") +
    tile("Cash flow", money(s.net_cash), s.net_cash < 0 ? "neg" : "pos",
         "Every selected transaction summed as-is, transfers included. This is what actually moved through the accounts, and what reconciles against your balances.") +
    tile("Transfers", money(s.transfers), "", "Total volume moved by transfer-kind categories (both directions)") +
    tile("Transactions", s.count) +
    uncatTile;
  $("tile-uncat").onclick = () => {
    CAT.set(["Uncategorized"]); $("groupby").value = "list"; refresh();
  };
}

// --- charts ---------------------------------------------------------------
function ec(id) {
  if (!charts[id]) charts[id] = echarts.init($(id), null, { renderer: "svg" });
  return charts[id];
}
const axisText = { color: T.sub };
const grid = { left: 48, right: 16, top: 24, bottom: 28 };

// Give a chart container a height that fits its rows (horizontal bars).
function sizeFor(id, nRows) {
  $(id).style.height = Math.max(240, nRows * 30 + 60) + "px";
}

// Shared horizontal-bar chart: items = [{label, value, color?}], sorted desc.
function hbar(id, items, fallbackColor) {
  const rows = [...items].reverse(); // largest at top
  sizeFor(id, items.length);
  const c = ec(id);
  c.setOption({
    textStyle: { color: T.ink },
    grid: { left: 150, right: 74, top: 8, bottom: 24 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: money2 },
    xAxis: { type: "value", axisLabel: { ...axisText, formatter: moneyK, hideOverlap: true }, splitLine: { lineStyle: { color: T.line } } },
    yAxis: {
      type: "category", data: rows.map((r) => r.label),
      axisTick: { show: false }, axisLine: { lineStyle: { color: T.line } },
      axisLabel: { color: T.sub, formatter: (v) => v && v.length > 22 ? v.slice(0, 21) + "…" : v },
    },
    series: [{
      type: "bar", barMaxWidth: 22,
      data: rows.map((r) => ({ value: r.value, itemStyle: { color: r.color || fallbackColor, borderRadius: [0, 3, 3, 0] } })),
      label: { show: true, position: "right", color: T.sub, formatter: (p) => moneyK(p.value) },
    }],
  }, true);
  c.resize();
}

function chartMonth(rows) {
  ec("chart-month").setOption({
    textStyle: { color: T.ink }, grid,
    tooltip: { trigger: "axis", valueFormatter: money2 },
    legend: { data: ["Income", "Expense"], textStyle: { color: T.sub }, top: 0 },
    xAxis: { type: "category", data: rows.map((r) => r.month), axisLabel: axisText, axisLine: { lineStyle: { color: T.line } } },
    yAxis: { type: "value", axisLabel: { ...axisText, formatter: moneyK, hideOverlap: true }, splitLine: { lineStyle: { color: T.line } } },
    series: [
      { name: "Income", type: "bar", data: rows.map((r) => r.income || 0), itemStyle: { color: GOOD, borderRadius: [3, 3, 0, 0] } },
      { name: "Expense", type: "bar", data: rows.map((r) => r.expense || 0), itemStyle: { color: CRIT, borderRadius: [3, 3, 0, 0] } },
    ],
  }, true);
}

function chartCategory(rows) {
  // Only the outgoing side, and only non-transfer categories -- so the bars
  // here add up to exactly the Expenses tile.
  const expense = rows
    .filter((r) => r.kind !== "transfer" && r.expense > 0)
    .sort((a, b) => b.expense - a.expense);
  const top = expense.slice(0, 10);
  const rest = expense.slice(10).reduce((s, r) => s + r.expense, 0);
  const items = top.map((r) => ({ label: r.category, value: r.expense, color: catColor[r.category] || OTHER }));
  if (rest > 0) items.push({ label: "Other", value: +rest.toFixed(2), color: OTHER });
  hbar("chart-cat", items, OTHER);
}

function chartMerchants(rows) {
  hbar("chart-merch", rows.map((r) => ({ label: r.merchant || "(unknown)", value: r.total })), T.accent);
}

function chartAccounts(rows) {
  ec("chart-acct").setOption({
    textStyle: { color: T.ink }, grid,
    tooltip: { trigger: "axis", valueFormatter: money2 },
    legend: { data: ["Income", "Expense"], textStyle: { color: T.sub }, top: 0 },
    xAxis: { type: "category", data: rows.map((r) => r.aspsp_name || r.name || "?"), axisLabel: axisText, axisLine: { lineStyle: { color: T.line } } },
    yAxis: { type: "value", axisLabel: { ...axisText, formatter: moneyK, hideOverlap: true }, splitLine: { lineStyle: { color: T.line } } },
    series: [
      { name: "Income", type: "bar", data: rows.map((r) => r.income || 0), itemStyle: { color: GOOD, borderRadius: [3, 3, 0, 0] } },
      { name: "Expense", type: "bar", data: rows.map((r) => r.expense || 0), itemStyle: { color: CRIT, borderRadius: [3, 3, 0, 0] } },
    ],
  }, true);
}

// --- bottom: list or grouped ---------------------------------------------
function renderBottom() {
  const mode = $("groupby").value;
  $("density").hidden = mode !== "list";
  if (mode === "list") { $("table").dataset.mode = "list"; loadList(false); }
  else loadGrouped(mode);
}

function catOptions(selName) {
  const groups = {};
  for (const c of CATS) (groups[c.group_name || "Other"] ||= []).push(c);
  return Object.entries(groups).map(([g, list]) =>
    `<optgroup label="${esc(g)}">` +
    list.map((c) => `<option value="${c.id}"${c.name === selName ? " selected" : ""}>${esc(c.name)}</option>`).join("") +
    `</optgroup>`).join("");
}

function setDensityLabel() {
  $("density").textContent = density === "compact" ? "⤢ Full view" : "⤡ Compact";
}

const catSelect = (t) =>
  `<select class="catsel" data-id="${esc(t.dedup_id)}">${catOptions(t.category)}</select>` +
  (t.category_source === "manual" ? '<span class="pin" title="Manually set">✎</span>' : "");

function rowMarkup(t) {
  const cls = t.signed_amount < 0 ? "neg" : "pos";
  const party = t.creditor_name || t.debtor_name || "";
  const color = t.category_color || OTHER;
  const uncat = !t.category || t.category === "Uncategorized";
  return `<tr class="${uncat ? "uncat" : ""}">
    <td>${t.booking_date || t.value_date || ""}</td>
    <td>${esc(t.remittance_information || "")}</td>
    <td>${esc(party)}</td>
    <td><span class="badge"><span class="dot" style="background:${color}"></span>${catSelect(t)}</span></td>
    <td class="amt ${cls}">${money2(t.signed_amount)}</td>
  </tr>`;
}

// Compact card: essentials on the face, full detail + classify on tap-to-expand.
function cardMarkup(t) {
  const cls = t.signed_amount < 0 ? "neg" : "pos";
  const party = t.creditor_name || t.debtor_name || "";
  const color = t.category_color || OTHER;
  const uncat = !t.category || t.category === "Uncategorized";
  const date = t.booking_date || t.value_date || "";
  const desc = t.remittance_information || party || "—";
  return `<div class="txn ${uncat ? "uncat" : ""}" data-id="${esc(t.dedup_id)}">
    <div class="txn-row">
      <span class="txn-caret">▸</span>
      <div class="txn-main">
        <div class="txn-desc">${esc(desc)}</div>
        <div class="txn-sub"><span class="dot" style="background:${color}"></span>${esc(t.category || "Uncategorized")} · ${date}</div>
      </div>
      <div class="txn-amt ${cls}">${money2(t.signed_amount)}</div>
    </div>
    <div class="txn-detail">
      <div><span class="lbl">Party</span> ${esc(party || "—")}</div>
      <div><span class="lbl">Description</span> ${esc(t.remittance_information || "—")}</div>
      <div><span class="lbl">Category</span> ${catSelect(t)}</div>
    </div>
  </div>`;
}

function wireList() {
  $("table").querySelectorAll(".catsel").forEach((s) => (s.onchange = onCategoryChange));
  $("table").querySelectorAll(".txn-row").forEach((r) =>
    (r.onclick = () => r.closest(".txn").classList.toggle("open")));
  // keep taps on the category picker from collapsing the card
  $("table").querySelectorAll(".txn-detail").forEach((d) =>
    (d.onclick = (e) => e.stopPropagation()));
}

async function loadList(append) {
  const data = await api("/api/transactions", { limit: PAGE, offset });
  total = data.total;
  const compact = density === "compact";
  const body = data.items.map(compact ? cardMarkup : rowMarkup).join("");
  if (append) {
    const host = $("table").querySelector(compact ? ".txns" : "tbody");
    host.insertAdjacentHTML("beforeend", body);
  } else if (compact) {
    $("table").innerHTML = `<div class="txns">${body}</div>`;
  } else {
    $("table").innerHTML =
      `<table><thead><tr><th>Date</th><th>Description</th><th>Party</th><th>Category</th><th class="amt">Amount</th></tr></thead><tbody>${body}</tbody></table>`;
  }
  offset += data.items.length;
  $("count").textContent = `${offset} of ${total}`;
  $("more").hidden = offset >= total;
  wireList();
}

async function onCategoryChange(e) {
  const sel = e.target;
  const dedup_id = sel.dataset.id;
  const name = sel.options[sel.selectedIndex].text;
  const res = await fetch(`/api/transactions/${encodeURIComponent(dedup_id)}/category`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category_id: +sel.value }),
  }).then((r) => r.json());
  if (res.applied > 0)
    toast(`Set <b>${esc(name)}</b> · also applied to <b>${res.applied}</b> similar`);
  else
    toast(`Set <b>${esc(name)}</b>`);
  refresh(); // recompute stats + list with the override + propagation applied
}

async function loadGrouped(mode) {
  const map = {
    category: ["/api/stats/by-category", "Category", (r) => r.category, (r) => r.category],
    merchant: ["/api/stats/by-merchant", "Merchant / payee", (r) => r.merchant, (r) => r.merchant],
    account: ["/api/stats/by-account", "Account", (r) => r.account_key, (r) => r.aspsp_name || r.name],
    month: ["/api/stats/by-month", "Month", (r) => r.month, (r) => r.month],
  }[mode];
  const [path, title, keyFn, labelFn] = map;
  let rows = await api(path, mode === "merchant" ? { limit: 100 } : {});
  const val = (r) => mode === "month" ? (r.expense || 0) : r.total;
  $("more").hidden = true;
  $("count").textContent = `${rows.length} groups`;
  const body = rows.map((r) => {
    const color = mode === "category" ? (catColor[r.category] || OTHER) : T.accent;
    return `<tr class="click" data-mode="${mode}" data-key="${esc(keyFn(r) ?? "")}">
      <td><span class="badge"><span class="dot" style="background:${color}"></span>${esc(labelFn(r) || "—")}</span></td>
      <td class="muted">${r.count ?? ""}</td>
      <td class="amt neg">${money2(val(r))}</td></tr>`;
  }).join("");
  $("table").innerHTML =
    `<table><thead><tr><th>${title}</th><th>Count</th><th class="amt">Total</th></tr></thead><tbody>${body}</tbody></table>`;
  $("table").querySelectorAll("tr.click").forEach((tr) => (tr.onclick = () => drill(tr.dataset.mode, tr.dataset.key)));
}

// Clicking a group drills into it: set the matching filter and show the list.
function drill(mode, key) {
  if (mode === "category") {
    CAT.set([key]);
  } else if (mode === "merchant") {
    $("q").value = key;
  } else if (mode === "account") {
    ACC.set([key]);
  } else if (mode === "month") {
    $("preset").value = "custom";
    document.querySelectorAll(".custom-dates").forEach((e) => (e.hidden = false));
    $("from").value = `${key}-01`;
    const [y, m] = key.split("-").map(Number);
    $("to").value = new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10);
  }
  $("groupby").value = "list";
  refresh();
}

init();
