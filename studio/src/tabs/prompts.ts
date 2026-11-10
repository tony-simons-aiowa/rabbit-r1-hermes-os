// Quick prompts tab: editable list (label, text, optional bot), reorder, add and remove.
import type { Bot, QuickPrompt } from "../../../shared/protocol";
import { button, card, focusKey, h, select, type Tab } from "../dom";
import { edit, getAdmin, getDraft, onChange } from "../state";

const MAX_PROMPTS = 12;
const LABEL_MAX = 24;
const TEXT_MAX = 500;

function newId(existing: string[]): string {
  let id = "";
  do {
    id = "q" + Math.random().toString(36).slice(2, 8);
  } while (existing.includes(id));
  return id;
}

export function mountPrompts(): Tab {
  let sig = "";
  let pendingFocus = "";

  const listEl = h("ol", { class: "prompt-list", "aria-label": "Quick prompts" });
  const empty = h("p", { class: "muted", hidden: true }, "No quick prompts yet. They appear in the device's menu.");
  const countEl = h("span", { class: "hint" });
  const addBtn = button("Add prompt", () => addPrompt(), { variant: "primary" });

  function setField(id: string, key: "label" | "text" | "bot", value: string): void {
    edit((d) => {
      const p = d.quick_prompts.find((x) => x.id === id);
      if (p) p[key] = value;
    });
  }

  function addPrompt(): void {
    const list = getDraft().quick_prompts;
    if (list.length >= MAX_PROMPTS) return;
    const id = newId(list.map((p) => p.id));
    pendingFocus = "label-" + id;
    edit((d) => {
      d.quick_prompts.push({ id, label: "New prompt", text: "", bot: "" });
    });
  }

  function removePrompt(id: string): void {
    edit((d) => {
      d.quick_prompts = d.quick_prompts.filter((p) => p.id !== id);
    });
  }

  function move(id: string, delta: number): void {
    const list = getDraft().quick_prompts.slice();
    const i = list.findIndex((p) => p.id === id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    pendingFocus = (delta < 0 ? "up-" : "down-") + id;
    edit((d) => {
      d.quick_prompts = list;
    });
  }

  function item(p: QuickPrompt, index: number, count: number, bots: Bot[]): HTMLElement {
    const label = h("input", {
      type: "text",
      maxlength: LABEL_MAX,
      value: p.label,
      placeholder: "Label",
      autocomplete: "off",
      "aria-label": "Label",
      "data-fk": "label-" + p.id,
      oninput: (e: Event) => setField(p.id, "label", (e.target as HTMLInputElement).value.slice(0, LABEL_MAX)),
    });
    const count2 = h("span", { class: "hint" }, `${p.text.length}/${TEXT_MAX}`);
    const text = h("textarea", {
      rows: 2,
      maxlength: TEXT_MAX,
      placeholder: "What to send to the bot",
      "aria-label": "Prompt text",
      "data-fk": "text-" + p.id,
      oninput: (e: Event) => {
        const v = (e.target as HTMLTextAreaElement).value.slice(0, TEXT_MAX);
        count2.textContent = `${v.length}/${TEXT_MAX}`;
        setField(p.id, "text", v);
      },
    }, p.text);

    const options = [{ value: "", label: "Current bot" }, ...bots.map((b) => ({ value: b.id, label: b.name || b.id }))];
    if (p.bot && !bots.some((b) => b.id === p.bot)) options.push({ value: p.bot, label: `${p.bot} (not found)` });
    const bot = select({
      label: "Bot for this prompt",
      options,
      value: p.bot,
      onChange: (v) => setField(p.id, "bot", v),
    });

    const up = button("▲", () => move(p.id, -1), {
      variant: "icon",
      disabled: index === 0,
      attrs: { "aria-label": `Move ${p.label} up`, "data-fk": "up-" + p.id },
    });
    const down = button("▼", () => move(p.id, 1), {
      variant: "icon",
      disabled: index === count - 1,
      attrs: { "aria-label": `Move ${p.label} down`, "data-fk": "down-" + p.id },
    });
    const remove = button("Remove", () => removePrompt(p.id), {
      variant: "ghost",
      attrs: { "aria-label": `Remove ${p.label}` },
    });

    return h(
      "li",
      { class: "prompt-item" },
      h(
        "div",
        { class: "prompt-head" },
        h("div", { class: "reorder" }, up, down),
        h("label", { class: "field grow" }, h("span", { class: "field-label" }, "Label"), label),
        h("label", { class: "field" }, h("span", { class: "field-label" }, "Bot"), bot),
        remove,
      ),
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Prompt text"), text),
      h("div", { class: "prompt-foot" }, count2),
    );
  }

  function rebuild(): void {
    const list = getDraft().quick_prompts;
    const bots = getAdmin().bots;
    listEl.replaceChildren(...list.map((p, i) => item(p, i, list.length, bots)));
    if (pendingFocus) {
      focusKey(listEl, pendingFocus);
      pendingFocus = "";
    }
  }

  function sync(): void {
    const list = getDraft().quick_prompts;
    const bots = getAdmin().bots;
    // Rebuild only when the structure changes; typing and bot picks update the draft without touching the DOM.
    const next = JSON.stringify([list.map((p) => p.id), bots.map((b) => [b.id, b.name])]);
    if (next !== sig) {
      sig = next;
      rebuild();
    }
    empty.hidden = list.length > 0;
    addBtn.disabled = list.length >= MAX_PROMPTS;
    countEl.textContent = `${list.length} of ${MAX_PROMPTS}`;
  }

  const el = h(
    "div",
    { class: "stack-lg" },
    card(
      "Quick prompts",
      h(
        "p",
        { class: "muted" },
        "Shown in the device menu for one-tap sends. Leave Bot on Current bot to use whichever bot is open.",
      ),
      h("div", { class: "row between" }, countEl, addBtn),
      empty,
      listEl,
    ),
  );

  onChange(sync);
  sync();
  return { el, sync };
}
