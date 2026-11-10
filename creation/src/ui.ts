// Tiny DOM helpers. Text only ever enters the DOM via text nodes / textContent.

export type Attrs = Record<string, string | number | boolean | null | undefined | ((e: Event) => void)>;
export type Child = Node | string | number | null | undefined | false | Child[];

/** h("div", {class: "x", onclick: fn}, "text", child) — never sets innerHTML. */
export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs?: Attrs | null, ...children: Child[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (attrs) {
    for (const key of Object.keys(attrs)) {
      const v = attrs[key];
      if (v === null || v === undefined || v === false) continue;
      if (typeof v === "function") {
        if (key.startsWith("on")) el.addEventListener(key.slice(2), v);
        continue;
      }
      if (key === "style" && typeof v === "string") {
        el.setAttribute("style", v);
      } else if (v === true) {
        el.setAttribute(key, "");
      } else {
        el.setAttribute(key, String(v));
      }
    }
  }
  append(el, children);
  return el;
}

function append(el: Node, children: Child[]): void {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else if (typeof c === "string" || typeof c === "number") el.appendChild(document.createTextNode(String(c)));
    else el.appendChild(c);
  }
}

/** Replace all children of el. */
export function clear(el: HTMLElement): void {
  while (el.firstChild) el.removeChild(el.firstChild);
}

/**
 * A wheel-navigable list of rows. Rows are elements; the highlighted one gets class "hl".
 * Keeps the highlight in view by setting scrollTop on the container.
 */
export class List {
  index = 0;
  rows: HTMLElement[] = [];
  constructor(
    public el: HTMLElement,
    public onActivate: (index: number) => void,
    public onMove?: (index: number) => void,
  ) {}

  setRows(rows: HTMLElement[], keepIndex = true): void {
    const prev = this.index;
    this.rows = rows;
    // Replace only row elements; non-row children (e.g. a detail block) stay put.
    for (const c of Array.from(this.el.children)) if (c.hasAttribute("data-i")) c.remove();
    rows.forEach((r, i) => {
      r.classList.toggle("hl", false);
      r.setAttribute("data-i", String(i));
      this.el.appendChild(r);
    });
    this.index = keepIndex ? Math.min(prev, Math.max(0, rows.length - 1)) : 0;
    this.highlight();
  }

  move(delta: number): void {
    if (!this.rows.length) return;
    const n = this.rows.length;
    this.index = (this.index + delta + n) % n;
    this.highlight();
    if (this.onMove) this.onMove(this.index);
  }

  highlight(): void {
    this.rows.forEach((r, i) => r.classList.toggle("hl", i === this.index));
    const row = this.rows[this.index];
    if (row) {
      const top = row.offsetTop;
      const bottom = top + row.offsetHeight;
      const view = this.el.clientHeight;
      if (top < this.el.scrollTop) this.el.scrollTop = top;
      else if (bottom > this.el.scrollTop + view) this.el.scrollTop = bottom - view;
    }
  }

  activate(): void {
    if (this.rows.length) this.onActivate(this.index);
  }

  /** Wire pointer taps: pointerup on a row activates it (no preventDefault on touchstart). */
  bindTouch(): void {
    this.el.addEventListener("pointerup", (e) => {
      const t = (e.target as HTMLElement | null)?.closest?.("[data-i]") as HTMLElement | null;
      if (!t) return;
      const i = Number(t.getAttribute("data-i"));
      if (Number.isFinite(i)) {
        this.index = i;
        this.highlight();
        this.onActivate(i);
      }
    });
  }
}

/** "now", "5m", "2h", "3d". ts in unix seconds. */
export function relativeTime(ts: number): string {
  if (!ts) return "";
  const diff = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (diff < 60) return "now";
  if (diff < 3600) return Math.floor(diff / 60) + "m";
  if (diff < 86400) return Math.floor(diff / 3600) + "h";
  return Math.floor(diff / 86400) + "d";
}
