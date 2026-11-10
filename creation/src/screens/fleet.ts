// Device-scoped Fleet overview. The bridge only returns turns belonging to this paired device.
import type { FleetAsk, FleetData } from "../../../shared/protocol";
import { h, List } from "../ui";
import type { AppEvent } from "../platform";
import type { Screen } from "../nav";
import { pop } from "../nav";
import { state, botById } from "../state";
import { guard } from "../app";
import { toast } from "../chrome";
import { openSheet } from "./menu";
import { openType } from "./type";

type Entry = { kind: "heading"; label: string } | { kind: "ask"; turnId: string; botId: string; ask: FleetAsk }
  | { kind: "working"; turnId: string; botId: string } | { kind: "gateway"; id: string; name: string; status: string };

function askKey(turnId: string, requestId: string): string { return `${turnId}\u001f${requestId}`; }
function entryKey(entry: Entry): string {
  if (entry.kind === "ask") return `ask:${askKey(entry.turnId, entry.ask.request_id)}`;
  if (entry.kind === "working") return `working:${entry.turnId}:${entry.botId}`;
  if (entry.kind === "gateway") return `gateway:${entry.id}`;
  return `heading:${entry.label}`;
}

function botLabel(botId: string): string {
  const bot = botById(botId);
  if (!bot) return botId;
  const duplicates = state.bots.filter((b) => b.name === bot.name);
  return duplicates.length > 1 ? `${bot.name} · ${bot.gateway_name || bot.gateway_id || "Local"}` : bot.name;
}

export function createFleet(): Screen {
  const el = h("div", { class: "scr fleet", "data-screen": "fleet-now" });
  const header = h("div", { class: "fleet-head" },
    h("button", { class: "fleet-back", type: "button", "aria-label": "Back to Home", onclick: () => pop() }, "‹ Home"),
    h("div", { class: "fleet-title" }, "Fleet Now"),
    h("button", { class: "fleet-refresh", type: "button", "aria-label": "Refresh Fleet", onclick: () => void refresh() }, "↻"),
  );
  const summary = h("div", { class: "fleet-summary", role: "status" }, "Loading Fleet…");
  const listEl = h("div", { class: "fleet-list" });
  el.append(header, summary, listEl);

  let data: FleetData = { needs_you: [], working: [], gateways: [] };
  let entries: Entry[] = [];
  let timer = 0;
  let active = false;
  let loading = false;
  let lastError = "";
  const responding = new Set<string>();
  const answered = new Set<string>();
  const list = new List(listEl, (i) => activate(i));
  list.bindTouch();

  function render(): void {
    const focused = entries[list.index] ? entryKey(entries[list.index]) : "";
    const needs = data.needs_you || [];
    const working = data.working || [];
    const gateways = data.gateways || [];
    entries = [
      { kind: "heading", label: `Needs You · ${needs.length}` },
      ...needs.map((item): Entry => ({ kind: "ask", turnId: item.turn_id, botId: item.bot_id, ask: item.ask })),
      { kind: "heading", label: `Working · ${working.length}` },
      ...working.map((item): Entry => ({ kind: "working", turnId: item.turn_id, botId: item.bot_id })),
      { kind: "heading", label: `Gateways · ${gateways.length}` },
      ...gateways.map((g): Entry => ({ kind: "gateway", id: g.id, name: g.name, status: g.status })),
    ];
    summary.textContent = lastError || (loading && !data.needs_you.length && !data.working.length && !data.gateways.length
      ? "Loading Fleet…" : `Needs You ${needs.length} · Working ${working.length} · Gateways ${gateways.length}`);
    list.setRows(entries.map((entry) => {
      if (entry.kind === "heading") return h("div", { class: "fleet-section", "aria-hidden": "true" }, entry.label);
      if (entry.kind === "ask") return h("div", { class: "fleet-row fleet-ask", "data-request-id": entry.ask.request_id },
        h("div", { class: "fleet-row-main" },
          h("div", { class: "fleet-row-title" }, entry.ask.title || (entry.ask.kind === "approval" ? "Approval needed" : "Question")),
          h("div", { class: "fleet-row-sub" }, `${botLabel(entry.botId)} · ${entry.ask.kind === "approval" ? "Approval" : "Question"}`),
        ), h("span", { class: "fleet-chevron" }, "›"));
      if (entry.kind === "working") return h("div", { class: "fleet-row", "data-turn-id": entry.turnId, "aria-label": "Working turn" },
        h("div", { class: "fleet-row-main" }, h("div", { class: "fleet-row-title" }, botLabel(entry.botId)), h("div", { class: "fleet-row-sub" }, "Turn in progress")),
        h("span", { class: "fleet-status unknown" }, "Working"));
      const cls = entry.status === "online" ? "online" : entry.status === "offline" ? "offline" : "unknown";
      return h("div", { class: "fleet-row fleet-gateway" }, h("div", { class: "fleet-row-main" },
        h("div", { class: "fleet-row-title" }, entry.name || entry.id), h("div", { class: "fleet-row-sub" }, entry.id)),
        h("span", { class: "fleet-status " + cls }, entry.status));
    }), true);
    const restored = entries.findIndex((entry) => entryKey(entry) === focused);
    if (restored >= 0) list.index = restored;
    else if (list.index < entries.length && entries[list.index]?.kind === "heading") list.index = Math.min(list.index + 1, Math.max(0, entries.length - 1));
    list.highlight();
  }

  async function refresh(): Promise<void> {
    if (!active || loading || !state.api) return;
    loading = true;
    lastError = "";
    render();
    try {
      const next = await guard(state.api.fleet());
      if (!active) return;
      data = next;
      const liveReqs = new Set(data.needs_you.map((x) => askKey(x.turn_id, x.ask.request_id)));
      for (const id of Array.from(answered)) if (!liveReqs.has(id)) answered.delete(id);
      render();
    } catch (e) {
      if (!active) return;
      lastError = e instanceof Error ? `${e.message} · Tap ↻ to retry` : "Fleet unavailable · Tap ↻ to retry";
      render();
    } finally {
      loading = false;
    }
  }

  function activate(i: number): void {
    const entry = entries[i];
    if (!entry || entry.kind === "heading" || entry.kind === "gateway") return;
    if (entry.kind === "working") return;
    const key = askKey(entry.turnId, entry.ask.request_id);
    if (answered.has(key) || responding.has(key)) return;
    const label = botLabel(entry.botId);
    if (entry.ask.kind === "clarify" && !(entry.ask.choices || []).length) {
      openType({ title: entry.ask.title || `Question · ${label}`, placeholder: entry.ask.detail || "Type your answer", onSend: (answer) => void respond(entry, { answer }) });
      return;
    }
    const choices = entry.ask.choices || [];
    if (!choices.length) {
      toast("This approval has no available choices");
      return;
    }
    openSheet({ title: `${entry.ask.title || "Approval"} · ${label}`, detail: entry.ask.detail, rows: choices.map((choice) => ({
      label: choice.label, cls: choice.id === "deny" ? "err" : "", onPick: () => void respond(entry, { choice: choice.id }),
    })) });
  }

  async function respond(entry: Extract<Entry, { kind: "ask" }>, reply: { choice?: string; answer?: string }): Promise<void> {
    const reqId = entry.ask.request_id;
    const key = askKey(entry.turnId, reqId);
    const current = data.needs_you.find((x) => x.turn_id === entry.turnId && x.ask.request_id === reqId);
    if (!current || current.ask.kind !== entry.ask.kind) {
      toast("That request is no longer current");
      void refresh();
      return;
    }
    if (responding.has(key) || answered.has(key) || !state.api) return;
    responding.add(key);
    toast("Sending answer…");
    try {
      await guard(state.api.respond(entry.turnId, reqId, reply));
      answered.add(key);
      void refresh();
    } catch (e) {
      const message = e instanceof Error ? e.message : "Could not send your answer";
      toast(message);
      void refresh();
    } finally {
      responding.delete(key);
    }
  }

  return {
    el,
    onShow() {
      active = true;
      void refresh();
      timer = window.setInterval(() => void refresh(), 5000);
    },
    onHide() {
      active = false;
      if (timer) window.clearInterval(timer);
      timer = 0;
    },
    onInput(ev: AppEvent): boolean {
      if (ev === "up" || ev === "down") {
        const delta = ev === "up" ? -1 : 1;
        let next = list.index;
        for (let n = 0; n < entries.length; n++) {
          next = (next + delta + entries.length) % entries.length;
          if (entries[next]?.kind !== "heading") break;
        }
        list.index = next;
        list.highlight();
        return true;
      }
      if (ev === "click") { list.activate(); return true; }
      if (ev === "menu" || ev === "back") { pop(); return true; }
      return false;
    },
  };
}
