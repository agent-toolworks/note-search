// The note reader's Markdown renderer (hub-source/page/md.js), run in Node
// with a small stand-in for the browser's DOM. tests/test_hub.py runs this
// when node is installed. Prints "ok <n>" and exits 0, or names the failure.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

class Node {
  constructor() { this.childNodes = []; }
  append(...kids) {
    for (const k of kids) {
      if (k instanceof Fragment) this.childNodes.push(...k.childNodes);
      else this.childNodes.push(k instanceof Node ? k : new Text(String(k)));
    }
  }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
}
class Text extends Node {
  constructor(data) { super(); this.data = data; }
  get textContent() { return this.data; }
  html() { return this.data.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
}
class Fragment extends Node {
  html() { return this.childNodes.map((c) => c.html()).join(""); }
}
class Element extends Node {
  constructor(tag) { super(); this.tagName = tag.toUpperCase(); this.attrs = {}; this.dataset = {}; this.className = ""; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  set href(v) { this.attrs.href = v; }
  set id(v) { this.attrs.id = v; }
  get id() { return this.attrs.id; }
  set start(v) { this.attrs.start = v; }
  html() {
    const a = Object.assign({}, this.attrs);
    if (this.className) a.class = this.className;
    for (const [k, v] of Object.entries(this.dataset)) a["data-" + k] = v;
    if (this.type) a.type = this.type;
    if (this.checked) a.checked = "";
    const at = Object.entries(a).map(([k, v]) => " " + k + '="' + String(v).replace(/"/g, "&quot;") + '"').join("");
    return "<" + this.tagName.toLowerCase() + at + ">" + this.childNodes.map((c) => c.html()).join("") + "</" + this.tagName.toLowerCase() + ">";
  }
  all() { return [this, ...this.childNodes.filter((c) => c instanceof Element).flatMap((c) => c.all())]; }
}
const document = {
  createElement: (t) => new Element(t),
  createTextNode: (s) => new Text(s),
  createDocumentFragment: () => new Fragment(),
};
const ctx = vm.createContext({ document, Node, console });
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "hub-source", "page", "md.js"), "utf8") + "\nthis.MD = MD;", ctx);
const MD = ctx.MD;

const render = (text, c) => MD.renderMarkdown(text, c);
const elements = (frag) => frag.childNodes.filter((c) => c instanceof Element).flatMap((c) => c.all());
let n = 0;
function check(name, cond, detail) {
  n++;
  if (!cond) { console.error("FAIL " + name + (detail ? "\n" + detail : "")); process.exit(1); }
}

// 1. Nothing in a note becomes markup: HTML is text, javascript: links have no href.
const evil = render('<script>alert(1)</script>\n\n<img src=x onerror="alert(2)">\n\n[click](javascript:alert(3)) [ok](https://example.com)\n\n<!-- hidden -->\nafter');
const tags = elements(evil).map((e) => e.tagName);
check("no script or img element", !tags.includes("SCRIPT") && !tags.includes("IMG"), tags.join(","));
check("html shown as text", evil.html().includes("&lt;script&gt;alert(1)&lt;/script&gt;"), evil.html());
check("no javascript: href", !/href="javascript:/i.test(evil.html()), evil.html());
check("https link kept", evil.html().includes('href="https://example.com"'), evil.html());
check("an HTML comment is hidden", !evil.html().includes("hidden") && evil.html().includes("after"), evil.html());

// 2. Structure.
const doc = render([
  "# Payment gateway", "", "## Timeouts", "", "Line one", "line **two** and *three*, `code<b>`, ~~old~~, ==hi== #tag", "",
  "- a", "  - nested", "- [x] done", "- [ ] todo", "", "1. first", "2. second", "",
  "| Left | Right |", "| :--- | ---: |", "| a \\| b | `x|y` |", "",
  "```js", "const x = '<b>';", "```", "",
  "> [!warning] Careful", "> body text", "", "> plain quote", "",
  "See [[Other note#Section|the other]] and [rel](../b.md#Top).", "", "---", "## Timeouts",
].join("\n"), { link: (href, kids) => { const a = document.createElement("a"); a.setAttribute("data-href", href); a.append(...kids); return a; },
                wiki: (t, l) => { const a = document.createElement("a"); a.setAttribute("data-wiki", t); a.append(l); return a; }, firstLine: 10 });
const html = doc.html();
check("headings with ids, duplicates numbered", html.includes('<h1 id="payment-gateway"') && html.includes('<h2 id="timeouts"') && html.includes('id="timeouts-2"'), html);
check("source line numbers", html.includes('data-line="10"'), html);
check("a line break is a break", /Line one<br><\/br>line/.test(html), html);
check("inline marks", html.includes("<strong>two</strong>") && html.includes("<em>three</em>") && html.includes("<del>old</del>") &&
      html.includes("<mark>hi</mark>") && html.includes('<span class="md-tag">#tag</span>'), html);
check("code span keeps its text", html.includes("<code>code&lt;b&gt;</code>"), html);
check("nested list", /<ul class="md-list"><li>a<ul class="md-list"><li>nested<\/li><\/ul><\/li>/.test(html), html);
check("task list", html.includes('<li class="md-task"><input type="checkbox" checked=""></input>done') && html.includes('<input type="checkbox"></input>todo'), html);
check("ordered list", /<ol class="md-list"><li>first<\/li><li>second<\/li><\/ol>/.test(html), html);
check("table with alignment and escaped pipe", html.includes('<th class="al-right">Right</th>') && html.includes("<td>a | b</td>") && html.includes("<code>x|y</code>"), html);
check("fenced code is text", html.includes('<pre class="md-code" data-lang="js"') && html.includes("const x = '&lt;b&gt;';"), html);
check("callout", html.includes('md-callout md-callout-warning') && html.includes("Careful") && html.includes("body text"), html);
check("blockquote", html.includes("<blockquote><p") && html.includes("plain quote"), html);
check("wiki link handed to ctx", html.includes('data-wiki="Other note#Section"') && html.includes(">the other</a>"), html);
check("relative link handed to ctx", html.includes('data-href="../b.md#Top"'), html);
check("rule", html.includes("<hr></hr>"), html);

// 3. Memory-note shape: frontmatter is stripped by the source; **Why:** lines keep their breaks.
const mem = render("Rule text.\n**Why:** because.\n**How to apply:** do it.").html();
check("bold label lines keep their breaks", mem.includes("Rule text.<br></br><strong>Why:</strong> because.<br></br><strong>How to apply:</strong>"), mem);

console.log("ok " + n);
