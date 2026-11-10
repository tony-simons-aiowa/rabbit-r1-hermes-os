import type { GroupChat } from "../../../shared/protocol";
import { guard, openFleet } from "../app";
import { toast } from "../chrome";
import { current, pop, push, type Screen } from "../nav";
import { state } from "../state";
import { h, List } from "../ui";
import { row, screenEl } from "./base";
import { openType } from "./type";

function header(title: string): HTMLElement {
  return h("div", { class: "fleet-head" },
    h("button", { class: "fleet-back", type: "button", onclick: pop }, "‹ Back"),
    h("div", { class: "fleet-title" }, title));
}

function menu(title: string, pick: (i: number) => void) {
  const el = screenEl("groups");
  const status = h("div", { class: "fleet-summary", role: "status" });
  const list = new List(h("div", { class: "set-list" }), pick);
  list.bindTouch();
  el.append(header(title), status, list.el);
  const screen: Screen = {
    el,
    onInput(ev) {
      if (ev === "up" || ev === "down") list.move(ev === "up" ? -1 : 1);
      else if (ev === "click") list.activate();
      else return false;
      return true;
    },
  };
  return { screen, list, status };
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : "Can't reach the bridge";
}

export function createGroups(): Screen {
  let actions: Array<() => void> = [];
  const { screen, list, status } = menu("Group Chats", (i) => actions[i]?.());
  screen.onShow = () => {
    const api = state.api;
    if (!api) return;
    status.textContent = "Loading…";
    actions = [];
    list.setRows([]);
    void guard(api.groups()).then(({ groups }) => {
      if (current() !== screen) return;
      status.textContent = groups.length ? "Your conversations" : "Start a conversation with 2–4 bots.";
      actions = [openPicker, ...groups.map((group) => () => showRoom(group.id, group.name))];
      list.setRows([row({ title: "New group", right: "+" }), ...groups.map((group) =>
        row({ title: group.name, sub: group.members.map((member) => member.name).join(", "), right: "›" }))]);
    }).catch((error) => {
      status.textContent = message(error);
      actions = [() => screen.onShow?.()];
      list.setRows([row({ title: "Try again" })]);
    });
  };
  return screen;
}

function openPicker(): void {
  const bots = state.bots.filter((bot) => bot.reachable);
  if (bots.length < 2) return toast("At least two bots must be online");
  const selected = new Set<string>();
  let saving = false;
  const { screen, list, status } = menu("Choose bots", (i) => {
    if (saving) return;
    if (i === 0) {
      if (selected.size < 2) return toast("Choose at least two bots");
      openType({ title: "Name this group", placeholder: "Group name", maxLength: 40, onSend: (name) => {
        const api = state.api;
        if (!api || saving) return;
        saving = true;
        void guard(api.createGroup(name, [...selected])).then((group) => {
          if (current() !== screen) return;
          pop();
          showRoom(group.id, group.name);
        }).catch((error) => toast(message(error))).finally(() => { saving = false; });
      } });
      return;
    }
    const bot = bots[i - 1];
    if (selected.has(bot.id)) selected.delete(bot.id);
    else if (selected.size < 4) selected.add(bot.id);
    else return toast("Choose up to four bots");
    render();
  });
  function render(): void {
    status.textContent = selected.size + " selected · Recent chat is shared with every member";
    list.setRows([row({ title: "Name group", right: "›" }), ...bots.map((bot) =>
      row({ title: bot.name, sub: bot.gateway_name || "Local", right: selected.has(bot.id) ? "✓" : "+" }))]);
  }
  render();
  push(screen);
}

function showRoom(id: string, name: string): void {
  const el = screenEl("groups");
  const status = h("div", { class: "fleet-summary", role: "status" }, "Loading…");
  const transcript = h("div", { class: "group-transcript" });
  const action = h("button", { class: "group-action", type: "button", onclick: act }, "Type message");
  el.append(header(name), status, transcript, action);
  let room: GroupChat | undefined;
  let timer: ReturnType<typeof setTimeout>;
  let visible = false;
  let reading = false;
  let sending = false;

  function pending(): boolean {
    return room?.rounds[room.rounds.length - 1]?.replies.some((reply) => reply.status === "pending") ?? false;
  }
  function render(next: GroupChat): void {
    if (JSON.stringify(room) !== JSON.stringify(next)) {
      const atBottom = !room || transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 12;
      const offset = transcript.scrollTop;
      room = next;
      const members = new Map(next.members.map((member) => [member.id, member]));
      transcript.replaceChildren(...next.rounds.flatMap((round) => [
        entry("You", round.text),
        ...round.replies.map((reply) => {
          const member = members.get(reply.bot);
          return entry(member ? member.name + " · " + member.gateway : reply.bot,
            reply.status === "pending" ? "Waiting for reply…" : reply.error || reply.text || "No reply");
        }),
      ]));
      transcript.scrollTop = atBottom ? transcript.scrollHeight : offset;
    }
    status.textContent = pending() ? "Bots are replying · Approvals in Fleet Now" :
      next.members.map((member) => member.name).join(", ");
    action.textContent = pending() ? "Open Fleet Now" : "Type message";
  }
  function entry(author: string, text: string): HTMLElement {
    return h("div", { class: "group-message" }, h("strong", null, author), h("div", null, text));
  }
  async function refresh(): Promise<void> {
    const api = state.api;
    if (!visible || reading || sending || !api) return;
    clearTimeout(timer);
    reading = true;
    let retry = false;
    try {
      const next = await guard(api.group(id));
      if (visible && !sending) render(next);
    } catch (error) {
      if (visible) status.textContent = message(error);
      retry = true;
    } finally {
      reading = false;
      if (visible && !sending && (pending() || retry)) timer = setTimeout(() => void refresh(), 1500);
    }
  }
  function act(): void {
    if (pending()) return openFleet();
    if (!room || sending) return;
    openType({ title: name, quick: [{
      label: "Test group chat",
      text: "Say hello to the group in one short sentence and include your bot name. Reply in text only. Do not use tools or change anything.",
    }], onSend: (text) => {
      const api = state.api;
      if (!api || sending) return;
      sending = true;
      clearTimeout(timer);
      status.textContent = "Sending…";
      void guard(api.sendGroup(id, text)).then((next) => {
        if (visible) render(next);
      }).catch((error) => toast(message(error))).finally(() => {
        sending = false;
        void refresh();
      });
    } });
  }
  push({
    el,
    onShow() { visible = true; void refresh(); },
    onHide() { visible = false; clearTimeout(timer); },
    onInput(ev) {
      if (ev === "up" || ev === "down") transcript.scrollTop += ev === "up" ? -38 : 38;
      else if (ev === "click") act();
      else return false;
      return true;
    },
  });
}
