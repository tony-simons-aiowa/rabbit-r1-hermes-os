// Sessions: "New chat" then the bot's recent sessions. Picking one resumes it in the open chat.
import { h, List, relativeTime } from "../ui";
import type { AppEvent } from "../platform";
import { pop, current, type Screen } from "../nav";
import type { Session } from "../../../shared/protocol";
import { state } from "../state";
import { toast } from "../chrome";
import { guard } from "../app";
import { activeChat } from "./chat";
import { row, screenEl } from "./base";

export function createSessions(botId: string): Screen {
  const el = screenEl("sessions", "Sessions");
  const listEl = h("div", { class: "sess-list" });
  el.appendChild(listEl);
  let sessions: Session[] = [];
  let loaded = false;

  const list = new List(listEl, (i) => pick(i));
  list.bindTouch();
  list.setRows([row({ title: "Loading…", cls: "sheet-row dim" })], false);

  const screen: Screen = {
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

  function pick(i: number): void {
    if (!loaded) return;
    if (current() === screen) pop();
    const chat = activeChat();
    if (!chat || chat.botId !== botId) {
      toast("Chat is closed");
      return;
    }
    if (i === 0) chat.resume(null);
    else chat.resume(sessions[i - 1].id);
  }

  if (!state.api) {
    list.setRows([row({ title: "Not paired" })], false);
  } else {
    guard(state.api.sessions(botId, 20))
      .then((res) => {
        sessions = res.sessions || [];
        loaded = true;
        const rows = [row({ title: "＋ New chat", cls: "sheet-row" })];
        for (const s of sessions) {
          rows.push(
            row({
              title: s.title || "Untitled",
              sub: s.preview || "",
              right: relativeTime(s.last_active),
              cls: "sheet-row",
            }),
          );
        }
        list.setRows(rows, false);
      })
      .catch(() => {
        list.setRows([row({ title: "Couldn't load sessions", cls: "sheet-row dim" })], false);
      });
  }
  return screen;
}
