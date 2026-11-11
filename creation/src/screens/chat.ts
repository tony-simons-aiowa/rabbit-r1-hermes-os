// Chat: transcript, optimistic sends, streaming turn events, Ask sheet and the chat menu.
import { ApiError, followTurn } from "../../../shared/api";
import type { Bot, Message, Session, TurnEvent } from "../../../shared/protocol";
import { renderAvatar, setAvatarState } from "../../../shared/avatar";
import { toolIcon, toolLabel } from "../../../shared/tools";
import { h, clear } from "../ui";
import type { AppEvent } from "../platform";
import type { Screen } from "../nav";
import { MdView } from "../markdown";
import { state, botById, rememberSession, savePrefs, subscribe, pinBot, unpinBot } from "../state";
import { toast, setOnline, refreshChrome } from "../chrome";
import { guard, talk, openSessions, openHome } from "../app";
import { playAudio } from "../voice";
import { canTalk } from "./listening";
import { openSheet } from "./menu";
import { openType } from "./type";

const SCROLL_STEP = 48;
const MAX_MESSAGES = 60;
const HISTORY_LIMIT = 30;

export type ChatHandle = {
  botId: string;
  screen: Screen;
  send(text: string): void;
  /** Switch to another session (null = a fresh chat). */
  resume(sid: string | null): void;
};

let active: ChatHandle | null = null;
/** Bots with a turn in flight (survives leaving and re-opening the chat). */
const busyBots = new Set<string>();

export function activeChat(): ChatHandle | null {
  return active;
}

type Live = {
  node: HTMLElement;
  md: MdView;
  cursor: HTMLElement;
  /** All answer text so far. */
  text: string;
  /** Text of the current markdown block; a tool chip or note starts a new block so order stays chronological. */
  seg: string;
  /** True once a chip or note has been placed after the current block. */
  broken: boolean;
  blocks: number;
  chips: Map<string, HTMLElement>;
};

export function createChat(botId: string, initialSessionId?: string | null): Screen {
  const el = h("div", { class: "scr chat" });
  const fallback: Bot["avatar"] = { kind: "shape", shape: "circle", color: "", url: "" };
  const avatarOf = (): Bot["avatar"] => {
    const b = botById(botId);
    return b ? b.avatar : fallback;
  };
  const av = renderAvatar(avatarOf(), { botId, size: 22, state: "idle", animate: true });
  const nameEl = h("div", { class: "chat-t" }, botById(botId)?.name || botId);
  const subEl = h("div", { class: "chat-s" });
  const head = h("div", { class: "chat-head" }, av, h("div", { class: "chat-htxt" }, nameEl, subEl));
  const tx = h("div", { class: "tx" });
  const foot = h("div", { class: "chat-foot" }, "hold to talk · click for menu");
  el.append(head, tx, foot);

  let sid: string | null = initialSessionId === undefined ? (state.prefs.sessions[botId] || null) : initialSessionId;
  let sessionTitle = "";
  let turnId: string | null = null;
  let live: Live | null = null;
  let lastAssistant = "";
  let loadSeq = 0;
  let userUp = false;
  const msgNodes: HTMLElement[] = [];
  const asks = new Map<string, { dismiss(): void }>();

  const isBusy = (): boolean => busyBots.has(botId);

  function setHeader(): void {
    const s = asks.size ? "ask" : isBusy() ? "busy" : "idle";
    setAvatarState(av, s, false, true);
    subEl.textContent = sessionTitle || (sid ? "" : "New chat");
  }

  function scrollDown(): void {
    if (!userUp) tx.scrollTop = tx.scrollHeight;
  }

  function addNode(n: HTMLElement): void {
    tx.appendChild(n);
    msgNodes.push(n);
    while (msgNodes.length > MAX_MESSAGES) {
      const old = msgNodes.shift() as HTMLElement;
      old.remove();
    }
  }

  function clearEmpty(): void {
    const e = tx.querySelector(".empty");
    if (e) e.remove();
  }

  function renderEmpty(): void {
    clear(tx);
    msgNodes.length = 0;
    live = null;
    tx.appendChild(h("div", { class: "empty" }, "Say something — hold the side button to talk"));
  }

  function chipEl(name: string, preview: string, ok: boolean): HTMLElement {
    const text = toolIcon(name) + " " + toolLabel(name) + (preview ? " " + preview : "");
    return h("div", { class: "chip" + (ok ? "" : " err") }, text);
  }

  function addUser(text: string): void {
    addNode(h("div", { class: "msg user" }, h("div", { class: "bubble" }, text)));
  }

  function newLive(): Live {
    const md = h("div", { class: "md" });
    const cursor = h("span", { class: "cursor" }, "▍");
    const node = h("div", { class: "msg asst" }, md, cursor);
    addNode(node);
    return { node, md: new MdView(md), cursor, text: "", seg: "", broken: false, blocks: 1, chips: new Map() };
  }

  function insertLive(n: HTMLElement): void {
    if (live) {
      live.node.insertBefore(n, live.cursor);
      live.broken = true;
    } else addNode(n);
  }

  function renderHistory(m: Message): void {
    if (m.role === "user") {
      addUser(m.text);
      return;
    }
    const md = h("div", { class: "md" });
    const node = h("div", { class: "msg asst" });
    if (state.config.chat.show_tools) for (const t of m.tools || []) node.appendChild(chipEl(t.name, t.preview, t.ok));
    node.appendChild(md);
    const view = new MdView(md);
    view.set(m.text, true);
    addNode(node);
    lastAssistant = m.text;
  }

  async function load(): Promise<void> {
    const seq = ++loadSeq;
    if (!state.api) return;
    try {
      const sres = await guard(state.api.sessions(botId, 20));
      if (seq !== loadSeq) return;
      const list: Session[] = sres.sessions || [];
      if (!sid && list.length) sid = list[0].id;
      const found = list.find((s) => s.id === sid);
      sessionTitle = found ? found.title : "";
      setHeader();
      if (!sid) {
        if (!isBusy()) renderEmpty();
        return;
      }
      const mres = await guard(state.api.messages(botId, sid, HISTORY_LIMIT));
      if (seq !== loadSeq) return;
      clear(tx);
      msgNodes.length = 0;
      lastAssistant = "";
      if (!mres.messages.length) renderEmpty();
      for (const m of mres.messages) renderHistory(m);
      rememberSession(botId, sid);
      userUp = false;
      scrollDown();
    } catch (e) {
      if (seq !== loadSeq) return;
      if (!tx.hasChildNodes()) renderEmpty();
      toast(e instanceof ApiError ? e.message : "Can't load this chat");
    }
  }

  function finish(e: Extract<TurnEvent, { t: "end" }>): void {
    busyBots.delete(botId);
    turnId = null;
    const L = live;
    live = null;
    if (L) {
      const text = e.text || L.text;
      if (text) lastAssistant = text;
      L.md.set(L.blocks > 1 ? L.seg : text, true);
      L.cursor.remove();
      if (e.status === "failed") L.node.appendChild(h("div", { class: "err" }, e.error || "Something went wrong"));
      else if (e.status === "cancelled") L.node.appendChild(h("div", { class: "note" }, "stopped"));
      if (!text && e.status !== "failed") L.node.appendChild(h("div", { class: "note" }, "(no reply)"));
    }
    for (const sh of asks.values()) sh.dismiss();
    asks.clear();
    setHeader();
    scrollDown();
  }

  function respond(reqId: string, reply: { choice?: string; answer?: string }): void {
    asks.delete(reqId);
    setHeader();
    const tid = turnId;
    if (!tid || !state.api) return;
    guard(state.api.respond(tid, reqId, reply)).catch(() => toast("Could not send your answer"));
  }

  function openAsk(e: Extract<TurnEvent, { t: "ask" }>): void {
    if (e.kind === "clarify" && (!e.choices || e.choices.length === 0)) {
      asks.set(e.request_id, { dismiss: () => undefined });
      setHeader();
      openType({
        title: e.title || "Answer",
        placeholder: e.detail ? e.detail.slice(0, 60) : "Your answer",
        onSend: (text) => respond(e.request_id, { answer: text }),
      });
      return;
    }
    const rows = (e.choices || []).map((c) => ({
      label: c.label,
      cls: c.id === "deny" ? "err" : "",
      onPick: () => respond(e.request_id, { choice: c.id }),
    }));
    asks.set(e.request_id, openSheet({ title: e.title || "Approve?", detail: e.detail, rows }));
    setHeader();
  }

  function closeAsk(reqId: string): void {
    const sh = asks.get(reqId);
    if (sh) sh.dismiss();
    asks.delete(reqId);
    setHeader();
  }

  function onEvent(e: TurnEvent): void {
    switch (e.t) {
      case "start":
        if (e.session_id) {
          sid = e.session_id;
          rememberSession(botId, sid);
        }
        break;
      case "delta":
        if (!live) live = newLive();
        clearEmpty();
        if (live.broken) {
          live.broken = false;
          if (live.seg) {
            // Text already shown above the chip: finish that block and continue in a new one below.
            live.md.set(live.seg, true);
            live.seg = "";
            live.blocks++;
          } else {
            live.md.el.remove();
          }
          const block = h("div", { class: "md" });
          live.node.insertBefore(block, live.cursor);
          live.md = new MdView(block);
        }
        live.text += e.text;
        live.seg += e.text;
        live.md.set(live.seg);
        scrollDown();
        break;
      case "note":
        if (state.config.chat.show_notes) {
          insertLive(h("div", { class: "note" }, e.text));
          scrollDown();
        }
        break;
      case "tool": {
        if (!live) live = newLive();
        if (!state.config.chat.show_tools) break;
        if (e.phase === "start") {
          const chip = chipEl(e.name, e.preview, true);
          chip.classList.add("pulse");
          live.chips.set(e.id, chip);
          insertLive(chip);
        } else {
          let chip = live.chips.get(e.id);
          if (!chip) {
            chip = chipEl(e.name, e.preview, e.ok);
            insertLive(chip);
          }
          chip.classList.remove("pulse");
          chip.classList.toggle("err", !e.ok);
          chip.textContent = toolIcon(e.name) + " " + toolLabel(e.name) + (e.preview ? " " + e.preview : "");
          live.chips.delete(e.id);
        }
        scrollDown();
        break;
      }
      case "ask":
        // A pending turn can continue after its chat is covered or left. Fleet owns the
        // device-wide ask surface in that case; don't unexpectedly push a sheet over Home.
        if (!el.classList.contains("on")) break;
        openAsk(e);
        break;
      case "ask_done":
        closeAsk(e.request_id);
        break;
      case "speech":
        void playAudio(e.url);
        break;
      case "end":
        finish(e);
        break;
    }
  }

  function send(text: string): void {
    const t = text.trim();
    if (!t) return;
    if (isBusy()) {
      toast("Still answering — stop or wait");
      return;
    }
    const b = botById(botId);
    if (b && !b.reachable) {
      toast(b.locked_reason || "This bot is locked");
      return;
    }
    if (!state.api) return;
    // This screen's turn takes over its transcript from any pending history load.
    ++loadSeq;
    clearEmpty();
    userUp = false;
    addUser(t);
    live = newLive();
    busyBots.add(botId);
    setHeader();
    scrollDown();
    const api = state.api;
    guard(api.startTurn(botId, t, sid, state.config.voice.auto_speak))
      .then((res) => {
        turnId = res.turn_id;
        if (res.session_id) {
          sid = res.session_id;
          rememberSession(botId, sid);
        }
        const f = followTurn(api, res.turn_id, onEvent, (ok) => setOnline(ok));
        return guard(f.done);
      })
      .catch((e) => {
        busyBots.delete(botId);
        turnId = null;
        if (live) {
          live.cursor.remove();
          live.node.appendChild(h("div", { class: "err" }, errorText(e)));
          live = null;
        }
        setHeader();
        scrollDown();
      });
  }

  function errorText(e: unknown): string {
    if (e instanceof ApiError) {
      if (e.code === "busy") return "Busy: another turn is running";
      if (e.code === "bot_locked") return e.message || "This bot is locked";
      if (e.code === "network") return "Can't reach the bridge";
      return e.message || "Send failed";
    }
    return "Send failed";
  }

  function stop(): void {
    const tid = turnId;
    if (tid && state.api) guard(state.api.stopTurn(tid)).catch(() => toast("Could not stop"));
  }

  function resume(next: string | null): void {
    if (isBusy()) {
      toast("Still answering — stop or wait");
      return;
    }
    if (next) {
      sid = next;
      rememberSession(botId, next);
    } else {
      sid = null;
      delete state.prefs.sessions[botId];
      savePrefs();
      sessionTitle = "";
      renderEmpty();
      setHeader();
      return;
    }
    void load();
  }

  function speakLast(): void {
    if (!lastAssistant || !state.api) {
      toast("Nothing to read");
      return;
    }
    guard(state.api.speak(lastAssistant.slice(0, 2000)))
      .then((r) => playAudio(r.url))
      .catch(() => toast("Read aloud unavailable"));
  }

  function openMenu(): void {
    const rows: { label: string; sub?: string; cls?: string; onPick: () => void }[] = [];
    if (canTalk()) rows.push({ label: "Talk", onPick: () => talk(botId, "tap") });
    rows.push({ label: "Type", onPick: () => openType({ title: "Message", quick: quickPrompts(), onSend: send }) });
    if (state.config.quick_prompts.length) {
      rows.push({ label: "Quick prompts", onPick: () => openQuick() });
    }
    if (isBusy()) rows.push({ label: "Stop", cls: "err", onPick: stop });
    rows.push({ label: "New chat", onPick: () => resume(null) });
    rows.push({ label: state.prefs.pinnedBot === botId ? "Unpin from Home" : "Pin to Home", onPick: () => {
      if (state.prefs.pinnedBot === botId) unpinBot(); else pinBot(botId);
      toast(state.prefs.pinnedBot === botId ? "Pinned to Home" : "Unpinned from Home");
    } });
    rows.push({ label: "Sessions", onPick: () => openSessions(botId) });
    if (state.voice.tts) rows.push({ label: "Read aloud", onPick: speakLast });
    rows.push({ label: "Back to bots", onPick: () => openHome() });
    openSheet({ title: nameEl.textContent || botId, rows });
  }

  function quickPrompts() {
    return state.config.quick_prompts
      .filter((q) => !q.bot || q.bot === botId)
      .map((q) => ({ label: q.label, text: q.text }));
  }

  function openQuick(): void {
    const qs = quickPrompts();
    if (!qs.length) {
      toast("No quick prompts");
      return;
    }
    openSheet({
      title: "Quick prompts",
      rows: qs.map((q) => ({ label: q.label, sub: q.text, onPick: () => send(q.text) })),
    });
  }

  const handle: ChatHandle = { botId, screen: null as unknown as Screen, send, resume };

  const unsub = subscribe(() => {
    const b = botById(botId);
    if (b) nameEl.textContent = b.name;
    setHeader();
    refreshChrome();
  });

  tx.addEventListener("scroll", () => {
    const atBottom = tx.scrollTop + tx.clientHeight >= tx.scrollHeight - 8;
    userUp = !atBottom;
  });

  const screen: Screen = {
    el,
    onShow() {
      state.currentBot = botId;
      refreshChrome();
    },
    onInput(ev: AppEvent): boolean {
      switch (ev) {
        case "up":
          tx.scrollTop -= SCROLL_STEP;
          return true;
        case "down":
          tx.scrollTop += SCROLL_STEP;
          return true;
        case "click":
          openMenu();
          return true;
        case "menu":
          openMenu();
          return true;
        case "holdStart": {
          if (state.config.buttons.long_press !== "talk") {
            openMenu();
            return true;
          }
          const b = botById(botId);
          if (b && !b.reachable) {
            toast(b.locked_reason || "This bot is locked");
            return true;
          }
          if (!canTalk()) {
            toast("Voice not available — use Type");
            return true;
          }
          talk(botId, "hold");
          return true;
        }
        default:
          return false;
      }
    },
    destroy() {
      unsub();
      if (active === handle) active = null;
    },
  };
  handle.screen = screen;
  active = handle;
  setHeader();
  if (!state.api) renderEmpty();
  void load();
  return screen;
}
