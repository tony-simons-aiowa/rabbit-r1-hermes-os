// Boot: token → bootstrap → Home (or Pair). Pollers, config reload and 401 handling live here.
import { Api, ApiError } from "../../shared/api";
import { initInput, onInput, setDoubleAction } from "./platform";
import { initNav, dispatch } from "./nav";
import { initChrome, setOnline, toast } from "./chrome";
import { state, subscribe, notify, loadToken, loadPrefs } from "./state";
import { applyAppTheme, bootstrapDevice, openHome, openPair, guard, flags } from "./app";
import { pairWith } from "./screens/pair";

const ROSTER_MS = 8000;
const INBOX_MS = 30000;
const MAX_INBOX = 40;

let rosterBusy = false;
let inboxBusy = false;

async function pollRoster(): Promise<void> {
  if (rosterBusy || !state.api || document.hidden || !flags.homeVisible) return;
  rosterBusy = true;
  try {
    const r = await guard(state.api.bots());
    setOnline(true);
    if (r.config_rev !== state.configRev) {
      await bootstrapDevice();
    } else {
      state.bots = r.bots;
      notify();
    }
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 401)) setOnline(false);
  } finally {
    rosterBusy = false;
  }
}

async function pollInbox(): Promise<void> {
  if (inboxBusy || !state.api || document.hidden) return;
  inboxBusy = true;
  try {
    const r = await guard(state.api.inbox(state.inboxNext, 0));
    state.inboxNext = r.next || state.inboxNext;
    if (r.items.length) {
      const fresh = r.items.slice().sort((a, b) => b.ts - a.ts);
      state.inbox = fresh.concat(state.inbox).slice(0, MAX_INBOX);
      notify();
      if (fresh.length) toast(fresh[0].title || "New notification");
    }
  } catch (e) {
    /* retried on the next tick */
  } finally {
    inboxBusy = false;
  }
}

function startPollers(): void {
  window.setInterval(() => void pollRoster(), ROSTER_MS);
  window.setInterval(() => void pollInbox(), INBOX_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      void pollRoster();
      void pollInbox();
    }
  });
  // Studio saves → the emulator iframe is told to re-bootstrap.
  window.addEventListener("message", (e: MessageEvent) => {
    if (e.origin !== location.origin) return;
    const d = e.data as { type?: string } | null;
    if (d && d.type === "r1:reload-config" && state.api) {
      void bootstrapDevice().catch(() => undefined);
    }
  });
}

async function boot(): Promise<void> {
  const app = document.getElementById("app");
  if (!app) return;
  const screens = initChrome(app);
  initNav(screens);
  initInput();
  onInput(dispatch);
  subscribe(() => setDoubleAction(state.config.buttons.double_click));
  applyAppTheme();

  await loadPrefs();

  const url = new URL(location.href);
  const emu = url.searchParams.get("emu") === "1";
  const pairCode = url.searchParams.get("pair");
  let token = "";
  if (emu) {
    const m = /token=([^&]+)/.exec(location.hash);
    token = m ? decodeURIComponent(m[1]) : "";
  } else {
    token = await loadToken();
  }
  if (pairCode) {
    url.searchParams.delete("pair");
    history.replaceState(null, "", url.pathname + url.search + url.hash);
  }

  if (!token) {
    if (pairCode) {
      openPair("Pairing…");
      const err = await pairWith(pairCode);
      if (err) openPair(err);
    } else {
      openPair();
    }
  } else {
    state.token = token;
    state.api = new Api({ token });
    try {
      await bootstrapDevice();
      openHome();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return; // guard() already returned to Pair
      setOnline(false);
      openHome();
    }
  }

  startPollers();
  void pollInbox();
}

void boot();
