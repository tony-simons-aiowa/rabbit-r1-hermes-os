// Tiny DOM toolkit: an h() element builder plus a few form controls. Text is always set with
// textContent (never innerHTML), so strings from the bridge cannot inject markup.

export type Attrs = Record<string, unknown>;
export type Child = Node | string | number | false | null | undefined | Child[];
export type Tab = { el: HTMLElement; sync: () => void };
export type Option = { value: string; label: string };
export type ButtonVariant = "primary" | "ghost" | "danger" | "icon" | "chip" | "link";

const PROPS = new Set(["value", "checked", "disabled", "selected"]);

/** Build an element. Keys starting with "on" become listeners; value/checked/disabled are set as properties. */
export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs?: Attrs | null,
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (attrs) {
    for (const key of Object.keys(attrs)) {
      const v = attrs[key];
      if (v === undefined) continue;
      if (PROPS.has(key)) {
        (el as unknown as Record<string, unknown>)[key] = v;
      } else if (typeof v === "function") {
        if (key.startsWith("on")) el.addEventListener(key.slice(2), v as EventListener);
      } else if (v !== null && v !== false) {
        el.setAttribute(key, v === true ? "" : String(v));
      }
    }
  }
  append(el, children);
  return el;
}

function append(parent: Node, children: Child[]): void {
  for (const c of children) {
    if (c === false || c === null || c === undefined) continue;
    if (Array.isArray(c)) append(parent, c);
    else if (typeof c === "string" || typeof c === "number") parent.appendChild(document.createTextNode(String(c)));
    else parent.appendChild(c);
  }
}

export function card(title: string, ...children: Child[]): HTMLElement {
  return h("section", { class: "card" }, h("h2", { class: "card-title" }, title), ...children);
}

export function button(
  label: string,
  onClick: (e: MouseEvent) => void,
  opts: { variant?: ButtonVariant; disabled?: boolean; attrs?: Attrs } = {},
): HTMLButtonElement {
  const cls = opts.variant === "link" ? "link-btn" : opts.variant ? "btn " + opts.variant : "btn";
  return h("button", { ...(opts.attrs ?? {}), type: "button", class: cls, disabled: !!opts.disabled, onclick: onClick }, label);
}

/** Two-state switch. The returned input is kept so sync code can update its checked state. */
export function toggle(opts: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}): { el: HTMLLabelElement; input: HTMLInputElement } {
  const input: HTMLInputElement = h("input", {
    type: "checkbox",
    role: "switch",
    checked: opts.checked,
    onchange: () => opts.onChange(input.checked),
  });
  const el = h(
    "label",
    { class: "switch-row" },
    h(
      "span",
      { class: "switch-text" },
      h("span", { class: "switch-title" }, opts.label),
      opts.hint ? h("span", { class: "hint" }, opts.hint) : null,
    ),
    h("span", { class: "switch" }, input, h("span", { class: "switch-track", "aria-hidden": "true" })),
  );
  return { el, input };
}

export function select(opts: {
  label: string;
  options: Option[];
  value: string;
  onChange: (v: string) => void;
}): HTMLSelectElement {
  const el: HTMLSelectElement = h(
    "select",
    { "aria-label": opts.label, onchange: () => opts.onChange(el.value) },
    opts.options.map((o) => h("option", { value: o.value }, o.label)),
  );
  el.value = opts.value;
  return el;
}

/** Set a control's value unless the user is typing in it. Keeps live syncs from fighting the keyboard. */
export function setValue(input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string): void {
  if (document.activeElement === input) return;
  if (input.value !== value) input.value = value;
}

/** Move focus to the element carrying data-fk="key" after a list was rebuilt. */
export function focusKey(root: ParentNode, key: string): void {
  for (const el of Array.from(root.querySelectorAll<HTMLElement>("[data-fk]"))) {
    if (el.dataset.fk === key) {
      el.focus();
      return;
    }
  }
}

export function errText(e: unknown): string {
  return e instanceof Error ? e.message : "Something went wrong.";
}

export function toast(message: string, kind: "info" | "ok" | "error" = "info"): void {
  const host = document.getElementById("toasts");
  if (!host) return;
  const el = h("div", { class: "toast " + kind, role: kind === "error" ? "alert" : "status" }, message);
  host.appendChild(el);
  window.setTimeout(() => el.remove(), kind === "error" ? 7000 : 4000);
}

export function relTime(ts: number): string {
  if (!ts) return "never";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 45) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " d ago";
}

export function fmtDate(ts: number): string {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
