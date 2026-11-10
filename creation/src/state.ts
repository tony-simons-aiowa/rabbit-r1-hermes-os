// App state with a minimal subscribe/notify.
import { Api } from "../../shared/api";
import type { Bootstrap, Bot, DeviceConfig, InboxItem } from "../../shared/protocol";
import { DEFAULT_CONFIG } from "../../shared/protocol";
import { getJson, setJson, storage } from "./platform";

export const TOKEN_KEY = "r1.token";
const PREFS_KEY = "r1.prefs";

type Prefs = { lastBot: string; sessions: Record<string, string> };

export const state = {
  api: null as Api | null,
  token: "",
  deviceName: "",
  bootstrap: null as Bootstrap | null,
  config: DEFAULT_CONFIG as DeviceConfig,
  configRev: 0,
  bots: [] as Bot[],
  prefs: { lastBot: "", sessions: {} } as Prefs,
  inbox: [] as InboxItem[],
  inboxNext: 0,
  online: true,
  /** Bot shown in the status bar: the open chat's bot, or the highlighted roster row. */
  currentBot: "",
  voice: { stt: false, tts: false },
  serverMock: false,
};

const subs = new Set<() => void>();

export function subscribe(fn: () => void): () => void {
  subs.add(fn);
  return () => subs.delete(fn);
}

export function notify(): void {
  subs.forEach((f) => {
    try {
      f();
    } catch (e) {
      console.error(e);
    }
  });
}

export async function loadToken(): Promise<string> {
  return (await storage.getSecure(TOKEN_KEY)) || "";
}

export async function saveToken(token: string): Promise<void> {
  await storage.setSecure(TOKEN_KEY, token);
}

export async function clearToken(): Promise<void> {
  await storage.remove(TOKEN_KEY);
  await storage.remove("r1.prefs");
}

export async function loadPrefs(): Promise<void> {
  const p = await getJson<Prefs>(PREFS_KEY, { lastBot: "", sessions: {} });
  state.prefs = { lastBot: p.lastBot || "", sessions: p.sessions || {} };
}

export function savePrefs(): void {
  void setJson(PREFS_KEY, state.prefs);
}

export function rememberSession(bot: string, sid: string): void {
  if (!sid) return;
  state.prefs.sessions[bot] = sid;
  savePrefs();
}

export function rememberBot(bot: string): void {
  state.prefs.lastBot = bot;
  savePrefs();
}

export function botById(id: string): Bot | undefined {
  return state.bots.find((b) => b.id === id);
}

/** Roster order: config.roster.order first, then the default bot, then alphabetical; hidden removed. */
export function orderedBots(): Bot[] {
  const hidden = new Set(state.config.roster.hidden || []);
  const order = state.config.roster.order || [];
  const rank = (b: Bot): number => {
    const i = order.indexOf(b.id);
    return i >= 0 ? i : order.length;
  };
  return state.bots
    .filter((b) => !hidden.has(b.id))
    .slice()
    .sort((a, b) => {
      const ra = rank(a);
      const rb = rank(b);
      if (ra !== rb) return ra - rb;
      if (a.is_default !== b.is_default) return a.is_default ? -1 : 1;
      // Bots you can't talk to yet sink below the ones you can.
      if (a.reachable !== b.reachable) return a.reachable ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
}

export function applyBootstrap(b: Bootstrap): void {
  state.bootstrap = b;
  state.config = b.config;
  state.configRev = b.config_rev;
  state.bots = b.bots;
  state.voice = b.voice;
  state.serverMock = !!b.server.mock;
  state.deviceName = b.device.name;
  notify();
}
