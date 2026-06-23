/* CrowdWise frontend — fetches forecasts, renders the map + ledger, handles upload. */
"use strict";

let MAP = null, MARKERS = null, CURRENT = null;
const $ = id => document.getElementById(id);
const fmtCause = c => (c || "").replace(/_/g, " ");

/* severity in [0,1] -> color on a green -> amber -> red ramp */
function severityColor(t) {
  t = Math.max(0, Math.min(1, t));
  // 0 = green (140°), .5 = amber (45°), 1 = red (0°)
  const hue = t < 0.5 ? 140 - (140 - 45) * (t / 0.5) : 45 - 45 * ((t - 0.5) / 0.5);
  return `hsl(${hue}, 85%, 52%)`;
}

/* an SVG teardrop pin, tinted by severity, as a Leaflet divIcon */
function pinIcon(color) {
  const svg =
    `<svg width="26" height="38" viewBox="0 0 26 38" xmlns="http://www.w3.org/2000/svg">` +
    `<path d="M13 0C6 0 .5 5.4.5 12.2.5 21 13 38 13 38s12.5-17 12.5-25.8C25.5 5.4 20 0 13 0z" ` +
    `fill="${color}" stroke="#0E1418" stroke-width="1.5"/>` +
    `<circle cx="13" cy="12.2" r="4.6" fill="#0E1418"/></svg>`;
  return L.divIcon({
    html: svg, className: "cw-pin",
    iconSize: [26, 38], iconAnchor: [13, 38], popupAnchor: [0, -34],
  });
}

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
  renderLedger(view);   // seeds severity-based officer suggestions
  renderTally(view);    // sums what was suggested/assigned
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

/* Suggested officers per chokepoint, scaled to severity. No global pool — the
   operator assigns however many each chokepoint warrants. */
function suggestOfficers(sev, closure) {
  if (closure || sev >= 0.66) return 3;   // high severity / closure
  if (sev >= 0.33) return 2;               // medium
  return 1;                                // low
}

/* Running tally of officers the operator has assigned (informational, no cap). */
function renderTally(v) {
  const total = v.assignments.reduce((s, a) => s + a.officers, 0);
  $("poolnote").textContent =
    `${total} officer${total === 1 ? "" : "s"} assigned across ${v.assignments.length} chokepoints · adjust per chokepoint by severity.`;
}

function renderLedger(v) {
  const ledger = $("ledger"); ledger.innerHTML = "";
  const scores = v.assignments.map(a => a.score);
  const sMax = Math.max(1, ...scores), sMin = Math.min(...scores, 0);
  const sSpan = Math.max(1, sMax - sMin);
  v.assignments.forEach((a, i) => {
    const sev = (a.score - sMin) / sSpan;           // 0..1 within this forecast
    const barColor = a.closure ? "#FF5A5F" : severityColor(sev);
    const barPct = Math.round(18 + 82 * sev);        // floor so low bars still read
    // suggest officers by severity; operator adjusts freely (no pool ceiling)
    a.officers = suggestOfficers(sev, a.closure);
    a._rec = a.officers;                             // remember the suggestion
    const row = document.createElement("div");
    row.className = "row";
    row.tabIndex = 0; row.setAttribute("role", "button"); row.setAttribute("aria-expanded", "false");
    row.dataset.i = i;

    const gutter = a.officers > 0
      ? `<div class="gutter">${'<span class="off-pip"></span>'.repeat(a.officers)}</div>`
      : `<div class="gutter empty">0</div>`;
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
      <div class="score">
        <div class="sevbar" title="Congestion severity"><span style="width:${barPct}%;background:${barColor}"></span></div>
        <div class="sevrow"><span class="n">${Math.round(a.score)}</span>
          <span class="formula">${Math.round(a.predicted_delay)} min × ${a.exposure.toFixed(1)}</span></div>
      </div>
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
          <span class="ov-state">Suggested ${a._rec}</span>
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
      a.officers = Math.max(0, Math.min(12, a.officers + d));
      row.querySelector(".val").textContent = a.officers;
      const st = row.querySelector(".ov-state");
      if (a.officers !== a._rec) { st.textContent = `Assigned · suggested ${a._rec}`; st.classList.add("changed"); }
      else { st.textContent = `Suggested ${a._rec}`; st.classList.remove("changed"); }
      const g = row.querySelector(".gutter");
      if (a.officers > 0) { g.className = "gutter"; g.innerHTML = '<span class="off-pip"></span>'.repeat(a.officers); }
      else { g.className = "gutter empty"; g.textContent = "0"; }
      renderTally(CURRENT);
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
  const scores = pts.map(a => a.score);
  const max = Math.max(1, ...scores), min = Math.min(...scores, 0);
  const span = Math.max(1, max - min);

  // Fan out events that share (almost) identical coordinates so each pin is
  // visible and clickable instead of stacking into one blob.
  const seen = new Map();
  const KEY = a => `${a.lat.toFixed(4)},${a.lon.toFixed(4)}`;
  pts.forEach(a => {
    const k = KEY(a);
    const n = seen.get(k) || 0;
    seen.set(k, n + 1);
    let lat = a.lat, lon = a.lon;
    if (n > 0) {                       // 2nd+ event at this venue: ring it outward
      const ang = (n * 2.399);         // golden-angle spread, avoids overlap
      const rad = 0.012 + 0.004 * n;   // ~1.3km steps
      lat += rad * Math.cos(ang);
      lon += rad * Math.sin(ang) / Math.cos(a.lat * Math.PI / 180);
    }
    const sev = (a.score - min) / span;            // 0..1 within this forecast
    const color = a.closure ? "#FF5A5F" : severityColor(sev);
    L.marker([lat, lon], { icon: pinIcon(color), riseOnHover: true })
      .bindPopup(
        `<b>#${a.rank} ${fmtCause(a.cause)}</b><br>${a.addr}<br>` +
        `delay ${Math.round(a.predicted_delay)} min · score ${Math.round(a.score)}<br>` +
        `officers ${a.officers}${a.closure ? " · CLOSURE" : ""}`
      ).addTo(MARKERS);
  });

  // Fit the map to the DENSE cluster, ignoring far-flung outliers — one event
  // 300 km away shouldn't force a whole-state zoom that collapses the city pins
  // into a blob. Center on the median point; include points within ~60 km of it.
  if (pts.length) {
    const med = arr => { const s = [...arr].sort((a, b) => a - b); return s[Math.floor(s.length / 2)]; };
    const cLat = med(pts.map(a => a.lat)), cLon = med(pts.map(a => a.lon));
    const near = pts.filter(a =>
      Math.hypot(a.lat - cLat, (a.lon - cLon) * Math.cos(cLat * Math.PI / 180)) < 0.6); // ~60km
    const fit = (near.length ? near : pts).map(a => [a.lat, a.lon]);
    MAP.fitBounds(L.latLngBounds(fit).pad(0.25), { maxZoom: 13 }); // cap so it never over/under-zooms
  } else {
    MAP.setView([12.97, 77.59], 11); // Bengaluru fallback (empty fitBounds throws)
  }
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
