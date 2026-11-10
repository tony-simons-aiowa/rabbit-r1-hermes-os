// Small, safe Markdown renderer. Builds DOM nodes only (never innerHTML). Supports paragraphs,
// line breaks, **bold**, *italic*, `code`, fenced code, "- " / "1. " lists, "#" headings and
// [text](url) rendered as plain underlined text (not clickable).
import { h, clear } from "./ui";

type Block =
  | { k: "p"; lines: string[] }
  | { k: "h"; text: string }
  | { k: "code"; text: string }
  | { k: "ul" | "ol"; items: string[] };

function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out: Block[] = [];
  let para: string[] | null = null;
  let list: { k: "ul" | "ol"; items: string[] } | null = null;
  const flushPara = (): void => {
    if (para && para.length) out.push({ k: "p", lines: para });
    para = null;
  };
  const flushList = (): void => {
    if (list) out.push(list);
    list = null;
  };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flushPara();
      flushList();
      const code: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        code.push(lines[i]);
        i++;
      }
      i++; // skip closing fence (or run past end while streaming)
      out.push({ k: "code", text: code.join("\n") });
      continue;
    }
    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    const ul = /^\s*[-*]\s+(.*)$/.exec(line);
    const ol = /^\s*\d+\.\s+(.*)$/.exec(line);
    if (heading) {
      flushPara();
      flushList();
      out.push({ k: "h", text: heading[1] });
    } else if (ul || ol) {
      flushPara();
      const kind: "ul" | "ol" = ul ? "ul" : "ol";
      if (!list || list.k !== kind) {
        flushList();
        list = { k: kind, items: [] };
      }
      list.items.push((ul || ol || [])[1]);
    } else if (line.trim() === "") {
      flushPara();
      flushList();
    } else {
      flushList();
      if (!para) para = [];
      para.push(line);
    }
    i++;
  }
  flushPara();
  flushList();
  return out;
}

const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+?\*\*)|(\*[^*\s][^*\n]*?\*)|(\[[^\]\n]+\]\([^)\n]+\))/;

/** Append inline-formatted text to el. */
function inline(el: HTMLElement, text: string): void {
  let rest = text;
  for (;;) {
    const m = INLINE.exec(rest);
    if (!m) {
      if (rest) el.appendChild(document.createTextNode(rest));
      return;
    }
    if (m.index > 0) el.appendChild(document.createTextNode(rest.slice(0, m.index)));
    const tok = m[0];
    if (tok.charAt(0) === "`") {
      el.appendChild(h("code", { class: "md-ic" }, tok.slice(1, -1)));
    } else if (tok.startsWith("**")) {
      el.appendChild(h("strong", null, tok.slice(2, -2)));
    } else if (tok.charAt(0) === "*") {
      el.appendChild(h("em", null, tok.slice(1, -1)));
    } else {
      const label = tok.slice(1, tok.indexOf("]"));
      el.appendChild(h("span", { class: "md-link" }, label));
    }
    rest = rest.slice(m.index + tok.length);
  }
}

/** Render markdown source into a fresh set of children of el. */
export function renderMarkdown(el: HTMLElement, src: string): void {
  clear(el);
  for (const b of parseBlocks(src)) {
    if (b.k === "p") {
      const p = h("p", { class: "md-p" });
      b.lines.forEach((ln, idx) => {
        if (idx > 0) p.appendChild(h("br"));
        inline(p, ln);
      });
      el.appendChild(p);
    } else if (b.k === "h") {
      const d = h("div", { class: "md-h" });
      inline(d, b.text);
      el.appendChild(d);
    } else if (b.k === "code") {
      el.appendChild(h("pre", { class: "md-code" }, h("code", null, b.text)));
    } else {
      const list = h(b.k === "ul" ? "ul" : "ol", { class: "md-list" });
      for (const item of b.items) {
        const li = h("li");
        inline(li, item);
        list.appendChild(li);
      }
      el.appendChild(list);
    }
  }
}

/**
 * Throttled view: set() is cheap; the DOM is rebuilt at most ~8 times per second and at most
 * once per animation frame. Pass immediate=true to render synchronously (e.g. when a message finishes).
 */
export class MdView {
  private text = "";
  private pending = false;
  private last = 0;
  private timer = 0;

  constructor(public el: HTMLElement) {}

  set(text: string, immediate = false): void {
    this.text = text;
    if (immediate) {
      this.cancel();
      this.flush();
      return;
    }
    if (this.pending) return;
    this.pending = true;
    const wait = Math.max(0, 125 - (Date.now() - this.last));
    this.timer = setTimeout(() => {
      this.timer = 0;
      requestAnimationFrame(() => this.flush());
    }, wait) as unknown as number;
  }

  flush(): void {
    this.pending = false;
    this.last = Date.now();
    renderMarkdown(this.el, this.text);
  }

  cancel(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = 0;
    this.pending = false;
  }
}
