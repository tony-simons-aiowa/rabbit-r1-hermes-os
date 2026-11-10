// Bots tab: roster order, visibility, avatar shape/color overrides, and device display toggles.
import { SHAPES, renderAvatar, setAvatarState } from "../../../shared/avatar";
import type { Avatar, Bot } from "../../../shared/protocol";
import { button, card, focusKey, h, setValue, toggle, type Tab } from "../dom";
import { hexOf } from "../color";
import { edit, getAdmin, getDraft, onChange } from "../state";

const SWATCHES = Array.from({ length: 12 }, (_, i) => `hsl(${i * 30} 68% 58%)`);

type Row = {
  li: HTMLElement;
  holder: HTMLElement;
  avatarKey: string;
  avatarEl: HTMLElement | null;
  previewColor: string;
  status: HTMLElement;
  lock: HTMLElement;
  hiddenInput: HTMLInputElement;
  up: HTMLButtonElement;
  down: HTMLButtonElement;
  expand: HTMLButtonElement;
  editor: HTMLElement;
  pickers: HTMLElement;
  imageNote: HTMLElement;
  colorInput: HTMLInputElement;
  shapePicks: Map<string, HTMLButtonElement>;
  colorPicks: Map<string, HTMLButtonElement>;
};

/** Avatar as the device will draw it: the bot's own avatar with any Studio override applied. */
function effectiveAvatar(b: Bot): Avatar {
  const o = getDraft().avatar_overrides[b.id] ?? {};
  return {
    kind: b.avatar.kind,
    shape: o.shape ?? b.avatar.shape,
    color: o.color ?? b.avatar.color,
    url: b.avatar.url,
  };
}

/** Roster order as the device shows it: explicit order first, then the default bot, then alphabetical. */
function currentOrder(): string[] {
  const bots = getAdmin().bots;
  const all = bots.map((b) => b.id);
  const ordered = getDraft().roster.order.filter((id) => all.includes(id));
  const rest = all.filter((id) => !ordered.includes(id));
  const def = bots.find((b) => b.is_default)?.id;
  rest.sort((a, b) => Number(b === def) - Number(a === def) || a.localeCompare(b));
  return [...ordered, ...rest];
}

function statusText(state: string): string {
  if (state === "busy") return "Working now";
  if (state === "ask") return "Needs your answer";
  return "";
}

function setPressed(btn: HTMLButtonElement, on: boolean): void {
  btn.setAttribute("aria-pressed", String(on));
}

export function mountBots(): Tab {
  const expanded = new Set<string>();
  const rows = new Map<string, Row>();
  let pendingFocus = "";
  let sig = "";

  const listEl = h("ul", { class: "bot-list", "aria-label": "Bots in roster order" });
  const empty = h("p", { class: "muted", hidden: true }, "No bots found. Run hermes r1 doctor to check your Hermes profiles.");

  const showPets = toggle({
    label: "Show pets",
    hint: "Pet sprites beside the highlighted bot on the device.",
    checked: true,
    onChange: (v) =>
      edit((d) => {
        d.roster.show_pets = v;
      }),
  });
  const activeStrip = toggle({
    label: "Show 'Active now' strip",
    hint: "Avatars of bots that are busy or were active recently.",
    checked: true,
    onChange: (v) =>
      edit((d) => {
        d.roster.active_strip = v;
      }),
  });

  function setHidden(id: string, hidden: boolean): void {
    edit((d) => {
      const set = new Set(d.roster.hidden);
      if (hidden) set.add(id);
      else set.delete(id);
      d.roster.hidden = Array.from(set);
    });
  }

  function move(id: string, delta: number): void {
    const ids = currentOrder();
    const i = ids.indexOf(id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    pendingFocus = (delta < 0 ? "up-" : "down-") + id;
    edit((d) => {
      d.roster.order = ids;
    });
  }

  function setAvatarOverride(id: string, key: "shape" | "color", value: string | undefined): void {
    edit((d) => {
      const cur: { shape?: string; color?: string } = { ...(d.avatar_overrides[id] ?? {}) };
      if (value === undefined) delete cur[key];
      else cur[key] = value;
      if (cur.shape === undefined && cur.color === undefined) delete d.avatar_overrides[id];
      else d.avatar_overrides[id] = cur;
    });
  }

  function toggleExpanded(id: string): void {
    if (expanded.has(id)) expanded.delete(id);
    else expanded.add(id);
    sync();
  }

  function buildRow(b: Bot): Row {
    const id = b.id;
    const name = b.name || id;
    const holder = h("span", { class: "bot-avatar" });
    const status = h("span", { class: "bot-status" });
    const lock = h("span", { class: "lock", role: "img" }, "🔒");
    const hiddenToggle = toggle({ label: "Hidden", checked: false, onChange: (v) => setHidden(id, v) });
    const up = button("▲", () => move(id, -1), {
      variant: "icon",
      attrs: { "aria-label": `Move ${name} up`, "data-fk": "up-" + id },
    });
    const down = button("▼", () => move(id, 1), {
      variant: "icon",
      attrs: { "aria-label": `Move ${name} down`, "data-fk": "down-" + id },
    });
    const expand = button("Avatar ▸", () => toggleExpanded(id), {
      variant: "ghost",
      attrs: { "aria-expanded": "false" },
    });

    // Avatar editor
    const shapePicks = new Map<string, HTMLButtonElement>();
    const shapeDefault = button("Hermes default", () => setAvatarOverride(id, "shape", undefined), {
      variant: "chip",
      attrs: { "aria-pressed": "false" },
    });
    shapePicks.set("", shapeDefault);
    const shapeRow = h("div", { class: "chip-row", role: "group", "aria-label": "Shape" }, shapeDefault);
    for (const s of SHAPES) {
      const pick = h("button", {
        type: "button",
        class: "shape-pick",
        "aria-pressed": "false",
        "aria-label": s,
        title: s,
        onclick: () => setAvatarOverride(id, "shape", s),
      });
      shapePicks.set(s, pick);
      shapeRow.append(pick);
    }

    const colorPicks = new Map<string, HTMLButtonElement>();
    const colorDefault = button("Hermes default", () => setAvatarOverride(id, "color", undefined), {
      variant: "chip",
      attrs: { "aria-pressed": "false" },
    });
    colorPicks.set("", colorDefault);
    const colorRow = h("div", { class: "chip-row", role: "group", "aria-label": "Color" }, colorDefault);
    SWATCHES.forEach((c, i) => {
      const pick = h("button", {
        type: "button",
        class: "swatch",
        style: `background:${c}`,
        "aria-pressed": "false",
        "aria-label": `Color ${i + 1}`,
        title: c,
        onclick: () => setAvatarOverride(id, "color", c),
      });
      colorPicks.set(c, pick);
      colorRow.append(pick);
    });
    const colorInput = h("input", {
      type: "color",
      class: "color-input",
      "aria-label": "Custom color",
      oninput: (e: Event) => setAvatarOverride(id, "color", (e.target as HTMLInputElement).value),
    });
    const imageNote = h("p", { class: "hint", hidden: true }, "Uses the photo set in Hermes Desktop.");
    const pickers = h(
      "div",
      { class: "pickers" },
      h("span", { class: "picker-label" }, "Shape"),
      shapeRow,
      h("span", { class: "picker-label" }, "Color"),
      colorRow,
      h("label", { class: "field inline" }, h("span", { class: "field-label" }, "Custom"), colorInput),
    );
    const editor = h("div", { class: "bot-editor", hidden: true }, imageNote, pickers);

    const li = h(
      "li",
      { class: "bot", "data-id": id },
      h(
        "div",
        { class: "bot-row" },
        h("div", { class: "reorder" }, up, down),
        holder,
        h(
          "div",
          { class: "bot-meta" },
          h(
            "div",
            { class: "bot-name" },
            name,
            b.is_default ? h("span", { class: "badge" }, "default") : null,
          ),
          h("div", { class: "bot-sub" }, [id, b.title, b.model].filter(Boolean).join(" · ")),
          status,
        ),
        lock,
        hiddenToggle.el,
        expand,
      ),
      editor,
    );

    return {
      li,
      holder,
      avatarKey: "",
      avatarEl: null,
      previewColor: "",
      status,
      lock,
      hiddenInput: hiddenToggle.input,
      up,
      down,
      expand,
      editor,
      pickers,
      imageNote,
      colorInput,
      shapePicks,
      colorPicks,
    };
  }

  function rebuild(): void {
    const byId = new Map(getAdmin().bots.map((b) => [b.id, b] as const));
    rows.clear();
    const built: HTMLElement[] = [];
    for (const id of currentOrder()) {
      const b = byId.get(id);
      if (!b) continue;
      const row = buildRow(b);
      rows.set(id, row);
      built.push(row.li);
    }
    listEl.replaceChildren(...built);
    if (pendingFocus) {
      focusKey(listEl, pendingFocus);
      pendingFocus = "";
    }
  }

  function updateRow(row: Row, b: Bot, index: number, count: number): void {
    const d = getDraft();
    const eff = effectiveAvatar(b);

    const key = [eff.kind, eff.shape, eff.color].join("|");
    if (key !== row.avatarKey || !row.avatarEl) {
      row.avatarKey = key;
      const el = renderAvatar(eff, { botId: b.id, size: 34, state: b.state });
      row.holder.replaceChildren(el);
      row.avatarEl = el;
    }
    if (row.avatarEl) setAvatarState(row.avatarEl, b.state);

    if (row.previewColor !== eff.color) {
      row.previewColor = eff.color;
      for (const [s, btn] of row.shapePicks) {
        if (!s) continue;
        btn.replaceChildren(
          renderAvatar({ kind: "shape", shape: s, color: eff.color, url: "" }, { botId: b.id, size: 26, animate: false }),
        );
      }
    }

    const o = d.avatar_overrides[b.id] ?? {};
    for (const [k, btn] of row.shapePicks) setPressed(btn, k === "" ? o.shape === undefined : o.shape === k);
    for (const [k, btn] of row.colorPicks) setPressed(btn, k === "" ? o.color === undefined : o.color === k);
    setValue(row.colorInput, hexOf(eff.color));

    const isImage = b.avatar.kind === "image";
    row.pickers.hidden = isImage;
    row.imageNote.hidden = !isImage;

    const hiddenNow = d.roster.hidden.includes(b.id);
    row.hiddenInput.checked = hiddenNow;
    row.li.classList.toggle("is-hidden", hiddenNow);

    row.lock.hidden = b.reachable;
    row.lock.title = b.locked_reason;
    row.lock.setAttribute("aria-label", "Locked: " + (b.locked_reason || "not reachable"));
    row.li.classList.toggle("is-locked", !b.reachable);
    row.status.textContent = statusText(b.state);
    row.status.className = "bot-status " + b.state;

    row.up.disabled = index <= 0;
    row.down.disabled = index >= count - 1;

    const open = expanded.has(b.id);
    row.expand.setAttribute("aria-expanded", String(open));
    row.expand.textContent = open ? "Avatar ▾" : "Avatar ▸";
    row.editor.hidden = !open;
  }

  function sync(): void {
    const a = getAdmin();
    const d = getDraft();
    const order = currentOrder();
    const nextSig = JSON.stringify([order, a.bots.map((b) => [b.id, b.name, b.title, b.model, b.is_default, b.avatar.kind])]);
    if (nextSig !== sig) {
      sig = nextSig;
      rebuild();
    }
    empty.hidden = a.bots.length > 0;
    showPets.input.checked = d.roster.show_pets;
    activeStrip.input.checked = d.roster.active_strip;
    for (const b of a.bots) {
      const row = rows.get(b.id);
      if (row) updateRow(row, b, order.indexOf(b.id), order.length);
    }
  }

  const el = h(
    "div",
    { class: "stack-lg" },
    card("Roster options", h("div", { class: "switch-grid" }, showPets.el, activeStrip.el)),
    card(
      "Bots",
      h(
        "p",
        { class: "muted" },
        "Hidden bots don't appear on the R1. Use ▲ ▼ to set the order. Expand Avatar to change the shape and color.",
      ),
      empty,
      listEl,
    ),
  );

  onChange(sync);
  sync();
  return { el, sync };
}
