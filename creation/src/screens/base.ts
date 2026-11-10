// Shared helpers for screen modules.
import { h } from "../ui";

/** Standard list row: title, optional sub line, optional leading node and right-side text. Min 36px tall. */
export function row(opts: { title: string; sub?: string; lead?: Node | null; right?: string; cls?: string }): HTMLElement {
  return h(
    "div",
    { class: "row " + (opts.cls || "") },
    opts.lead || null,
    h(
      "div",
      { class: "row-main" },
      h("div", { class: "row-t" }, opts.title),
      opts.sub ? h("div", { class: "row-s" }, opts.sub) : null,
    ),
    opts.right ? h("span", { class: "row-r" }, opts.right) : null,
  );
}

/** A plain screen root with a title header. */
export function screenEl(cls: string, title?: string): HTMLElement {
  return h("div", { class: "scr " + cls }, title ? h("div", { class: "hdr" }, title) : null);
}
