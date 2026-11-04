"use strict";
// A small Markdown renderer for the note reader. It builds DOM nodes and never
// inserts HTML: notes are text other people wrote, so a <script> in a note is
// shown as text, never run. It reads what notes here use: ATX headings, lists
// (nested, numbered, task), tables, fenced and indented code, quotes and
// Obsidian callouts (> [!note]), rules, links, [[wiki links]], ==highlights==,
// #tags, **bold**, *italic*, ~~strike~~ and `code`. A line break is a break, as
// Obsidian shows it.
//
//   renderMarkdown(text, ctx) -> DocumentFragment
//   ctx.link(href, textNodes) -> Node   decides what a [text](href) becomes
//   ctx.wiki(target, label)   -> Node   the same for [[target|label]]
//   ctx.firstLine                       line number of text's first line (data-line)

const MD = (() => {
  const h = (tag, cls, ...kids) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    for (const k of kids.flat(Infinity)) if (k !== null && k !== undefined && k !== false)
      n.append(k instanceof Node ? k : document.createTextNode(String(k)));
    return n;
  };

  const slug = (s) => String(s).toLowerCase().replace(/[`*_~=\[\]()#]/g, "").trim()
    .replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "") || "section";

  // ---- inline -------------------------------------------------------------
  const PATTERNS = [
    ["code", /(`+)([\s\S]*?[^`])\1(?!`)/y],
    ["escape", /\\([\\`*_{}\[\]()#+\-.!~=|<>])/y],
    ["image", /!\[([^\]]*)\]\(\s*<?([^)\s>]*)>?(?:\s+"[^"]*")?\s*\)/y],
    ["embed", /!\[\[([^\]\n]+)\]\]/y],
    ["wiki", /\[\[([^\]\n]+)\]\]/y],
    ["link", /\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*<?([^)\s>]*)>?(?:\s+"[^"]*")?\s*\)/y],
    ["autolink", /<((?:https?|mailto):[^>\s]+)>/y],
    ["url", /\bhttps?:\/\/[^\s<>()\[\]]+[^\s<>()\[\].,;:!?'"]/y],
    ["strong", /\*\*(?=\S)([\s\S]*?\S)\*\*|__(?=\S)([\s\S]*?\S)__(?!\w)/y],
    ["em", /\*(?=[^\s*])([\s\S]*?[^\s*])\*(?!\*)|(?<!\w)_(?=\S)([\s\S]*?\S)_(?!\w)/y],
    ["del", /~~(?=\S)([\s\S]*?\S)~~/y],
    ["mark", /==(?=\S)([\s\S]*?\S)==/y],
    ["tag", /(?<![\w&#/])#([A-Za-z][\w\/-]*)/y],
  ];
  const SPECIAL = /[`\\!\[<*_~=h#]/;

  function inline(text, ctx) {
    const out = [];
    let buf = "", i = 0;
    const flush = () => { if (buf) { out.push(document.createTextNode(buf)); buf = ""; } };
    while (i < text.length) {
      const c = text[i];
      if (c === "\n") { flush(); out.push(h("br")); i++; continue; }
      if (!SPECIAL.test(c)) { buf += c; i++; continue; }
      let hit = null;
      for (const [kind, rx] of PATTERNS) {
        if (kind === "url" && c !== "h") continue;
        rx.lastIndex = i;
        const m = rx.exec(text);
        if (m) { hit = [kind, m]; break; }
      }
      if (!hit) { buf += c; i++; continue; }
      flush();
      const [kind, m] = hit;
      i += m[0].length;
      if (kind === "code") out.push(h("code", null, m[2].replace(/^ (.*) $/, "$1")));
      else if (kind === "escape") out.push(document.createTextNode(m[1]));
      else if (kind === "image") out.push(h("span", "md-image", "🖼 " + (m[1] || m[2] || "image")));
      else if (kind === "embed") out.push(h("span", "md-image", "↪ " + m[1]));
      else if (kind === "wiki") {
        const [target, label] = m[1].split("|");
        out.push(ctx.wiki(target.trim(), (label || target).trim()));
      } else if (kind === "link") out.push(ctx.link(m[2], inline(m[1], ctx)));
      else if (kind === "autolink" || kind === "url") out.push(ctx.link(kind === "url" ? m[0] : m[1], [document.createTextNode(kind === "url" ? m[0] : m[1])]));
      else if (kind === "strong") out.push(h("strong", null, inline(m[1] || m[2], ctx)));
      else if (kind === "em") out.push(h("em", null, inline(m[1] || m[2], ctx)));
      else if (kind === "del") out.push(h("del", null, inline(m[1], ctx)));
      else if (kind === "mark") out.push(h("mark", null, inline(m[1], ctx)));
      else if (kind === "tag") out.push(h("span", "md-tag", "#" + m[1]));
    }
    flush();
    return out;
  }

  // ---- blocks -------------------------------------------------------------
  const RX = {
    fence: /^(\s{0,3})(`{3,}|~{3,})\s*([\w+#.-]*)/,
    heading: /^\s{0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$/,
    hr: /^\s{0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/,
    quote: /^\s{0,3}>\s?/,
    item: /^(\s*)([-*+]|\d{1,9}[.)])(?:[ \t]+(.*)|$)/,
    tableSep: /^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$/,
    comment: /^\s*<!--/,
  };
  const startsBlock = (line, next) => RX.fence.test(line) || RX.heading.test(line) || RX.hr.test(line) ||
    RX.quote.test(line) || RX.item.test(line) || RX.comment.test(line) ||
    (line.includes("|") && next !== undefined && RX.tableSep.test(next) && next.includes("-"));

  const cells = (line) => {
    let s = line.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
    const out = [];
    let cur = "", code = false;
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (c === "\\" && s[i + 1] === "|") { cur += "|"; i++; continue; }
      if (c === "`") code = !code;
      if (c === "|" && !code) { out.push(cur.trim()); cur = ""; } else cur += c;
    }
    out.push(cur.trim());
    return out;
  };

  function blocks(lines, ctx, base) {
    const frag = document.createDocumentFragment();
    let i = 0;
    const lineNo = (k) => base + k;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }

      if (/^\s*%%/.test(line)) {                       // Obsidian comment
        let j = i;
        if (!/%%.*%%/.test(line.trim().slice(2)) && !(line.trim().length > 2 && line.trim().endsWith("%%")))
          while (j + 1 < lines.length && !lines[++j].includes("%%"));
        i = j + 1;
        continue;
      }
      if (RX.comment.test(line)) {                     // HTML comment: hidden, never parsed as HTML
        let j = i;
        while (j < lines.length && !lines[j].includes("-->")) j++;
        i = j + 1;
        continue;
      }
      let m = RX.fence.exec(line);
      if (m) {
        const fence = m[2], lang = m[3], body = [];
        let j = i + 1;
        while (j < lines.length && !new RegExp("^\\s{0,3}" + fence[0] + "{" + fence.length + ",}\\s*$").test(lines[j])) body.push(lines[j++]);
        const pre = h("pre", "md-code", h("code", null, body.join("\n")));
        if (lang) pre.dataset.lang = lang;
        pre.dataset.line = lineNo(i);
        frag.append(pre);
        i = j + 1;
        continue;
      }
      m = RX.heading.exec(line);
      if (m) {
        const level = m[1].length, text = m[2];
        const el = h("h" + level, "md-h", inline(text, ctx));
        el.id = ctx.id(slug(text));
        el.dataset.heading = text;
        el.dataset.line = lineNo(i);
        frag.append(el);
        i++;
        continue;
      }
      if (RX.hr.test(line)) { frag.append(h("hr")); i++; continue; }
      if (line.includes("|") && i + 1 < lines.length && RX.tableSep.test(lines[i + 1]) && lines[i + 1].includes("-")) {
        const head = cells(line), align = cells(lines[i + 1]).map((c) => c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : "");
        const table = h("table", "md-table");
        const tr = h("tr");
        head.forEach((c, k) => { const th = h("th", align[k] ? "al-" + align[k] : null, inline(c, ctx)); tr.append(th); });
        table.append(h("thead", null, tr));
        const tbody = h("tbody");
        let j = i + 2;
        while (j < lines.length && lines[j].includes("|") && lines[j].trim()) {
          const row = cells(lines[j]), r = h("tr");
          head.forEach((_, k) => r.append(h("td", align[k] ? "al-" + align[k] : null, inline(row[k] || "", ctx))));
          tbody.append(r);
          j++;
        }
        table.append(tbody);
        frag.append(h("div", "md-tablewrap", table));
        i = j;
        continue;
      }
      if (RX.quote.test(line)) {
        const body = [];
        let j = i;
        while (j < lines.length && RX.quote.test(lines[j])) body.push(lines[j++].replace(RX.quote, ""));
        const call = /^\[!(\w+)\][+-]?\s*(.*)$/.exec(body[0] || "");
        if (call) {
          const kind = call[1].toLowerCase();
          const box = h("div", "md-callout md-callout-" + kind,
            h("div", "md-callout-title", call[2] ? inline(call[2], ctx) : kind[0].toUpperCase() + kind.slice(1)));
          box.append(blocks(body.slice(1), ctx, lineNo(i) + 1));
          frag.append(box);
        } else {
          const q = h("blockquote");
          q.append(blocks(body, ctx, lineNo(i)));
          frag.append(q);
        }
        i = j;
        continue;
      }
      m = RX.item.exec(line);
      if (m) { i = list(lines, i, ctx, base, frag); continue; }
      if (/^( {4}|\t)/.test(line)) {                   // indented code
        const body = [];
        let j = i;
        while (j < lines.length && (/^( {4}|\t)/.test(lines[j]) || (!lines[j].trim() && j + 1 < lines.length && /^( {4}|\t)/.test(lines[j + 1]))))
          body.push(lines[j++].replace(/^( {4}|\t)/, ""));
        frag.append(h("pre", "md-code", h("code", null, body.join("\n"))));
        i = j;
        continue;
      }
      const para = [line];
      let j = i + 1;
      while (j < lines.length && lines[j].trim() && !startsBlock(lines[j], lines[j + 1])) para.push(lines[j++]);
      const p = h("p", null, inline(para.map((l) => l.replace(/^\s+/, "").replace(/(\\| {2,})$/, "")).join("\n"), ctx));
      p.dataset.line = lineNo(i);
      frag.append(p);
      i = j;
    }
    return frag;
  }

  function list(lines, start, ctx, base, frag) {
    const first = RX.item.exec(lines[start]);
    const indent = first[1].replace(/\t/g, "    ").length;
    const ordered = /\d/.test(first[2]);
    const el = h(ordered ? "ol" : "ul", "md-list");
    if (ordered && parseInt(first[2], 10) !== 1) el.start = parseInt(first[2], 10);
    let i = start;
    while (i < lines.length) {
      const m = RX.item.exec(lines[i]);
      if (!m || m[1].replace(/\t/g, "    ").length !== indent || /\d/.test(m[2]) !== ordered) break;
      const width = indent + m[2].length + 1;
      const body = [m[3] || ""];
      let j = i + 1, loose = false;
      while (j < lines.length) {
        const l = lines[j];
        if (!l.trim()) {
          const next = lines[j + 1];
          if (next !== undefined && next.replace(/\t/g, "    ").search(/\S/) >= width) { body.push(""); loose = true; j++; continue; }
          break;
        }
        const lead = l.replace(/\t/g, "    ").search(/\S/);
        if (lead < width && (RX.item.test(l) || lead <= indent)) break;
        body.push(l.replace(/\t/g, "    ").slice(Math.min(lead, width)));
        j++;
      }
      const li = h("li");
      let text = body[0];
      const task = /^\[([ xX])\]\s+/.exec(text);
      if (task) {
        const box = h("input");
        box.type = "checkbox";
        box.disabled = true;
        box.checked = task[1] !== " ";
        li.className = "md-task";
        li.append(box);
        body[0] = text.slice(task[0].length);
      }
      const rest = body.slice(1);
      const simple = !loose && !rest.some((l) => startsBlock(l) || RX.item.test(l));
      if (simple) li.append(...inline(body.join("\n"), ctx));
      else if (!loose && body[0].trim()) {               // tight: "text" then a nested list
        li.append(...inline(body[0], ctx));
        li.append(blocks(rest, ctx, base + i + 1));
      } else li.append(blocks(body, ctx, base + i));
      el.append(li);
      i = j;
      while (i < lines.length && !lines[i].trim() && lines[i + 1] !== undefined && RX.item.test(lines[i + 1]) &&
             RX.item.exec(lines[i + 1])[1].replace(/\t/g, "    ").length === indent) i++;
    }
    frag.append(el);
    return i;
  }

  function renderMarkdown(text, ctx) {
    const used = {};
    const full = Object.assign({
      // Only web, mail, in-page and relative links get an href; javascript: and
      // other schemes stay text.
      link: (href, kids) => {
        const a = h("a", null, kids);
        if (/^(https?:|mailto:|#)/i.test(href) || !/^[a-z][a-z0-9+.-]*:/i.test(href)) a.setAttribute("href", href);
        return a;
      },
      wiki: (target, label) => h("span", "md-wiki", label),
      firstLine: 1,
    }, ctx);
    full.id = (s) => { used[s] = (used[s] || 0) + 1; return used[s] > 1 ? s + "-" + used[s] : s; };
    return blocks(String(text).split("\n"), full, full.firstLine);
  }

  return { renderMarkdown, inline, slug };
})();
