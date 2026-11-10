// Settings: local theme preview, font size (read-only, set in Studio), Diagnostics, Unpair, About.
import { h, List } from "../ui";
import type { AppEvent } from "../platform";
import type { Screen } from "../nav";
import { THEME_NAMES } from "../../../shared/themes";
import { state } from "../state";
import { toast } from "../chrome";
import { applyAppTheme, openDiagnostics, unpair } from "../app";
import { row, screenEl } from "./base";

const APP_VERSION = "0.1.0";

export function createSettings(): Screen {
  const el = screenEl("settings", "Settings");
  const listEl = h("div", { class: "set-list" });
  el.appendChild(listEl);

  let preview = state.config.theme.preset;
  let unpairArmed = false;

  type Entry = { title: string; sub: string; right: string; onPick: () => void };
  let entries: Entry[] = [];

  const list = new List(listEl, (i) => {
    const e = entries[i];
    if (e) e.onPick();
  });
  list.bindTouch();

  function build(): void {
    const scale = Math.round((state.config.font_scale || 1) * 100) + "%";
    entries = [
      {
        title: "Theme",
        sub: "Local preview. Change permanently in Studio",
        right: preview,
        onPick: () => {
          const idx = THEME_NAMES.indexOf(preview);
          preview = THEME_NAMES[(idx + 1) % THEME_NAMES.length];
          applyAppTheme(preview);
          build();
        },
      },
      {
        title: "Font size",
        sub: "Set in Studio",
        right: scale,
        onPick: () => toast("Change font size in Studio"),
      },
      {
        title: "Diagnostics",
        sub: "Check this device's capabilities",
        right: "",
        onPick: () => openDiagnostics(),
      },
      {
        title: unpairArmed ? "Tap again to unpair" : "Unpair",
        sub: unpairArmed ? "Clears this device's token" : "Remove this R1 from the bridge",
        right: "",
        onPick: () => {
          if (!unpairArmed) {
            unpairArmed = true;
            build();
            return;
          }
          void unpair();
        },
      },
      {
        title: "About",
        sub: "Hermes R1 " + APP_VERSION + " · " + (state.bootstrap ? state.bootstrap.server.version : "bridge ?"),
        right: "",
        onPick: () => undefined,
      },
    ];
    list.setRows(
      entries.map((e) => row({ title: e.title, sub: e.sub, right: e.right || undefined, cls: "sheet-row" })),
      true,
    );
  }

  build();

  return {
    el,
    onShow() {
      list.highlight();
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
}
