// Home / roster: optional greeting, "active now" strip, bot rows, then Inbox and Settings.
import { h, List } from "../ui";
import type { AppEvent } from "../platform";
import type { Screen } from "../nav";
import { renderAvatar, setAvatarState, renderPet, petMoodFor } from "../../../shared/avatar";
import type { Bot } from "../../../shared/protocol";
import { state, subscribe, orderedBots, rememberBot, availableGateways, selectedGateway, rememberGateway, gatewayId, unpinBot } from "../state";
import { refreshChrome, toast } from "../chrome";
import { openChat, openInbox, openFleet, openGroups, openRoutines, openSettings, talk, flags } from "../app";
import { canTalk, openListening } from "./listening";
import { openGroupRoom } from "./groups";
import { openSheet } from "./menu";

const PET_HEIGHT = 30;

type Item = { kind: "bot"; bot: Bot } | { kind: "pinnedBot"; bot: Bot } | { kind: "pinnedGroup"; id: string; name: string } | { kind: "gateway" } | { kind: "fleet" } | { kind: "groups" } | { kind: "routines" } | { kind: "inbox" } | { kind: "settings" };

export function createHome(): Screen {
  const el = h("div", { class: "scr home" });
  const greet = h("div", { class: "greet" });
  const strip = h("div", { class: "strip" });
  const listEl = h("div", { class: "home-list" });
  el.append(greet, strip, listEl);

  let items: Item[] = [];
  let avatars: (HTMLElement | null)[] = [];
  let petEl: HTMLElement | null = null;
  let sig = "";

  const list = new List(
    listEl,
    (i) => activate(i),
    () => syncHighlight(),
  );
  list.bindTouch();

  function botOf(i: number): Bot | null {
    const it = items[i];
    return it && (it.kind === "bot" || it.kind === "pinnedBot") ? it.bot : null;
  }

  function syncHighlight(): void {
    const idx = list.index;
    avatars.forEach((av, i) => {
      if (!av) return;
      const b = botOf(i);
      const state = b ? b.state : "idle";
      setAvatarState(av, state, false, i === idx || state !== "idle");
    });
    const b = botOf(idx);
    state.currentBot = b ? b.id : "";
    refreshChrome();
    if (petEl) petEl.remove();
    petEl = null;
    const row = list.rows[idx];
    if (b && b.pet && state.config.roster.show_pets && row) {
      petEl = renderPet(b.pet, PET_HEIGHT, petMoodFor(b.state));
      petEl.classList.add("pet-slot");
      row.appendChild(petEl);
    }
  }

  function activate(i: number): void {
    const it = items[i];
    if (!it) return;
    if (it.kind === "gateway") {
      const gateways = availableGateways();
      if (gateways.length > 1) {
        const current = gateways.findIndex((g) => g.id === selectedGateway());
        rememberGateway(gateways[(current + 1) % gateways.length].id);
        render(true);
      }
      return;
    }
    if (it.kind === "fleet") return openFleet();
    if (it.kind === "groups") return openGroups();
    if (it.kind === "routines") return openRoutines();
    if (it.kind === "pinnedGroup") { openGroupRoom(it.id, it.name); return; }
    if (it.kind === "inbox") return openInbox();
    if (it.kind === "settings") return openSettings();
    const bot = it.bot;
    if (!bot.reachable) {
      if (it.kind === "pinnedBot") {
        openSheet({ title: bot.name, detail: bot.locked_reason || "This bot is unavailable", rows: [
          { label: "Unpin from Home", onPick: () => { unpinBot(); toast("Unpinned from Home"); } },
        ] });
        return;
      }
      return toast(bot.locked_reason || "This bot is locked");
    }
    rememberBot(bot.id);
    openChat(bot.id);
  }

  function render(force = false): void {
    const gateway = selectedGateway();
    const gateways = availableGateways();
    const allBots = orderedBots();
    const bots = allBots.filter((b) => gatewayId(b) === gateway);
    const pinnedBot = allBots.find((b) => b.id === state.prefs.pinnedBot);
    const pinnedGroup = state.prefs.pinnedGroup;
    const pinAction = state.config.buttons.long_press === "talk"
      ? (canTalk() ? "Hold to talk" : "Voice off")
      : "Hold to open";
    const nIn = state.inbox.length;
    const sigNow = JSON.stringify([
      bots.map((b) => [b.id, b.name, b.state, b.active, b.reachable, b.last && b.last.text, b.pet && b.pet.url, b.avatar]),
      nIn,
      state.config.roster,
      state.config.home.greeting,
      state.config.buttons.long_press,
      gateway,
      gateways,
      state.prefs.pinnedBot,
      pinnedBot && [pinnedBot.id, pinnedBot.name, pinnedBot.state, pinnedBot.reachable, pinnedBot.locked_reason, pinnedBot.avatar, pinnedBot.pet?.url],
      pinnedGroup,
      pinAction,
    ]);
    if (!force && sigNow === sig) return;
    sig = sigNow;

    greet.textContent = state.config.home.greeting || "";
    greet.style.display = state.config.home.greeting ? "" : "none";

    items = bots.map((b): Item => ({ kind: "bot", bot: b }));
    items.unshift({ kind: "fleet" }, { kind: "groups" }, { kind: "routines" });
    if (gateways.length > 1) items.unshift({ kind: "gateway" });
    if (pinnedBot || pinnedGroup) {
      items.unshift(...[
        ...(pinnedGroup ? [{ kind: "pinnedGroup" as const, id: pinnedGroup.id, name: pinnedGroup.name }] : []),
        ...(pinnedBot ? [{ kind: "pinnedBot" as const, bot: pinnedBot }] : []),
      ]);
    }
    if (nIn) items.push({ kind: "inbox" });
    items.push({ kind: "settings" });

    // Active strip: small avatars of bots doing something right now.
    const active = bots.filter((b) => b.active || b.state !== "idle");
    strip.textContent = "";
    if (state.config.roster.active_strip && active.length) {
      strip.style.display = "";
      for (const b of active) {
        strip.appendChild(renderAvatar(b.avatar, { botId: b.id, size: 22, state: b.state, animate: false }));
      }
    } else {
      strip.style.display = "none";
    }

    avatars = [];
    const rows = items.map((it) => {
      if (it.kind === "gateway") {
        avatars.push(null);
        const name = gateways.find((g) => g.id === gateway)?.name || "Local";
        return h("div", { class: "row hrow special gateway-row" }, h("div", { class: "row-main" }, h("div", { class: "row-t" }, "Gateway · " + name), h("div", { class: "row-s" }, "Click to switch")));
      }
      if (it.kind === "pinnedGroup") {
        avatars.push(null);
        const hint = `Pinned group · ${pinAction}`;
        return h("div", { class: "row hrow special pinned-destination" }, h("div", { class: "row-main" }, h("div", { class: "row-t" }, it.name), h("div", { class: "row-s" }, hint)));
      }
      if (it.kind === "fleet") {
        avatars.push(null);
        return h("div", { class: "row hrow special fleet-entry" }, h("div", { class: "row-main" }, h("div", { class: "row-t" }, "Fleet Now"), h("div", { class: "row-s" }, "Needs You · Working · Gateways")));
      }
      if (it.kind === "groups") {
        avatars.push(null);
        return h("div", { class: "row hrow special" }, h("div", { class: "row-main" },
          h("div", { class: "row-t" }, "Group Chats"), h("div", { class: "row-s" }, "Talk with multiple bots")));
      }
      if (it.kind === "routines") {
        avatars.push(null);
        return h("div", { class: "row hrow special" }, h("div", { class: "row-main" },
          h("div", { class: "row-t" }, "Routines"), h("div", { class: "row-s" }, "Run a saved prompt")));
      }
      if (it.kind === "inbox") {
        avatars.push(null);
        return h("div", { class: "row hrow special" }, h("div", { class: "row-main" }, h("div", { class: "row-t" }, "Inbox (" + nIn + ")")));
      }
      if (it.kind === "settings") {
        avatars.push(null);
        return h("div", { class: "row hrow special" }, h("div", { class: "row-main" }, h("div", { class: "row-t" }, "Settings")));
      }
      const b = it.bot;
      const av = renderAvatar(b.avatar, { botId: b.id, size: 34, state: b.state, animate: false });
      avatars.push(av);
      const preview = it.kind === "pinnedBot"
        ? `Pinned bot · ${b.reachable ? pinAction : "Locked"}`
        : b.last ? b.last.text.split("\n")[0] : b.title || b.description || "";
      const tail = b.reachable
        ? h("span", { class: "dot " + b.state })
        : h("span", { class: "lock", "aria-label": "locked" }, "🔒");
      return h(
        "div",
        { class: "row hrow" + (b.reachable ? "" : " locked") + (it.kind === "pinnedBot" ? " pinned-destination" : "") },
        av,
        h("div", { class: "row-main" }, h("div", { class: "row-t" }, b.name), h("div", { class: "row-s" }, preview)),
        tail,
      );
    });
    petEl = null;
    list.setRows(rows, true);
    syncHighlight();
  }

  const screen: Screen = {
    el,
    onShow() {
      flags.homeVisible = true;
      render(true);
      unsub = subscribe(() => render());
    },
    onHide() {
      flags.homeVisible = false;
      if (unsub) unsub();
      unsub = null;
    },
    onInput(ev: AppEvent): boolean {
      switch (ev) {
        case "up":
          list.move(-1);
          return true;
        case "down":
          list.move(1);
          return true;
        case "click":
          list.activate();
          return true;
        case "holdStart": {
          const item = items[list.index];
          if (item?.kind === "pinnedGroup") {
            const sendGroup = openGroupRoom(item.id, item.name);
            if (state.config.buttons.long_press !== "talk") return true;
            if (!state.api) { toast("Bridge unavailable"); return true; }
            if (!canTalk()) { toast("Voice not available — use Type"); return true; }
            openListening({ group: item.name, mode: "hold", onTranscript: sendGroup });
            return true;
          }
          const b = botOf(list.index);
          if (!b) return true;
          if (!b.reachable) {
            toast(b.locked_reason || "This bot is locked");
            return true;
          }
          if (state.config.buttons.long_press !== "talk") {
            openChat(b.id);
            return true;
          }
          if (!canTalk()) {
            toast("Voice not available — use Type");
            return true;
          }
          openChat(b.id);
          talk(b.id, "hold");
          return true;
        }
        case "menu":
          openSettings();
          return true;
        default:
          return false;
      }
    },
    destroy() {
      if (unsub) unsub();
      unsub = null;
      flags.homeVisible = false;
    },
  };
  let unsub: (() => void) | null = null;

  render(true);
  return screen;
}
