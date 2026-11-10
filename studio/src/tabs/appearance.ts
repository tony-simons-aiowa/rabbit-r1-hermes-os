// Appearance tab: theme presets, per-token color overrides, font size, greeting and clock.
import { THEMES, THEME_NAMES, TOKENS, parseColor, resolveTheme, type Token } from "../../../shared/themes";
import { card, h, setValue, toggle, type Tab } from "../dom";
import { hexOf } from "../color";
import { edit, getDraft, onChange } from "../state";

const TOKEN_HINTS: Record<Token, string> = {
  bg: "Screen background",
  surface: "Cards and bars",
  border: "Dividers and outlines",
  fg: "Main text",
  muted: "Secondary text",
  primary: "Accent, buttons, dots",
  accent: "Selection and your bubble",
  ok: "Success",
  warn: "Warnings",
  err: "Errors",
};

const GREETING_MAX = 60;

function setOverride(tok: Token, value: string): void {
  edit((d) => {
    d.theme.overrides[tok] = value;
  });
}

function clearOverride(tok: Token): void {
  if (typeof getDraft().theme.overrides[tok] !== "string") return;
  edit((d) => {
    delete d.theme.overrides[tok];
  });
}

export function mountAppearance(): Tab {
  // ---- presets ----
  const presetButtons = new Map<string, HTMLButtonElement>();
  const presetGrid = h("div", { class: "presets" });
  for (const name of THEME_NAMES) {
    const t = THEMES[name];
    const btn = h(
      "button",
      {
        type: "button",
        class: "preset",
        "aria-pressed": "false",
        onclick: () =>
          edit((d) => {
            d.theme.preset = name;
            d.theme.overrides = {};
          }),
      },
      h(
        "span",
        { class: "mini", style: `background:${t.bg}` },
        h("span", { class: "mini-bar", style: `background:${t.surface};border-color:${t.border}` }),
        h("span", { class: "mini-line", style: `background:${t.fg}` }),
        h("span", { class: "mini-line short", style: `background:${t.muted}` }),
        h("span", { class: "mini-dot", style: `background:${t.primary}` }),
      ),
      h("span", { class: "preset-name" }, name),
    );
    presetButtons.set(name, btn);
    presetGrid.append(btn);
  }

  // ---- token overrides ----
  type Row = { el: HTMLElement; input: HTMLInputElement; text: HTMLInputElement; reset: HTMLButtonElement };
  const rows = new Map<Token, Row>();
  const tokenList = h("div", { class: "token-list" });
  for (const tok of TOKENS) {
    const input = h("input", {
      type: "color",
      class: "color-input",
      "aria-label": `${tok} color`,
      oninput: (e: Event) => setOverride(tok, (e.target as HTMLInputElement).value),
    });
    const text = h("input", {
      type: "text",
      class: "mono token-text",
      spellcheck: false,
      autocomplete: "off",
      "aria-label": `${tok} value`,
      oninput: (e: Event) => {
        const field = e.target as HTMLInputElement;
        const v = field.value.trim();
        if (v === "") {
          field.removeAttribute("aria-invalid");
          clearOverride(tok);
        } else if (!parseColor(v)) {
          field.setAttribute("aria-invalid", "true");
        } else {
          field.removeAttribute("aria-invalid");
          setOverride(tok, v);
        }
      },
    });
    const reset = h("button", { type: "button", class: "link-btn", onclick: () => clearOverride(tok) }, "Reset");
    const el = h(
      "div",
      { class: "token-row" },
      h("span", { class: "token-name" }, h("code", null, tok), h("span", { class: "hint" }, TOKEN_HINTS[tok])),
      input,
      text,
      reset,
    );
    rows.set(tok, { el, input, text, reset });
    tokenList.append(el);
  }

  // ---- device display ----
  const fontValue = h("span", { class: "range-value mono", "aria-hidden": "true" });
  const font = h("input", {
    type: "range",
    min: "0.85",
    max: "1.3",
    step: "0.05",
    "aria-label": "Font size",
    oninput: (e: Event) => {
      const v = Number((e.target as HTMLInputElement).value);
      edit((d) => {
        d.font_scale = Math.round(v * 100) / 100;
      });
    },
  });
  const greetCount = h("span", { class: "hint" });
  const greet = h("input", {
    type: "text",
    maxlength: GREETING_MAX,
    placeholder: "Optional, e.g. Good morning",
    autocomplete: "off",
    "aria-label": "Greeting",
    oninput: (e: Event) => {
      const v = (e.target as HTMLInputElement).value.slice(0, GREETING_MAX);
      edit((d) => {
        d.home.greeting = v;
      });
    },
  });
  const clock = toggle({
    label: "Show clock",
    hint: "Time in the status bar of the home screen.",
    checked: true,
    onChange: (v) =>
      edit((d) => {
        d.home.clock = v;
      }),
  });

  function sync(): void {
    const d = getDraft();
    const eff = resolveTheme(d.theme);
    for (const [name, btn] of presetButtons) {
      const on = name === d.theme.preset;
      btn.setAttribute("aria-pressed", String(on));
    }
    for (const tok of TOKENS) {
      const r = rows.get(tok);
      if (!r) continue;
      const ov = d.theme.overrides[tok];
      const has = typeof ov === "string" && ov !== "";
      setValue(r.input, hexOf(eff[tok]));
      setValue(r.text, has && ov ? ov : eff[tok]);
      r.text.removeAttribute("aria-invalid");
      r.reset.disabled = !has;
      r.el.classList.toggle("overridden", has);
    }
    setValue(font, String(d.font_scale));
    fontValue.textContent = Math.round(d.font_scale * 100) + "%";
    setValue(greet, d.home.greeting);
    greetCount.textContent = `${d.home.greeting.length}/${GREETING_MAX}`;
    clock.input.checked = d.home.clock;
  }

  const el = h(
    "div",
    { class: "stack-lg" },
    card(
      "Theme",
      h("p", { class: "muted" }, "Pick a preset. Choosing one clears your color tweaks."),
      presetGrid,
    ),
    card(
      "Fine-tune colors",
      h("p", { class: "muted" }, "Overrides apply on top of the preset. Changes save automatically."),
      tokenList,
    ),
    card(
      "Device display",
      h(
        "label",
        { class: "field" },
        h("span", { class: "field-label" }, "Font size"),
        h("span", { class: "range-row" }, font, fontValue),
      ),
      h(
        "label",
        { class: "field" },
        h("span", { class: "field-label" }, "Greeting"),
        greet,
        greetCount,
      ),
      clock.el,
    ),
  );

  onChange(sync);
  sync();
  return { el, sync };
}
