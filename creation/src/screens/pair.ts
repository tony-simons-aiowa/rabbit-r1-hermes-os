// Pair screen: shown when there is no device token.
import { h, List } from "../ui";
import type { Screen } from "../nav";
import type { AppEvent } from "../platform";
import { Api, ApiError } from "../../../shared/api";
import { state, saveToken } from "../state";
import { openHome, bootstrapDevice } from "../app";
import { openType } from "./type";
import { row, screenEl } from "./base";

const DEVICE_NAME = "Rabbit R1";

/** Exchange a pairing code for a token, then load bootstrap and go Home. Returns an error message or null. */
export async function pairWith(rawCode: string): Promise<string | null> {
  const code = rawCode.trim().toUpperCase();
  if (!code) return "Enter the pairing code from Studio";
  try {
    const api = new Api({ token: "" });
    const res = await api.pair(code, DEVICE_NAME);
    await saveToken(res.token);
    state.token = res.token;
    state.deviceName = res.device.name;
    state.api = new Api({ token: res.token });
    await bootstrapDevice();
    openHome();
    return null;
  } catch (e) {
    if (e instanceof ApiError) {
      if (e.status === 400) return "That code is wrong or expired";
      if (e.status === 429) return "Too many tries. Wait a few minutes.";
      return e.message;
    }
    return "Something went wrong";
  }
}

export function createPair(message?: string): Screen {
  const el = screenEl("pair");
  el.appendChild(h("div", { class: "pair-logo" }, "Hermes"));
  const msg = h(
    "div",
    { class: "pair-msg" },
    message || "Open Studio on your computer and scan the QR code to pair this R1.",
  );
  el.appendChild(msg);
  const list = new List(h("div", { class: "pair-list" }), (i) => {
    if (i === 0) {
      openType({
        title: "Pairing code",
        placeholder: "ABCD2345",
        onSend: (text) => {
          msg.textContent = "Pairing…";
          void pairWith(text).then((err) => {
            if (err) msg.textContent = err;
          });
        },
      });
    }
  });
  list.bindTouch();
  el.appendChild(list.el);
  list.setRows([row({ title: "Enter pairing code", cls: "sheet-row" })], false);

  const screen: Screen = {
    el,
    onShow() {
      list.highlight();
    },
    onInput(ev: AppEvent): boolean {
      if (ev === "up" || ev === "down") {
        list.move(ev === "up" ? -1 : 1);
        return true;
      }
      if (ev === "click") {
        list.activate();
        return true;
      }
      return false;
    },
  };
  return screen;
}
