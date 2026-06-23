/* CrowdWise frontend — fetches forecasts, renders the map + ledger, handles upload. */
"use strict";

let MAP = null, MARKERS = null, CURRENT = null;
const $ = id => document.getElementById(id);
const fmtCause = c => (c || "").replace(/_/g, " ");

/* ---------------- data loading ---------------- */
async function loadCurrent() {
  spin(true);
  try {
    const r = await fetch("api/events/current");
    if (r.status === 503) { setSource("warming up — training model…"); setTimeout(loadCurrent, 2500); return; }
    if (!r.ok) throw new Error(`server ${r.status}`);
    render(await r.json());
  } catch (e) {
    toast("Could not load forecast: " + e.message, true);
  } finally { spin(false); }
}

async function refresh() {
  spin(true);
  try {
    const r = await fetch("api/scrape", { method: "POST" });
    if (!r.ok) throw new Error((await r.json()).detail || `server ${r.status}`);
    render(await r.json());
    toast("Forecast refreshed");
  } catch (e) {
    toast("Refresh failed: " + e.message, true);
  } finally { spin(false); }
}

async function upload(file) {
  spin(true);
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("api/predict", { method: "POST", body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || `server ${r.status}`);
    render(d);
    toast(`Forecast for ${file.name} (${d.meta.event_count} events)`);
  } catch (e) {
    toast("Upload failed: " + e.message, true);
  } finally { spin(false); }
}

/* ---------------- rendering ---------------- */
function render(view) {
  CURRENT = view;
  fillHeader(view);
  renderPool(view);
  renderLedger(view);
  renderMap(view);
  setSource(view.meta);
}

function fmtDate(d) {
  const m = String(d).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return d;
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${+m[3]} ${months[+m[2]-1]} ${m[1]}`;
}
function fillHeader(v) {
  $("s-pool").textContent = v.pool;
  $("s-choke").textContent = v.total_chokepoints;
  $("s-planned").textContent = v.planned != null ? v.planned : "—";
  $("s-closures").textContent = v.closures != null ? v.closures : "—";
}
function setSource(meta) {
  const el = $("source");
  if (typeof meta === "string") { el.textContent = meta; el.className = "source-badge"; return; }
  const src = (meta && meta.source) || "";
  const human = src === "upload" ? "uploaded file"
    : src === "scrape:predicthq" ? "live events (PredictHQ)"
    : src === "scrape:cache" ? "sample event calendar"
    : src;
  const when = meta && meta.generated_at ? meta.generated_at.replace("T", " ").replace("Z", " UTC") : "";
  el.innerHTML = `source: <b>${human}</b>${when ? " · " + when : ""}`;
  el.className = "source-badge" + (src === "upload" ? " upload" : "");
}

function renderPool(v) {
  const used = v.assignments.reduce((s, a) => s + a.officers, 0);
  const bar = $("poolbar"); bar.innerHTML = "";
  for (let p = 0; p < v.pool; p++) {
    const pip = document.createElement("span");
    pip.className = "pip" + (p < used ? " on" : "");
    bar.appendChild(pip);
  }
  $("poolnote").textContent = used > v.pool
    ? `Over by ${used - v.pool}. Pull officers from a lower-priority chokepoint.`
    : `${used} of ${v.pool} officers committed · ${v.pool - used} in reserve.`;
}

function renderLedger(v) {
  const ledger = $("ledger"); ledger.innerHTML = "";
  v.assignments.forEach((a, i) => {
    const row = document.createElement("div");
    row.className = "row" + (a.officers === 0 ? " unfunded" : "");
    row.tabIndex = 0; row.setAttribute("role", "button"); row.setAttribute("aria-expanded", "false");
    row.dataset.i = i;
    a._rec = a.officers; // remember recommendation for override labelling

    const gutter = a.officers > 0
      ? `<div class="gutter">${'<span class="off-pip"></span>'.repeat(a.officers)}</div>`
      : `<div class="gutter empty">—</div>`;
    const analogRows = (a.analogs || []).map(an => `
      <div class="analog"><span class="ac">${fmtCause(an.cause)}</span>
      <span class="aa">${an.addr || "—"}</span><span class="aid">${an.id}</span></div>`).join("");
    const tight = (a.analogs || []).filter(an => an.cause === a.cause).length;
    const conf = tight >= 3 ? "<b>Strong historical support</b> — most analogs match this event type."
      : tight >= 1 ? "<b>Mixed historical support</b> — analogs span several types; weigh your own read."
      : "<b>Thin historical support</b> — few comparable past events. Operator judgment matters most.";
    const coords = (a.lat != null && a.lon != null) ? ` · ${a.lat.toFixed(4)}, ${a.lon.toFixed(4)}` : " · no map location";

    row.innerHTML = `
      <div class="rank">${String(a.rank).padStart(2, "0")}</div>
      ${gutter}
      <div class="where">
        <div class="cause">${fmtCause(a.cause)}${a.closure ? '<span class="tag-closure">CLOSURE</span>' : ''}</div>
        <div class="addr">${a.addr}</div>
        <div class="meta"><b>${a.corridor}</b> · onset ${a.start} · ${a.type}${coords}</div>
      </div>
      <div class="score"><div class="n">${Math.round(a.score)}</div>
        <div class="formula">${Math.round(a.predicted_delay)} min × ${a.exposure.toFixed(1)}</div></div>
      <div class="detail"><div class="detail-inner">
        <p class="why">${conf} Predicted to hold traffic <b>${Math.round(a.predicted_delay)} minutes</b> at peak; exposure on <b>${a.corridor}</b> rated <b>${a.exposure.toFixed(1)}×</b>.</p>
        <div class="analogs-label">Built from these past events</div>
        <div class="analog-grid">${analogRows}</div>
        <div class="override">
          <label>Officers on the ground</label>
          <div class="stepper">
            <button type="button" data-d="-1" aria-label="Remove officer">−</button>
            <span class="val">${a.officers}</span>
            <button type="button" data-d="1" aria-label="Add officer">+</button>
          </div>
          <span class="ov-state">Recommended</span>
        </div>
      </div></div>`;

    row.addEventListener("click", e => {
      if (e.target.closest(".override")) return;
      if (e.target.closest(".stepper")) return;
      const open = row.getAttribute("aria-expanded") === "true";
      row.setAttribute("aria-expanded", String(!open));
      if (!open && a.lat != null && a.lon != null && MAP) MAP.panTo([a.lat, a.lon]);
    });
    row.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); row.click(); }
    });
    // override steppers
    row.querySelectorAll("button[data-d]").forEach(btn => btn.addEventListener("click", ev => {
      ev.stopPropagation();
      const d = +btn.dataset.d;
      a.officers = Math.max(0, Math.min(6, a.officers + d));
      row.querySelector(".val").textContent = a.officers;
      const st = row.querySelector(".ov-state");
      if (a.officers !== a._rec) { st.textContent = `Override · was ${a._rec}`; st.classList.add("changed"); }
      else { st.textContent = "Recommended"; st.classList.remove("changed"); }
      const g = row.querySelector(".gutter");
      if (a.officers > 0) { g.className = "gutter"; g.innerHTML = '<span class="off-pip"></span>'.repeat(a.officers); row.classList.remove("unfunded"); }
      else { g.className = "gutter empty"; g.textContent = "—"; row.classList.add("unfunded"); }
      renderPool(CURRENT);
    }));
    ledger.appendChild(row);
  });
}

function renderMap(v) {
  if (!MAP) {
    MAP = L.map("map", { scrollWheelZoom: false });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      { attribution: "© OpenStreetMap contributors", maxZoom: 19 }).addTo(MAP);
  }
  if (MARKERS) MARKERS.remove();
  MARKERS = L.layerGroup().addTo(MAP);

  const pts = v.assignments.filter(a => a.lat != null && a.lon != null);
  const max = Math.max(1, ...pts.map(a => a.score));
  pts.forEach(a => {
    const hot = a.score / max;
    const radius = 9 + 20 * hot;
    const color = a.closure ? "#FF5A5F" : `hsl(${42 - 42 * hot}, 100%, 50%)`; // amber→red by score
    L.circleMarker([a.lat, a.lon], {
      radius, color, weight: 2, fillColor: color, fillOpacity: 0.5
    }).bindPopup(
      `<b>#${a.rank} ${fmtCause(a.cause)}</b><br>${a.addr}<br>` +
      `delay ${Math.round(a.predicted_delay)} min · score ${Math.round(a.score)}<br>` +
      `officers ${a.officers}${a.closure ? " · CLOSURE" : ""}`
    ).addTo(MARKERS);
  });

  if (pts.length) MAP.fitBounds(L.latLngBounds(pts.map(a => [a.lat, a.lon])).pad(0.25));
  else MAP.setView([12.97, 77.59], 11); // Bengaluru fallback (empty fitBounds throws)
  setTimeout(() => MAP.invalidateSize(), 100);
}

/* ---------------- ui helpers ---------------- */
function spin(on) { $("spinner").hidden = !on; }
let toastTimer = null;
function toast(msg, isErr) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = "toast"; }, 3800);
}

/* ---------------- wire up ---------------- */
$("refresh").addEventListener("click", refresh);
$("csv").addEventListener("change", e => { if (e.target.files[0]) upload(e.target.files[0]); e.target.value = ""; });
loadCurrent();
