// Saved prompt launcher. Routine IDs and target IDs come from device-owned Studio settings.
import { ApiError } from "../../../shared/api";
import type { Routine } from "../../../shared/protocol";
import { h, List } from "../ui";
import type { AppEvent } from "../platform";
import type { Screen } from "../nav";
import { current, pop } from "../nav";
import { gatewayLabel, state } from "../state";
import { toast } from "../chrome";
import { guard, sendTo } from "../app";
import { openGroupRoomWithMessage } from "./groups";
import { row, screenEl } from "./base";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Can't reach the bridge";
}

export function createRoutines(): Screen {
  const el = screenEl("routines", "Routines");
  const status = h("div", { class: "fleet-summary", role: "status" }, "Loading saved routines…");
  const listEl = h("div", { class: "set-list" });
  el.append(status, listEl);

  let routines: Routine[] = [];
  let groupNames = new Map<string, string>();
  let groupsLoaded = false;
  let active = false;
  let loadGeneration = 0;
  const list = new List(listEl, (index) => activate(index));
  list.bindTouch();

  function render(): void {
    if (!routines.length) {
      list.setRows([row({ title: "No saved routines", sub: "Add one in Studio" })], false);
      return;
    }
    list.setRows(routines.map((routine) => {
      let target = groupNames.get(routine.target.id) || routine.target.id;
      if (routine.target.kind === "bot") {
        const bot = state.bots.find((candidate) => candidate.id === routine.target.id);
        target = bot ? bot.name : routine.target.id;
        if (bot && state.bots.some((candidate) => candidate.id !== bot.id && candidate.name === bot.name)) {
          target = `${bot.name} · ${gatewayLabel(bot)}`;
        }
      }
      return row({ title: routine.label, sub: `${routine.target.kind === "bot" ? "Bot" : "Group"} · ${target}`, right: "›" });
    }), false);
  }

  function fail(message: string): void {
    status.textContent = message;
    toast(message);
  }

  function activate(index: number): void {
    if (!active || current() !== screen) return;
    const routine = routines[index];
    if (!routine) return;
    const prompt = routine.text.trim();
    if (!prompt) return fail("This routine has no prompt text");

    if (routine.target.kind === "bot") {
      const bot = state.bots.find((candidate) => candidate.id === routine.target.id);
      if (!bot) return fail("Saved bot is unavailable");
      if (!bot.reachable) return fail(bot.locked_reason || "Saved bot is unavailable");
      status.textContent = `Opening ${bot.name}…`;
      sendTo(bot.id, prompt);
      return;
    }

    if (!groupsLoaded) return fail("Can't verify the saved group right now");
    const name = groupNames.get(routine.target.id);
    if (!name) return fail("Saved group no longer exists");
    status.textContent = `Opening ${name}…`;
    openGroupRoomWithMessage(routine.target.id, name, prompt);
  }

  async function load(): Promise<void> {
    const generation = ++loadGeneration;
    const api = state.api;
    routines = [];
    groupNames = new Map();
    groupsLoaded = false;
    list.setRows([row({ title: "Loading saved routines…" })], false);
    if (!api) {
      status.textContent = "Bridge unavailable";
      list.setRows([row({ title: "Bridge unavailable" })], false);
      return;
    }
    status.textContent = "Loading saved routines…";
    const groupsTask = guard(api.groups()).then(({ groups }) => {
      groupsLoaded = true;
      groupNames = new Map(groups.map((group) => [group.id, group.name]));
    }).catch(() => {
      groupsLoaded = false;
    });
    try {
      const result = await guard(api.routines());
      await groupsTask;
      if (generation !== loadGeneration || !active || current() !== screen) return;
      routines = result.routines || [];
      status.textContent = routines.length ? "Choose a saved prompt" : "No saved routines · Add one in Studio";
      render();
    } catch (error) {
      await groupsTask;
      if (generation !== loadGeneration || !active || current() !== screen) return;
      const unavailable = error instanceof ApiError && error.status === 404;
      const message = unavailable ? "Saved routines aren't available on this bridge" : errorMessage(error);
      status.textContent = message;
      list.setRows([row({ title: unavailable ? "Update Hermes Studio to use routines" : "Routines unavailable", sub: message })], false);
    }
  }

  const screen: Screen = {
    el,
    onShow() {
      active = true;
      void load();
    },
    onHide() { active = false; },
    onInput(ev: AppEvent): boolean {
      if (ev === "up") { list.move(-1); return true; }
      if (ev === "down") { list.move(1); return true; }
      if (ev === "click") { list.activate(); return true; }
      if (ev === "back" || ev === "menu") { pop(); return true; }
      return false;
    },
  };
  return screen;
}
