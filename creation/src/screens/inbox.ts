// Inbox: notification cards. Click opens the bot that sent the item, if it is known.
import { h, List, relativeTime } from "../ui";
import type { AppEvent } from "../platform";
import type { Screen } from "../nav";
import { state, botById } from "../state";
import { toast } from "../chrome";
import { openChat } from "../app";
import { screenEl } from "./base";

export function createInbox(): Screen {
  const el = screenEl("inbox", "Inbox");
  const listEl = h("div", { class: "inbox-list" });
  el.appendChild(listEl);
  const items = state.inbox.slice(0, 40);

  const list = new List(listEl, (i) => {
    const it = items[i];
    if (!it) return;
    if (it.bot && botById(it.bot)) openChat(it.bot);
    else toast(it.title || "Notification");
  });
  list.bindTouch();

  if (!items.length) {
    listEl.appendChild(h("div", { class: "empty" }, "No notifications"));
  } else {
    const rows = items.map((it) =>
      h(
        "div",
        { class: "card lv-" + (it.level || "info") },
        h("div", { class: "card-t" }, it.title || "Notification"),
        it.body ? h("div", { class: "card-b" }, it.body) : null,
        h(
          "div",
          { class: "card-f" },
          botById(it.bot)?.name || it.bot || "Hermes",
          " · " + relativeTime(it.ts),
        ),
      ),
    );
    list.setRows(rows, false);
  }

  return {
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
      return false;
    },
  };
}
