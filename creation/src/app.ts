// Navigation hub. Screens call these entry points instead of importing each other's internals.
import { ApiError } from "../../shared/api";
import { resolveTheme, applyTheme } from "../../shared/themes";
import { state, clearToken, rememberBot, applyBootstrap } from "./state";
import { push, reset, current, type Screen } from "./nav";
import { toast } from "./chrome";
import { createHome } from "./screens/home";
import { createPair } from "./screens/pair";
import { createChat, activeChat } from "./screens/chat";
import { openListening } from "./screens/listening";
import { createSessions } from "./screens/sessions";
import { createInbox } from "./screens/inbox";
import { createFleet } from "./screens/fleet";
import { createSettings } from "./screens/settings";
import { createDiagnostics } from "./screens/diagnostics";

/** Whether the Home roster is on screen (drives roster polling). */
export const flags = { homeVisible: false };

export function openHome(): void {
  reset(createHome());
}

export function openPair(message?: string): void {
  reset(createPair(message));
}

export function openChat(bot: string): void {
  rememberBot(bot);
  state.currentBot = bot;
  push(createChat(bot));
}

/** Send text to a bot's chat. Opens that chat first when it is not on top. */
export function sendTo(bot: string, text: string): void {
  let c = activeChat();
  if (!c || c.botId !== bot || current() !== c.screen) {
    openChat(bot);
    c = activeChat();
  }
  if (c) c.send(text);
}

export function openSessions(bot: string): void {
  push(createSessions(bot));
}

export function openInbox(): void {
  push(createInbox());
}

export function openFleet(): void {
  push(createFleet());
}

export function openSettings(): void {
  push(createSettings());
}

export function openDiagnostics(): void {
  push(createDiagnostics());
}

export function talk(bot: string, mode: "hold" | "tap"): void {
  openListening(bot, mode);
}

export async function unpair(): Promise<void> {
  await clearToken();
  state.api = null;
  state.token = "";
  state.bootstrap = null;
  state.bots = [];
  state.prefs = { lastBot: "", sessions: {}, gatewayId: "" };
  openPair("Unpaired. Scan the QR code in Studio to pair again.");
}

/** Wrap an API promise: a 401 means the device token is gone, so return to pairing. */
export async function guard<T>(p: Promise<T>): Promise<T> {
  try {
    return await p;
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      await unpair();
      toast("Pairing expired");
    }
    throw e;
  }
}

/** Apply the theme (optionally a local preset preview) and font scale to the app root. */
export function applyAppTheme(previewPreset?: string): void {
  const app = document.getElementById("app");
  if (!app) return;
  const theme = resolveTheme({
    preset: previewPreset || state.config.theme.preset,
    overrides: state.config.theme.overrides,
  });
  applyTheme(app, theme);
  app.style.setProperty("--fs", String(state.config.font_scale || 1));
}

/** Fetch /bootstrap and apply config, bots and theme. Throws on failure (401 unpairs). */
export async function bootstrapDevice(): Promise<void> {
  if (!state.api) throw new Error("not paired");
  const b = await guard(state.api.bootstrap());
  applyBootstrap(b);
  applyAppTheme();
}

/** Current top screen, typed for callers that need its element. */
export function top(): Screen | undefined {
  return current();
}
