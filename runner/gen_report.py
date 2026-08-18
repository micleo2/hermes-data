#!/usr/bin/env python3
"""Generate a self-contained interactive HTML report from results/*.json.

One page, no build step, no CDN: the data is embedded, so the file works
offline and can be opened straight from disk or served from GitHub Pages.

Standard library only -- no third-party imports -- so it runs anywhere python3
does, including the benchmark Pi.

Clicking a data point opens the Hermes commit it measures.

This lives on the `main` branch at runner/gen_report.py, while the results it
reads live on the orphan `data` branch. Both are checked out as sibling
worktrees (see README), so the defaults resolve across them:

    <repo>/main/runner/gen_report.py  ->  <repo>/data/results/*.json
                                      ->  <repo>/data/index.html

Usage:
    python3 main/runner/gen_report.py           # -> data/index.html
    python3 main/runner/gen_report.py --open    # write, then open in the browser
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import webbrowser

COMMIT_URL = "https://github.com/facebook/hermes/commit/{sha}"

MIB = 1024 * 1024

# key, title, unit, scale factor, decimals. Order is panel order; the first nine
# are the "headline" set the page shows by default.
METRICS = [
    ("totalTime", "Total time", "s", 1, 2),
    ("totalCPUTime", "Total CPU time", "s", 1, 2),
    ("perfEvent_instructions", "Instructions", "G", 1e9, 2),
    ("perfEvent_cpu-cycles", "CPU cycles", "G", 1e9, 2),
    ("peakRSS", "Peak RSS", "MiB", MIB, 0),
    ("peakAllocatedBytes", "Peak allocated", "MiB", MIB, 1),
    ("totalGCTime", "Total GC time", "s", 1, 3),
    ("maxGCPause", "Max GC pause", "ms", 1e-3, 1),
    ("avgGCPause", "Avg GC pause", "ms", 1e-3, 2),
    ("numCollections", "GC collections", "", 1, 0),
    # Shown by the "All" toggle:
    ("totalGCCPUTime", "Total GC CPU time", "s", 1, 3),
    ("heapSize", "Heap size", "MiB", MIB, 0),
    ("finalHeapSize", "Final heap size", "MiB", MIB, 0),
    ("externalBytes", "External memory", "MiB", MIB, 1),
    ("perfEvent_L1-dcache-load-misses", "L1 dcache load misses", "M", 1e6, 1),
    ("perfEvent_L1-icache-load-misses", "L1 icache load misses", "M", 1e6, 1),
    ("perfEvent_major-faults", "Major faults", "", 1, 0),
]
HEADLINE_COUNT = 10

# A metric can be absent from a result: it may predate the engine emitting it
# (externalBytes) or the runner extracting it (avgGCPause). Those points are
# null in the embedded data and the page draws a gap rather than a zero.


def load_runs(results_dir: pathlib.Path) -> list[dict]:
    """Flatten perfEvents into the top level and sort chronologically."""
    runs = []
    for path in sorted(results_dir.glob("*.json")):
        with path.open() as fh:
            raw = json.load(fh)
        run = {k: v for k, v in raw.items() if k != "perfEvents"}
        run.update(raw.get("perfEvents", {}))
        run["date"] = dt.datetime.strptime(raw["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
        runs.append(run)
    runs.sort(key=lambda r: r["date"])
    return runs

CSS = """
:root {
  color-scheme: light;
  --surface: #fcfcfb;
  --page: #f9f9f7;
  --series: #2a78d6;
  --primary: #0b0b0b;
  --secondary: #52514e;
  --muted: #898781;
  --grid: #e1e0d9;
  --baseline: #c3c2b7;
  --border: rgba(11,11,11,0.10);
  --ghost: rgba(11,11,11,0.05);
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface: #1a1a19;
  --page: #0d0d0d;
  --series: #3987e5;
  --primary: #ffffff;
  --secondary: #c3c2b7;
  --muted: #898781;
  --grid: #2c2c2a;
  --baseline: #383835;
  --border: rgba(255,255,255,0.10);
  --ghost: rgba(255,255,255,0.06);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 28px 32px 64px;
  background: var(--page);
  color: var(--primary);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
h1 { font-size: 22px; font-weight: 600; margin: 0 0 4px; letter-spacing: -0.01em; }
.sub { color: var(--secondary); font-size: 13px; margin: 0 0 20px; }
.controls {
  display: flex; flex-wrap: wrap; gap: 8px 20px; align-items: center;
  margin-bottom: 22px; padding-bottom: 18px; border-bottom: 1px solid var(--border);
}
.group { display: flex; align-items: center; gap: 6px; }
.group > .lbl { color: var(--muted); font-size: 12px; margin-right: 2px; }
button {
  font: inherit; font-size: 12.5px; color: var(--secondary);
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 7px; padding: 4px 10px; cursor: pointer;
}
button:hover { background: var(--ghost); }
button[aria-pressed="true"] { color: var(--primary); border-color: var(--baseline); font-weight: 600; }
button:focus-visible, .hit:focus-visible { outline: 2px solid var(--series); outline-offset: 2px; }
#grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(370px, 1fr)); gap: 18px; }
figure {
  margin: 0; padding: 12px 12px 6px; background: var(--surface);
  border: 1px solid var(--border); border-radius: 10px;
}
figcaption { font-size: 13px; font-weight: 500; margin: 2px 0 2px 4px; }
svg { display: block; width: 100%; height: auto; overflow: visible; }
.gridline, .zeroline { stroke: var(--grid); stroke-width: 1; vector-effect: non-scaling-stroke; }
.median { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 4 3; vector-effect: non-scaling-stroke; }
.series { fill: none; stroke: var(--series); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
.endpoint { fill: var(--series); stroke: var(--surface); stroke-width: 2; }
.cursor { fill: var(--series); stroke: var(--surface); stroke-width: 2; }
.crosshair { stroke: var(--baseline); stroke-width: 1; vector-effect: non-scaling-stroke; }
.tick { fill: var(--muted); font-size: 9.5px; font-variant-numeric: tabular-nums; }
.endlabel { fill: var(--secondary); font-size: 9.5px; }
.hit { fill: transparent; cursor: pointer; }
#tip {
  position: fixed; z-index: 10; pointer-events: none; opacity: 0;
  transition: opacity .08s; min-width: 170px; padding: 9px 11px;
  background: var(--surface); border: 1px solid var(--border); border-radius: 9px;
  box-shadow: 0 6px 22px rgba(0,0,0,.13);
}
#tip .v { font-size: 17px; font-weight: 600; letter-spacing: -0.01em; }
#tip .m { color: var(--secondary); font-size: 12px; margin-bottom: 5px; }
#tip .r { color: var(--secondary); font-size: 11.5px; display: flex; justify-content: space-between; gap: 14px; }
#tip .sha { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
#tip .hint { color: var(--muted); font-size: 10.5px; margin-top: 6px; padding-top: 5px; border-top: 1px solid var(--border); }
#tableWrap { margin-top: 26px; overflow: auto; max-height: 70vh; }
table { border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
th, td { padding: 5px 11px; text-align: right; white-space: nowrap; border-bottom: 1px solid var(--border); }
th { position: sticky; top: 0; background: var(--page); text-align: right; font-weight: 600; color: var(--secondary); }
td:first-child, th:first-child, td:nth-child(2), th:nth-child(2) { text-align: left; }
td a { color: var(--series); text-decoration: none; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
td a:hover { text-decoration: underline; }
.vh { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
"""

JS = r"""
const $ = (s) => document.querySelector(s);
const W = 420, H = 232, PADL = 54, PADR = 46, PADT = 12, PADB = 26;
const PW = W - PADL - PADR, PH = H - PADT - PADB;
const SVGNS = "http://www.w3.org/2000/svg";

const state = { days: null, set: "headline", hover: null, panels: [] };

const fmt = (v, d) => v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

function niceTicks(lo, hi, target) {
  if (hi === lo) { const p = Math.abs(hi) || 1; lo -= p * 0.05; hi += p * 0.05; }
  const raw = (hi - lo) / target, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  const out = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) out.push(t);
  return out;
}

// Enough decimals that no two ticks print the same label (Peak RSS spans <1 MiB).
function decimalsFor(ticks, base) {
  let d = base;
  while (d < 6) {
    const seen = new Set(ticks.map((t) => t.toFixed(d)));
    if (seen.size === ticks.length) break;
    d++;
  }
  return d;
}

const median = (a) => {
  const s = [...a].sort((x, y) => x - y);
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
};

function el(tag, attrs, parent) {
  const n = document.createElementNS(SVGNS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(n);
  return n;
}

function activeIdx() {
  if (state.days === null) return DATA.commits.map((_, i) => i);
  const last = DATA.commits[DATA.commits.length - 1].t;
  const cut = last - state.days * 86400;
  return DATA.commits.map((c, i) => (c.t >= cut ? i : -1)).filter((i) => i >= 0);
}

function activeMetrics() {
  return state.set === "all" ? DATA.metrics : DATA.metrics.filter((m) => m.headline);
}

function buildPanel(metric, idx) {
  const raw = DATA.values[metric.key];
  // null = not reported for that commit. Keep the slot so every panel shares
  // one index space (the crosshair steps all panels by the same k).
  const vals = idx.map((i) => (raw[i] == null ? null : raw[i] / metric.scale));
  const times = idx.map((i) => DATA.commits[i].t);
  const known = vals.filter((v) => v !== null);
  if (!known.length) return null;  // never reported in this range -> no panel
  const med = median(known);
  const lo = Math.min(...known), hi = Math.max(...known);
  const pad = (hi - lo) * 0.18 || Math.abs(hi || 1) * 0.02;
  const y0 = lo - pad, y1 = hi + pad;
  const t0 = Math.min(...times), t1 = Math.max(...times);
  const X = (t) => PADL + (t1 === t0 ? PW / 2 : ((t - t0) / (t1 - t0)) * PW);
  const Y = (v) => PADT + PH - ((v - y0) / (y1 - y0)) * PH;

  const fig = document.createElement("figure");
  const cap = document.createElement("figcaption");
  cap.textContent = metric.unit ? metric.title + " (" + metric.unit + ")" : metric.title;
  fig.appendChild(cap);

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "presentation" }, fig);
  const ticks = niceTicks(y0, y1, 4).filter((t) => t >= y0 && t <= y1);
  const dec = decimalsFor(ticks, metric.decimals);
  for (const t of ticks) {
    el("line", { class: "gridline", x1: PADL, x2: PADL + PW, y1: Y(t), y2: Y(t) }, svg);
    const lab = el("text", { class: "tick", x: PADL - 8, y: Y(t) + 3.2, "text-anchor": "end" }, svg);
    lab.textContent = fmt(t, dec);
  }

  el("line", { class: "median", x1: PADL, x2: PADL + PW, y1: Y(med), y2: Y(med) }, svg);

  for (const [frac, ti] of [[0, 0], [0.5, 1], [1, 2]]) {
    const t = t0 + (t1 - t0) * frac;
    const lab = el("text", {
      class: "tick", x: X(t), y: H - 8,
      "text-anchor": ti === 0 ? "start" : ti === 2 ? "end" : "middle",
    }, svg);
    lab.textContent = new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  // Break the line at gaps rather than interpolating across missing commits.
  const seg = [];
  let pen = false;
  vals.forEach((v, k) => {
    if (v === null) { pen = false; return; }
    seg.push((pen ? "L" : "M") + X(times[k]) + " " + Y(v));
    pen = true;
  });
  el("path", { class: "series", d: seg.join(" ") }, svg);

  const lastK = vals.reduce((acc, v, k) => (v === null ? acc : k), -1);
  // A lone point between two gaps has no line to sit on, and a one-point path
  // draws nothing -- give it a dot so sparse metrics are visible.
  vals.forEach((v, k) => {
    if (v === null || k === lastK) return;
    const alone = (k === 0 || vals[k - 1] === null) && vals[k + 1] === null;
    if (alone) el("circle", { class: "endpoint", cx: X(times[k]), cy: Y(v), r: 3 }, svg);
  });
  el("circle", { class: "endpoint", cx: X(times[lastK]), cy: Y(vals[lastK]), r: 4 }, svg);
  const endLab = el("text", {
    class: "endlabel", x: X(times[lastK]) + 7, y: Y(vals[lastK]) - 7,
  }, svg);
  endLab.textContent = fmt(vals[lastK], dec);

  const cross = el("line", { class: "crosshair", y1: PADT, y2: PADT + PH, visibility: "hidden" }, svg);
  const cursor = el("circle", { class: "cursor", r: 5, visibility: "hidden" }, svg);

  // The hit area is the whole plot: the pointer only has to be nearest in X,
  // never land on a 2px line.
  const hit = el("rect", {
    class: "hit", x: PADL, y: PADT, width: PW, height: PH, tabindex: "0", role: "button",
    "aria-label": cap.textContent + ": " + vals.length + " commits. Arrow keys to step, Enter to open on GitHub.",
  }, svg);

  const panel = { metric, idx, vals, times, X, Y, dec, cross, cursor, hit, svg };

  const nearest = (clientX) => {
    const box = svg.getBoundingClientRect();
    const vx = ((clientX - box.left) / box.width) * W;
    let best = 0, bd = Infinity;
    times.forEach((t, k) => { const d = Math.abs(X(t) - vx); if (d < bd) { bd = d; best = k; } });
    return best;
  };

  hit.addEventListener("pointermove", (e) => setHover(panel, nearest(e.clientX), e.clientX, e.clientY));
  hit.addEventListener("pointerleave", clearHover);
  hit.addEventListener("click", (e) => openAt(nearest(e.clientX)));
  hit.addEventListener("focus", () => {
    const k = state.hover ? state.hover.k : vals.length - 1;
    const box = svg.getBoundingClientRect();
    setHover(panel, k, box.left + (X(times[k]) / W) * box.width, box.top + box.height / 2);
  });
  hit.addEventListener("blur", clearHover);
  hit.addEventListener("keydown", (e) => {
    const cur = state.hover ? state.hover.k : vals.length - 1;
    let k = cur;
    if (e.key === "ArrowRight") k = Math.min(vals.length - 1, cur + 1);
    else if (e.key === "ArrowLeft") k = Math.max(0, cur - 1);
    else if (e.key === "Home") k = 0;
    else if (e.key === "End") k = vals.length - 1;
    else if (e.key === "Enter" || e.key === " ") { openAt(cur); e.preventDefault(); return; }
    else return;
    e.preventDefault();
    const box = svg.getBoundingClientRect();
    setHover(panel, k, box.left + (X(times[k]) / W) * box.width, box.top + box.height / 2);
  });

  state.panels.push(panel);
  return fig;
}

function openAt(k) {
  const i = state.panels[0].idx[k];
  window.open(DATA.commits[i].url, "_blank", "noopener");
}

function setHover(active, k, clientX, clientY) {
  state.hover = { k };
  for (const p of state.panels) {
    const x = p.X(p.times[k]);
    p.cross.setAttribute("x1", x); p.cross.setAttribute("x2", x);
    p.cross.setAttribute("visibility", "visible");
    // No dot where the metric wasn't reported; the crosshair still lines up.
    if (p.vals[k] === null) {
      p.cursor.setAttribute("visibility", "hidden");
    } else {
      p.cursor.setAttribute("cx", x); p.cursor.setAttribute("cy", p.Y(p.vals[k]));
      p.cursor.setAttribute("visibility", "visible");
    }
  }
  const c = DATA.commits[active.idx[k]];
  const v = active.vals[k];
  const tip = $("#tip");
  // Compare against the most recent commit that actually reported the metric.
  let prev = null;
  for (let j = k - 1; j >= 0; j--) {
    if (active.vals[j] !== null) { prev = active.vals[j]; break; }
  }
  let delta = "first in range";
  if (v !== null && prev !== null) {
    const d = v - prev;
    const pct = prev !== 0 ? (d / prev) * 100 : 0;
    delta = (d >= 0 ? "+" : "−") + fmt(Math.abs(d), active.dec) +
      (prev !== 0 ? "  (" + (d >= 0 ? "+" : "−") + Math.abs(pct).toFixed(2) + "%)" : "");
  }
  tip.replaceChildren();
  const mk = (cls, text) => { const n = document.createElement("div"); n.className = cls; n.textContent = text; return n; };
  const unit = active.metric.unit ? " " + active.metric.unit : "";
  tip.appendChild(mk("v", v === null ? "—" : fmt(v, active.dec) + unit));
  tip.appendChild(mk("m", v === null
    ? active.metric.title + " · not reported for this commit"
    : active.metric.title));
  const row = (label, value, mono) => {
    const r = document.createElement("div"); r.className = "r";
    const a = document.createElement("span"); a.textContent = label;
    const b = document.createElement("span"); b.textContent = value;
    if (mono) b.className = "sha";
    r.append(a, b); return r;
  };
  tip.appendChild(row("commit", c.sha.slice(0, 12), true));
  tip.appendChild(row("date", new Date(c.t * 1000).toISOString().replace("T", " ").replace(".000Z", "Z")));
  tip.appendChild(row("vs prev", delta));
  tip.appendChild(mk("hint", "Click to open this commit on GitHub"));
  tip.style.opacity = "1";
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(clientX + 16, window.innerWidth - r.width - 10) + "px";
  tip.style.top = Math.max(8, Math.min(clientY + 16, window.innerHeight - r.height - 10)) + "px";
  $("#live").textContent = active.metric.title + " " +
    (v === null ? "not reported" : fmt(v, active.dec) + unit) + ", commit " + c.sha.slice(0, 12);
}

function clearHover() {
  $("#tip").style.opacity = "0";
  for (const p of state.panels) {
    p.cross.setAttribute("visibility", "hidden");
    p.cursor.setAttribute("visibility", "hidden");
  }
}

function renderTable(idx, metrics) {
  const t = document.createElement("table");
  const head = document.createElement("tr");
  for (const h of ["Commit", "Timestamp", ...metrics.map((m) => (m.unit ? m.title + " (" + m.unit + ")" : m.title))]) {
    const th = document.createElement("th"); th.textContent = h; head.appendChild(th);
  }
  t.appendChild(head);
  for (const i of [...idx].reverse()) {
    const c = DATA.commits[i], tr = document.createElement("tr");
    const td0 = document.createElement("td");
    const a = document.createElement("a");
    a.href = c.url; a.target = "_blank"; a.rel = "noopener"; a.textContent = c.sha.slice(0, 12);
    td0.appendChild(a); tr.appendChild(td0);
    const td1 = document.createElement("td"); td1.textContent = c.ts; tr.appendChild(td1);
    for (const m of metrics) {
      const td = document.createElement("td");
      const raw = DATA.values[m.key][i];
      td.textContent = raw == null ? "—" : fmt(raw / m.scale, m.decimals);
      tr.appendChild(td);
    }
    t.appendChild(tr);
  }
  const wrap = $("#tableWrap");
  wrap.replaceChildren(t);
}

function render() {
  const idx = activeIdx(), metrics = activeMetrics();
  state.panels = [];
  state.hover = null;
  const grid = $("#grid");
  // buildPanel returns null for a metric with no data in this range.
  grid.replaceChildren(...metrics.map((m) => buildPanel(m, idx)).filter(Boolean));
  renderTable(idx, metrics);
  $("#count").textContent = idx.length + " commits";
}

function wire() {
  for (const b of document.querySelectorAll("[data-days]")) {
    b.addEventListener("click", () => {
      state.days = b.dataset.days === "all" ? null : +b.dataset.days;
      document.querySelectorAll("[data-days]").forEach((o) => o.setAttribute("aria-pressed", o === b));
      render();
    });
  }
  for (const b of document.querySelectorAll("[data-set]")) {
    b.addEventListener("click", () => {
      state.set = b.dataset.set;
      document.querySelectorAll("[data-set]").forEach((o) => o.setAttribute("aria-pressed", o === b));
      render();
    });
  }
  const themeBtn = $("#theme");
  const apply = (dark) => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    themeBtn.textContent = dark ? "Light" : "Dark";
  };
  apply(window.matchMedia("(prefers-color-scheme: dark)").matches);
  themeBtn.addEventListener("click", () => apply(document.documentElement.dataset.theme !== "dark"));
  const tbl = $("#toggleTable");
  tbl.addEventListener("click", () => {
    const shown = $("#tableWrap").hasAttribute("hidden");
    $("#tableWrap").toggleAttribute("hidden", !shown);
    tbl.setAttribute("aria-pressed", shown);
    tbl.textContent = shown ? "Hide table" : "Show table";
  });
  window.addEventListener("resize", () => clearHover());
}

wire();
render();
"""

HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__</style>
</head>
<body>
<h1>__TITLE__</h1>
<p class="sub">__SUBTITLE__</p>
<div class="controls">
  <div class="group">
    <span class="lbl">Range</span>
    <button data-days="all" aria-pressed="true">All</button>
    <button data-days="30" aria-pressed="false">30d</button>
    <button data-days="14" aria-pressed="false">14d</button>
    <button data-days="7" aria-pressed="false">7d</button>
  </div>
  <div class="group">
    <span class="lbl">Metrics</span>
    <button data-set="headline" aria-pressed="true">Headline</button>
    <button data-set="all" aria-pressed="false">All</button>
  </div>
  <div class="group">
    <button id="toggleTable" aria-pressed="false">Show table</button>
    <button id="theme">Dark</button>
  </div>
  <div class="group"><span class="lbl" id="count"></span></div>
</div>
<main id="grid"></main>
<div id="tableWrap" hidden></div>
<div id="tip" role="tooltip"></div>
<div id="live" class="vh" aria-live="polite"></div>
<script>const DATA = __DATA__;</script>
<script>__JS__</script>
</body>
</html>
"""


def build_data(runs, url_template: str) -> dict:
    headline = {m[0] for m in METRICS[:HEADLINE_COUNT]}
    return {
        "commits": [
            {
                "sha": r["sha"],
                "ts": r["timestamp"],
                "t": int(r["date"].timestamp()),
                "url": url_template.format(sha=r["sha"]),
            }
            for r in runs
        ],
        "metrics": [
            {
                "key": key,
                "title": title,
                "unit": unit,
                "scale": scale,
                "decimals": decimals,
                "headline": key in headline,
            }
            for key, title, unit, scale, decimals in METRICS
        ],
        "values": {m[0]: [r.get(m[0]) for r in runs] for m in METRICS},
    }


def main(argv=None) -> int:
    # <repo>/main/runner/gen_report.py -> the data worktree is <repo>/data.
    data_worktree = pathlib.Path(__file__).resolve().parent.parent.parent / "data"
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--results-dir", type=pathlib.Path, default=data_worktree / "results")
    p.add_argument("--out", type=pathlib.Path, default=data_worktree / "index.html")
    p.add_argument(
        "--url-template",
        default=COMMIT_URL,
        help=f"commit URL for a data point; {{sha}} is substituted (default: {COMMIT_URL})",
    )
    p.add_argument("--open", action="store_true", help="open the report in a browser when done")
    args = p.parse_args(argv)

    if not args.results_dir.is_dir():
        p.error(f"no such results directory: {args.results_dir}")
    runs = load_runs(args.results_dir)
    if not runs:
        p.error(f"no JSON results found in {args.results_dir}")

    data = build_data(runs, args.url_template)

    span = f"{runs[0]['date']:%b %-d, %Y} – {runs[-1]['date']:%b %-d, %Y}"
    subtitle = (
        f"{len(runs)} commits · {span} · dashed line = median · "
        f"hover for the exact value, click to open the commit on GitHub"
    )
    html = (
        HTML.replace("__CSS__", CSS)
        .replace("__JS__", JS)
        .replace("__DATA__", json.dumps(data, separators=(",", ":")))
        .replace("__TITLE__", "Hermes benchmark performance")
        .replace("__SUBTITLE__", subtitle)
    )
    args.out.write_text(html, encoding="utf-8")
    print(f"wrote {args.out} ({len(runs)} commits, {len(METRICS)} metrics, {len(html) / 1024:.0f} KB)")
    if args.open:
        webbrowser.open(args.out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
