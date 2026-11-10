// Generic sheet of rows. Reused for the chat menu, quick prompts and the Ask sheet.
import { h, List } from "../ui";
import { push, pop, current, type Screen } from "../nav";
import type { AppEvent } from "../platform";
import { row } from "./base";

export type SheetRow = { label: string; sub?: string; right?: string; cls?: string; onPick: () => void };

export type Sheet = Screen & { dismiss(): void };

export function openSheet(opts: { title: string; detail?: string; rows: SheetRow[] }): Sheet {
  const el = h("div", { class: "scr sheet" });
  el.appendChild(h("div", { class: "hdr" }, opts.title));
  const list = new List(
    h("div", { class: "sheet-list" }),
    (i) => pick(i),
  );
  if (opts.detail) list.el.appendChild(h("div", { class: "sheet-detail" }, opts.detail));
  el.appendChild(list.el);
  list.bindTouch();

  const sheet: Sheet = {
    el,
    onInput(ev: AppEvent): boolean {
      if (ev === "up") {
        list.move(-1);
        return true;
      }
      if (ev === "down") {
        list.move(1);
        return true;
      }
      if (ev === "click") {
        list.activate();
        return true;
      }
      if (ev === "menu") {
        dismiss();
        return true;
      }
      return false;
    },
    dismiss,
  };

  function dismiss(): void {
    if (current() === sheet) pop();
  }

  function pick(i: number): void {
    const r = opts.rows[i];
    if (!r) return;
    dismiss();
    r.onPick();
  }

  list.setRows(
    opts.rows.map((r) => row({ title: r.label, sub: r.sub, right: r.right, cls: "sheet-row " + (r.cls || "") })),
    false,
  );
  push(sheet);
  return sheet;
}
