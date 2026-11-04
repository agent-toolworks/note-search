"use strict";
// note-search's view in toolworks-hub. Three tabs: Search, Measures (what the
// widgets showed before this page existed) and Notes (every note, as a table).
// A note opened from a search result or the table is read on the right,
// rendered from its Markdown by md.js. Every answer comes from note-search's
// hub-source (`run call <op>`), through the hub.
const SOURCE = new URLSearchParams(location.search).get("source") || "note-search";
const $ = (id) => document.getElementById(id);

let tab = "search", NOTES = null, notesSort = { key: "date", dir: -1 }, MEASURES = null;
let current = null, lastTerms = [], GROUPS = null, chartRange = "30d";
const byRef = new Map(), byStem = new Map();

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) if (c !== null && c !== undefined && c !== false)
    n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}
function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
const chip = (text, cls) => el("span", { class: "chip" + (cls ? " " + cls : "") }, text);
const fmtBytes = (n) => n == null ? "" : n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB";
const dateText = (d, src) => d ? (src === "modified" ? d + " (modified)" : d) : "";

async function api(op, args) {
  const url = "/api/sources/" + encodeURIComponent(SOURCE) + "/call/" + op +
    "?args=" + encodeURIComponent(JSON.stringify(args || {}));
  try {
    const r = await fetch(url, { credentials: "same-origin", cache: "no-store" });
    const j = await r.json().catch(() => ({ error: "the hub answered HTTP " + r.status + " with no JSON" }));
    if (!r.ok && !j.error) j.error = "HTTP " + r.status;
    if (j.ok === false && !j.error) j.error = "the source refused";
    return j;
  } catch (e) {
    return { error: "the hub did not answer: " + e.message };
  }
}
const failBox = (msg) => el("div", { class: "box bad" }, String(msg));

// ---- tabs and the address ---------------------------------------------------
function setTab(name, quiet) {
  tab = name;
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", b.dataset.tab === name ? "true" : "false");
  for (const t of ["search", "measures", "notes"]) $("tab-" + t).hidden = t !== name;
  $("layout").classList.toggle("reading", !!current && name !== "measures");
  $("reader").hidden = !current || name === "measures";
  if (name === "notes" && !NOTES) loadNotes();
  if (name === "notes" && NOTES) renderNotes();
  if (name === "measures" && !MEASURES) loadMeasures();
  if (name === "search" && !quiet) $("q").focus();
  saveHash();
}
function saveHash() {
  const p = new URLSearchParams();
  p.set("tab", tab);
  if ($("q").value) p.set("q", $("q").value);
  if (current) p.set("note", current);
  history.replaceState(null, "", "#" + p.toString());
}

// ---- notes list (for the table and for resolving links) ---------------------
async function loadNotes(force) {
  if (NOTES && !force) return NOTES;
  $("nstatus").textContent = "Reading the index…";
  const d = await api("notes", {});
  if (d.error) { $("nstatus").textContent = ""; $("ntable").replaceChildren(); $("nstatus").append(failBox(d.error)); return null; }
  NOTES = d.notes;
  byRef.clear(); byStem.clear();
  for (const n of NOTES) {
    byRef.set(n.ref, n);
    const k = n.group + "\u0000" + n.stem;
    if (!byStem.has(n.stem)) byStem.set(n.stem, []);
    byStem.get(n.stem).push(n);
    if (n.title) {
      const t = n.title.toLowerCase();
      if (!byStem.has(t)) byStem.set(t, []);
      if (t !== n.stem) byStem.get(t).push(n);
    }
  }
  const sel = $("ngroup");
  const keep = sel.value;
  sel.replaceChildren(el("option", { value: "" }, "All groups"), ...d.groups.map((g) => el("option", { value: g }, g)));
  sel.value = keep;
  $("nstatus").textContent = (d.warnings || []).join("; ");
  if (tab === "notes") renderNotes();
  return NOTES;
}

// Path sits under the title; the last three columns hide while a note is open.
const NOTE_COLS = [
  ["title", "Title"], ["group", "Group"], ["date", "Date"],
  ["entities", "Entities", "wide"], ["sections", "Sections", "num wide"], ["bytes", "Size", "num wide"],
];
function renderNotes() {
  const f = $("nfilter").value.trim().toLowerCase(), g = $("ngroup").value;
  let rows = NOTES.filter((n) => (!g || n.group === g) && (!f ||
    [n.title, n.path, n.group, (n.entities || []).join(" "), n.status || "", n.date || ""].join(" ").toLowerCase().includes(f)));
  const k = notesSort.key, dir = notesSort.dir;
  const val = (n) => k === "entities" ? (n.entities || []).join(", ") : (n[k] ?? "");
  rows = rows.slice().sort((a, b) => {
    const x = val(a), y = val(b);
    return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * dir;
  });
  $("ncount").textContent = rows.length + " of " + NOTES.length + " notes";
  const head = el("tr", null, NOTE_COLS.map(([key, title, cls]) => el("th", {
    class: cls, "aria-sort": key === k ? (dir > 0 ? "ascending" : "descending") : null,
    onclick: () => { notesSort = { key, dir: key === k ? -dir : (key === "date" || key === "bytes" || key === "sections" ? -1 : 1) }; renderNotes(); },
  }, title)));
  const shown = rows.slice(0, 2000);
  const body = shown.map((n) => el("tr", { "aria-current": n.ref === current ? "true" : null, onclick: () => openNote(n.ref) },
    el("td", null, el("span", { class: "tt" }, n.title || n.path), " ",
      n.stale ? chip("STALE" + (n.status ? " (" + n.status + ")" : ""), "stale") : null,
      (n.superseded_by || []).length ? chip("superseded", "super") : null,
      el("div", { class: "path" }, n.path)),
    el("td", null, chip(n.group, "group")),
    el("td", { class: "when" }, n.date || "", n.date_source === "modified" ? el("div", { class: "muted small" }, "file modified") : null),
    el("td", { class: "wide" }, (n.entities || []).slice(0, 4).map((e) => chip(e, "ent")), (n.entities || []).length > 4 ? " …" : ""),
    el("td", { class: "num wide" }, n.sections),
    el("td", { class: "num wide" }, fmtBytes(n.bytes))));
  $("ntable").replaceChildren(el("thead", null, head), el("tbody", null, body));
  if (rows.length > shown.length) $("ncount").textContent += " (first 2,000 shown; filter to see the rest)";
}

// ---- search -----------------------------------------------------------------
function chosenGroups() {
  return [...document.querySelectorAll("#sgroups input:checked")].map((i) => i.value);
}
function renderGroups(all, def) {
  if (GROUPS) return;
  GROUPS = all;
  let saved = null;
  try { saved = JSON.parse(store("ns.groups") || "null"); } catch (e) { saved = null; }
  const on = new Set((saved || def).filter((g) => all.includes(g)));
  if (!on.size) def.forEach((g) => on.add(g));
  $("sgroups").replaceChildren(el("span", null, "groups:"), ...all.map((g) => el("label", null,
    el("input", { type: "checkbox", value: g, checked: on.has(g) || null,
      onchange: () => { store("ns.groups", JSON.stringify(chosenGroups())); if ($("q").value.trim()) search(); } }),
    g + (def.includes(g) ? "" : " (opt-in)"))));
}
async function search() {
  const q = $("q").value.trim();
  saveHash();
  if (!q) { $("sout").replaceChildren(); $("sstatus").textContent = ""; return; }
  $("sstatus").textContent = "Searching…";
  const d = await api("search", { q, groups: GROUPS ? chosenGroups() : [] });
  if (d.groups) renderGroups(d.groups, d.default);
  if (d.error) { $("sstatus").textContent = ""; $("sout").replaceChildren(failBox(d.error)); return; }
  lastTerms = q.split(/[|\s]+/).filter((w) => w.length >= 3);
  const head = [];
  if (!d.notes.length) {
    head.push(el("div", { class: "box empty" }, "No note matches in " + d.searched.join(", ") +
      (d.nearest && d.nearest.length ? ". Nearest names: " + d.nearest.join(", ") : "")));
  } else if (!d.strict_found) {
    head.push(el("div", { class: "box warn" }, "No note has every word; these match some of them" +
      (d.nearest && d.nearest.length ? ". Nearest names: " + d.nearest.join(", ") : "")));
  }
  for (const w of d.warnings || []) head.push(el("div", { class: "box warn" }, w));
  $("sstatus").textContent = d.notes.length ? d.total + " note" + (d.total === 1 ? "" : "s") + " in " +
    d.searched.join(", ") + " · " + (d.stages || []).join("+") + " · " + d.ms + " ms" : "";
  const list = el("div", { class: "results" }, d.notes.map((n) => el("button", {
    type: "button", class: "result", "data-ref": n.ref, "aria-current": n.ref === current ? "true" : null,
    onclick: () => openNote(n.ref, { heading: n.section.includes("#") ? n.section.split("#").slice(1).join("#") : null, terms: lastTerms }),
  },
    el("div", { class: "rt" }, n.title || n.ref, chip(n.ref.split(":")[0], "group"),
      n.stale ? chip("STALE" + (n.status ? " (" + n.status + ")" : ""), "stale") : null,
      (n.superseded_by || []).length ? chip("superseded by " + n.superseded_by.join(", "), "super") : null,
      n.stage === "relaxed" ? chip("partial " + n.coverage.join("/")) : n.stage === "names" ? chip("name corrected") : null),
    el("div", { class: "rs" }, n.section + "  ·  L" + n.lines[0] + "-" + n.lines[1]),
    n.snippet ? el("div", { class: "rx" }, highlightText(n.snippet, lastTerms)) : null,
    el("div", { class: "rf" }, dateText(n.date, n.date_source),
      n.sections_matched > 1 ? " · +" + (n.sections_matched - 1) + " more sections" : "",
      (n.related || []).length ? " · related: " + n.related.join(", ") : ""))));
  $("sout").replaceChildren(...head, list);
}
function highlightText(text, terms) {
  const t = String(text).replace(/\s+/g, " ");
  if (!terms.length) return t;
  const rx = new RegExp("(" + terms.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "gi");
  return t.split(rx).map((part, i) => i % 2 ? el("mark", null, part) : part);
}

// ---- the reader -------------------------------------------------------------
function dirOf(path) { const i = path.lastIndexOf("/"); return i < 0 ? "" : path.slice(0, i + 1); }
function normPath(p) {
  const out = [];
  for (const part of p.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") out.pop(); else out.push(part);
  }
  return out.join("/");
}
function findWiki(target, group) {
  const name = target.split("/").pop().replace(/\.(md|markdown|txt)$/i, "").toLowerCase();
  const cands = byStem.get(name) || [];
  return cands.find((n) => n.group === group) || cands[0] || null;
}
function noteLink(ref, heading, kids, title) {
  return el("a", { title: title || ref, onclick: (e) => { e.preventDefault(); openNote(ref, { heading }); } }, kids);
}
function linkCtx(note) {
  const group = note.ref.split(":")[0], here = note.ref.slice(group.length + 1);
  return {
    firstLine: note.body_line,
    link(href, kids) {
      if (/^(https?|mailto):/i.test(href)) return el("a", { href, target: "_blank", rel: "noopener noreferrer" }, kids);
      if (href.startsWith("#")) {
        const id = MD.slug(decodeURIComponent(href.slice(1)));
        return el("a", { onclick: (e) => { e.preventDefault(); scrollToHeading(id); } }, kids);
      }
      if (/^[a-z][a-z0-9+.-]*:/i.test(href)) return el("span", { title: href }, kids);
      const [p, h] = href.split("#");
      const path = normPath(dirOf(here) + decodeURIComponent(p));
      for (const cand of [path, path + ".md"]) {
        const ref = group + ":" + cand;
        if (byRef.has(ref)) return noteLink(ref, h ? decodeURIComponent(h) : null, kids);
      }
      return el("a", { class: "missing", title: "not a note in " + group + ": " + path }, kids);
    },
    wiki(target, label) {
      const [name, h] = target.split("#");
      const n = name ? findWiki(name, group) : note;
      if (!n) return el("a", { class: "missing", title: "no note named " + name }, label);
      return noteLink(n.ref, h || null, label, n.ref);
    },
  };
}
function scrollToHeading(idOrText) {
  const body = $("rbody");
  let target = body.querySelector("#" + CSS.escape(idOrText));
  if (!target) {
    const want = String(idOrText).split("›").pop().trim().toLowerCase();
    target = [...body.querySelectorAll(".md-h")].find((h) => h.dataset.heading.toLowerCase() === want ||
      MD.slug(h.dataset.heading) === MD.slug(want));
  }
  for (const h of body.querySelectorAll(".md-h.target")) h.classList.remove("target");
  if (target) { target.classList.add("target"); target.scrollIntoView({ block: "start" }); }
  return !!target;
}
function markTerms(root, terms) {
  if (!terms || !terms.length) return;
  const rx = new RegExp(terms.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "gi");
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const t of nodes) {
    if (!rx.test(t.data)) continue;
    rx.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0, m;
    while ((m = rx.exec(t.data))) {
      frag.append(t.data.slice(last, m.index), el("mark", null, m[0]));
      last = m.index + m[0].length;
    }
    frag.append(t.data.slice(last));
    t.replaceWith(frag);
  }
}
function propValue(key, v) {
  const one = (x) => {
    const s = String(x);
    const w = /^\[\[([^\]]+)\]\]$/.exec(s.trim());
    if (w && current) {
      const n = findWiki(w[1].split("|")[0].split("#")[0], current.split(":")[0]);
      if (n) return noteLink(n.ref, null, w[1].split("|").pop(), n.ref);
    }
    if (/^https?:\/\//.test(s)) return el("a", { href: s, target: "_blank", rel: "noopener noreferrer" }, s);
    return s;
  };
  if (Array.isArray(v)) return v.map((x) => el("span", { class: "chip" }, one(x)));
  return one(v);
}
async function openNote(ref, opts) {
  opts = opts || {};
  current = ref;
  $("layout").classList.add("reading");
  $("reader").hidden = false;
  for (const b of document.querySelectorAll(".result")) b.setAttribute("aria-current", b.dataset.ref === ref ? "true" : "false");
  if (tab === "notes" && NOTES) renderNotes();
  saveHash();
  $("rtitle").textContent = ref;
  $("rref").textContent = ref;
  $("rmeta").replaceChildren();
  $("rtoc").replaceChildren();
  $("rbody").replaceChildren(el("p", { class: "muted" }, "Opening…"));
  if (!NOTES) await loadNotes();
  const d = await api("note", { ref });
  if (current !== ref) return;
  if (d.error) { $("rbody").replaceChildren(failBox(d.error)); return; }
  $("rtitle").textContent = d.title || ref;
  $("rref").textContent = d.ref;
  $("ropen").href = "vscode://file" + encodeURI(d.path);
  // properties and flags
  const fm = d.frontmatter || {};
  const keys = Object.keys(fm);
  const meta = [];
  if (keys.length) meta.push(el("table", { class: "props" }, el("tbody", null,
    keys.map((k) => el("tr", null, el("th", null, k), el("td", null, propValue(k, fm[k])))))));
  if (d.frontmatter_error) meta.push(el("div", { class: "box warn" }, "The frontmatter could not be read: " + d.frontmatter_error));
  const flags = [chip(dateText(d.date, d.date_source))];
  if (d.stale) flags.push(chip("STALE" + (d.status ? " (" + d.status + ")" : ""), "stale"));
  else if (d.status) flags.push(chip(d.status));
  if ((d.superseded_by || []).length) flags.push(el("span", null, "superseded by ",
    d.superseded_by.map((r, i) => [i ? ", " : "", noteLink(r, null, r)])));
  if (d.supersedes) flags.push(el("span", null, "supersedes ", byRef.has(d.supersedes) ? noteLink(d.supersedes, null, d.supersedes) : d.supersedes));
  if ((d.related || []).length) flags.push(el("span", { class: "muted" }, "related: ",
    d.related.map((r, i) => [i ? ", " : "", noteLink(r, null, r.split(":").slice(1).join(":"), r)])));
  meta.push(el("div", { class: "flags" }, flags));
  $("rmeta").replaceChildren(...meta);
  // body
  const body = MD.renderMarkdown(d.body, linkCtx(d));
  $("rbody").replaceChildren(body);
  if (d.cut) $("rbody").append(el("div", { class: "box warn cut" }, "This note is over 2 MB; only its first 2 MB are shown."));
  markTerms($("rbody"), opts.terms);
  const heads = [...$("rbody").querySelectorAll("h1.md-h, h2.md-h, h3.md-h")];
  if (heads.length > 2) $("rtoc").replaceChildren(...heads.map((h) => el("a", {
    class: "l" + h.tagName[1], onclick: () => scrollToHeading(h.id) }, h.textContent)));
  $("reader").scrollTop = 0;
  const heading = opts.heading || d.heading;
  if (heading) scrollToHeading(heading);
}
function closeNote() {
  current = null;
  $("layout").classList.remove("reading");
  $("reader").hidden = true;
  for (const b of document.querySelectorAll(".result")) b.setAttribute("aria-current", "false");
  if (tab === "notes" && NOTES) renderNotes();
  saveHash();
}

// ---- measures -----------------------------------------------------------------
async function loadMeasures() {
  $("mstatus").textContent = "Reading the search log…";
  const d = await api("measures", {});
  $("mstatus").textContent = "";
  if (d.error) { $("mout").replaceChildren(failBox(d.error)); return; }
  MEASURES = d.widgets;
  renderMeasures();
}
const TITLES = {
  "searches": "Searches, last 7 days", "open-rate": "Searches whose hit was opened",
  "again-rate": "Searched again within 2 minutes", "per-day": "Searches per day, by group",
  "measures": "Did searches work?", "no-match": "Searches with no strict match", "groups": "Groups",
};
function cell(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "object" && v.ref === "session") return el("a", { class: "session", title: "open the session in the hub",
    onclick: () => { try { window.top.location.hash = "#/entity/session/" + encodeURIComponent(v.id); } catch (e) { /* not framed */ } } },
    v.label || v.id.slice(0, 8));
  if (typeof v === "object" && v.link) return el("a", { href: v.link, target: "_blank", rel: "noopener noreferrer" }, v.label || v.link);
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}
function renderTable(data) {
  return el("div", { class: "tablewrap" }, el("table", null,
    el("thead", null, el("tr", null, data.columns.map((c) => el("th", null, c.title)))),
    el("tbody", null, data.rows.length ? data.rows.map((r) => el("tr", null, data.columns.map((c) =>
      el("td", null, cell(r[c.id])))))
      : el("tr", null, el("td", { colspan: data.columns.length, class: "muted" }, "nothing yet")))));
}
function renderChart(data) {
  const days = { "7d": 7, "30d": 30, "90d": 90 }[chartRange];
  const series = data.series.map((s, i) => ({ name: s.name, cls: "c" + (i % 6 + 1), points: s.points.slice(-days) }));
  const n = series.length ? series[0].points.length : 0;
  const totals = Array.from({ length: n }, (_, k) => series.reduce((a, s) => a + (s.points[k].v || 0), 0));
  const max = Math.max(1, ...totals);
  const W = 800, H = 160, L = 28, B = 18, bw = (W - L) / Math.max(1, n);
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.setAttribute("preserveAspectRatio", "none");
  const add = (tag, attrs, text) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text) e.textContent = text; svg.append(e); return e; };
  add("line", { x1: L, y1: H - B, x2: W, y2: H - B, class: "axis" });
  add("text", { x: 0, y: 10 }, String(max));
  add("text", { x: 0, y: H - B }, "0");
  for (let k = 0; k < n; k++) {
    let y = H - B;
    for (const s of series) {
      const v = s.points[k].v || 0;
      if (!v) continue;
      const hgt = (H - B - 6) * v / max;
      y -= hgt;
      const r = add("rect", { x: L + k * bw + 1, y, width: Math.max(1, bw - 2), height: hgt, class: s.cls });
      const t = document.createElementNS(NS, "title");
      t.textContent = s.points[k].t.slice(0, 10) + " · " + s.name + ": " + v;
      r.append(t);
    }
  }
  if (n) {
    add("text", { x: L, y: H - 4 }, series[0].points[0].t.slice(0, 10));
    add("text", { x: W - 62, y: H - 4 }, series[0].points[n - 1].t.slice(0, 10));
  }
  return el("div", { class: "chart" }, svg, el("div", { class: "legend" },
    series.map((s) => el("span", null, el("i", { class: s.cls }), s.name))));
}
function renderMeasures() {
  const stats = MEASURES.filter((w) => w.kind === "stat");
  const rest = MEASURES.filter((w) => w.kind !== "stat");
  const out = [el("div", { class: "stats" }, stats.map((w) => el("div", { class: "stat" },
    el("div", { class: "st" }, TITLES[w.id] || w.id),
    el("div", { class: "sv" }, String(w.data.value), w.data.unit ? el("small", null, w.data.unit) : null),
    w.data.label ? el("div", { class: "sl" }, w.data.label) : null)))];
  for (const w of rest) {
    const title = el("h3", null, TITLES[w.id] || w.id);
    if (w.kind === "timeseries") title.append(el("span", { class: "ranges" }, ["7d", "30d", "90d"].map((r) =>
      el("button", { type: "button", "aria-pressed": r === chartRange ? "true" : "false",
        onclick: () => { chartRange = r; renderMeasures(); } }, r))));
    out.push(el("div", { class: "card" }, title, w.notice ? el("div", { class: "notice" }, w.notice) : null,
      w.kind === "timeseries" ? renderChart(w.data) : w.kind === "table" ? renderTable(w.data) : el("pre", null, JSON.stringify(w.data))));
  }
  $("mout").replaceChildren(...out);
}

// ---- wiring -----------------------------------------------------------------
for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => setTab(b.dataset.tab));
$("sform").addEventListener("submit", (e) => { e.preventDefault(); search(); });
$("nfilter").addEventListener("input", () => NOTES && renderNotes());
$("ngroup").addEventListener("change", () => NOTES && renderNotes());
$("nrefresh").addEventListener("click", () => loadNotes(true));
$("mrefresh").addEventListener("click", () => loadMeasures());
$("rclose").addEventListener("click", closeNote);
$("rcopy").addEventListener("click", () => { if (current && navigator.clipboard) navigator.clipboard.writeText(current); });
addEventListener("keydown", (e) => { if (e.key === "Escape" && current && document.activeElement !== $("q")) closeNote(); });
addEventListener("message", (e) => {
  if (e.origin !== location.origin || !e.data || e.data.type !== "hub:visibility") return;
  if (e.data.visible && tab === "measures") loadMeasures();
});

(async function start() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("q")) $("q").value = p.get("q");
  const t = ["search", "measures", "notes"].includes(p.get("tab")) ? p.get("tab") : "search";
  const g = await api("search", { q: "" });
  if (g.groups) renderGroups(g.groups, g.default);
  if (g.version) $("version").textContent = g.version;
  setTab(t, !!p.get("note"));
  if ($("q").value) search();
  if (p.get("note")) openNote(p.get("note"));
})();
