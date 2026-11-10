// R1-specific platform glue. Everything here is feature-detected so the app also runs in a desktop browser.

/** Window of time after a side click in which a second click counts as a double click. */
export const DOUBLE_WINDOW_MS = 320;
/** Minimum spacing between wheel steps. */
export const WHEEL_MIN_MS = 40;

export type AppEvent = "up" | "down" | "click" | "back" | "menu" | "holdStart" | "holdEnd";

type Listener = (ev: AppEvent) => void;
const listeners: Listener[] = [];

/** Subscribe to unified input. Returns an unsubscribe function. */
export function onInput(fn: Listener): () => void {
  listeners.push(fn);
  return () => {
    const i = listeners.indexOf(fn);
    if (i >= 0) listeners.splice(i, 1);
  };
}

function emit(ev: AppEvent): void {
  for (const l of listeners.slice()) {
    try {
      l(ev);
    } catch (e) {
      console.error(e);
    }
  }
}

let doubleAction: "back" | "menu" = "back";
/** Configured meaning of a double click (from config.buttons.double_click). */
export function setDoubleAction(a: "back" | "menu"): void {
  doubleAction = a;
}

let lastWheel = 0;
let pendingClick = false;
let lastSideClick = 0;
let clickTimer = 0;

function wheel(ev: "up" | "down"): void {
  const now = Date.now();
  if (now - lastWheel < WHEEL_MIN_MS) return;
  lastWheel = now;
  emit(ev);
}

function sideClick(): void {
  const now = Date.now();
  if (pendingClick && now - lastSideClick <= DOUBLE_WINDOW_MS) {
    clearTimeout(clickTimer);
    pendingClick = false;
    lastSideClick = 0;
    emit(doubleAction);
    return;
  }
  pendingClick = true;
  lastSideClick = now;
  clearTimeout(clickTimer);
  clickTimer = setTimeout(() => {
    pendingClick = false;
    emit("click");
  }, DOUBLE_WINDOW_MS) as unknown as number;
}

function isTextTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || !!el.isContentEditable;
}

let spaceHeld = false;

/** Wire up R1 window events and desktop keyboard. Call once at boot. */
export function initInput(): void {
  window.addEventListener("scrollUp" as keyof WindowEventMap, () => wheel("up"));
  window.addEventListener("scrollDown" as keyof WindowEventMap, () => wheel("down"));
  window.addEventListener("sideClick" as keyof WindowEventMap, () => sideClick());
  window.addEventListener("longPressStart" as keyof WindowEventMap, () => emit("holdStart"));
  window.addEventListener("longPressEnd" as keyof WindowEventMap, () => emit("holdEnd"));

  window.addEventListener("keydown", (e) => {
    if (isTextTarget(e.target)) return;
    switch (e.key) {
      case "ArrowUp":
        e.preventDefault();
        wheel("up");
        break;
      case "ArrowDown":
        e.preventDefault();
        wheel("down");
        break;
      case "Enter":
        emit("click");
        break;
      case "Escape":
      case "Backspace":
        e.preventDefault();
        emit("back");
        break;
      case " ":
        e.preventDefault();
        if (!e.repeat && !spaceHeld) {
          spaceHeld = true;
          emit("holdStart");
        }
        break;
    }
  });
  window.addEventListener("keyup", (e) => {
    if (e.key === " " && spaceHeld) {
      spaceHeld = false;
      emit("holdEnd");
    }
  });
}

/** Ask the R1 to close the web app. Returns false on desktop. */
export function closeApp(): boolean {
  const cw = (window as unknown as { closeWebView?: { postMessage?: (s: string) => void } }).closeWebView;
  if (cw && typeof cw.postMessage === "function") {
    cw.postMessage("");
    return true;
  }
  return false;
}

// ---------- storage ----------

type Backend = {
  get(k: string): Promise<string | null>;
  set(k: string, v: string): Promise<void>;
  del(k: string): Promise<void>;
};

const memory = new Map<string, string>();

function toB64(s: string): string {
  return btoa(unescape(encodeURIComponent(s)));
}
function fromB64(b: string): string {
  return decodeURIComponent(escape(atob(b)));
}

function nativeBackend(kind: "secure" | "plain"): Backend | null {
  const cs = (window as unknown as { creationStorage?: Record<string, any> }).creationStorage;
  const s = cs ? cs[kind] : null;
  if (!s || typeof s.getItem !== "function") return null;
  return {
    get: async (k) => {
      const v = await s.getItem(k);
      return v == null ? null : fromB64(String(v));
    },
    set: async (k, v) => {
      await s.setItem(k, toB64(v));
    },
    del: async (k) => {
      await s.removeItem(k);
    },
  };
}

function lsBackend(): Backend | null {
  try {
    const ls = window.localStorage;
    if (!ls) return null;
    const probe = "__probe__";
    ls.setItem(probe, "1");
    ls.removeItem(probe);
    return {
      get: async (k) => ls.getItem(k),
      set: async (k, v) => ls.setItem(k, v),
      del: async (k) => ls.removeItem(k),
    };
  } catch (e) {
    return null;
  }
}

const memBackend: Backend = {
  get: async (k) => (memory.has(k) ? (memory.get(k) as string) : null),
  set: async (k, v) => {
    memory.set(k, v);
  },
  del: async (k) => {
    memory.delete(k);
  },
};

function pick(kind: "secure" | "plain"): Backend {
  return nativeBackend(kind) || lsBackend() || memBackend;
}

let secureBackend: Backend | null = null;
let plainBackend: Backend | null = null;

function sec(): Backend {
  if (!secureBackend) secureBackend = pick("secure");
  return secureBackend;
}
function plain(): Backend {
  if (!plainBackend) plainBackend = pick("plain");
  return plainBackend;
}

async function safe<T>(fn: () => Promise<T>, fallback: T): Promise<T> {
  try {
    return await fn();
  } catch (e) {
    return fallback;
  }
}

/** Async key/value storage: native creationStorage, then localStorage, then memory. Values are strings. */
export const storage = {
  getSecure: (k: string) => safe(() => sec().get(k), memory.get(k) ?? null),
  setSecure: (k: string, v: string) => safe(() => sec().set(k, v), undefined),
  getPlain: (k: string) => safe(() => plain().get(k), memory.get(k) ?? null),
  setPlain: (k: string, v: string) => safe(() => plain().set(k, v), undefined),
  async remove(k: string): Promise<void> {
    await safe(() => sec().del(k), undefined);
    await safe(() => plain().del(k), undefined);
    memory.delete(k);
  },
};

/** Read a JSON value from plain storage. */
export async function getJson<T>(k: string, fallback: T): Promise<T> {
  const raw = await storage.getPlain(k);
  if (raw == null) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch (e) {
    return fallback;
  }
}
export async function setJson(k: string, v: unknown): Promise<void> {
  await storage.setPlain(k, JSON.stringify(v));
}

export function hasNativeVoiceBridge(): boolean {
  const h = (window as unknown as { CreationVoiceHandler?: { postMessage?: unknown } }).CreationVoiceHandler;
  return !!(h && typeof h.postMessage === "function");
}
