// Type screen: a text input, a Send row and optional quick-prompt rows.
import { h, List } from "../ui";
import { push, pop, current, type Screen } from "../nav";
import type { AppEvent } from "../platform";
import { toast } from "../chrome";
import { row } from "./base";

export function openType(opts: {
  title: string;
  placeholder?: string;
  maxLength?: number;
  quick?: { label: string; text: string }[];
  onSend: (text: string) => void;
}): void {
  const el = h("div", { class: "scr type" });
  el.appendChild(h("div", { class: "hdr" }, opts.title));
  const input = h("input", {
    class: "type-input",
    type: "text",
    placeholder: opts.placeholder || "Type a message",
    autocomplete: "off",
    autocapitalize: "sentences",
    maxlength: opts.maxLength ?? 2000,
  });
  el.appendChild(h("div", { class: "type-field" }, input));
  const list = new List(h("div", { class: "type-list" }), (i) => activate(i));
  el.appendChild(list.el);
  list.bindTouch();

  const quick = opts.quick || [];
  const rows = [
    row({ title: "Send", cls: "sheet-row send" }),
    ...quick.map((q) => row({ title: q.label, sub: q.text, cls: "sheet-row" })),
  ];

  const screen: Screen = {
    el,
    onShow() {
      input.focus();
    },
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
      return false;
    },
  };

  function finish(text: string): void {
    const t = text.trim();
    if (!t) {
      toast("Type something first");
      return;
    }
    if (current() === screen) pop();
    opts.onSend(t);
  }

  function activate(i: number): void {
    if (i === 0) finish(input.value);
    else finish(quick[i - 1].text);
  }

  input.addEventListener("keydown", (e) => {
    if ((e as KeyboardEvent).key === "Enter") {
      e.preventDefault();
      finish(input.value);
    }
  });

  list.setRows(rows, false);
  push(screen);
}
