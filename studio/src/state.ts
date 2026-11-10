// App state: the server snapshot (admin), the editable draft config, and debounced saving.
import { Api, ApiError } from "../../shared/api";
import { DEFAULT_CONFIG, type AdminState, type DeviceConfig } from "../../shared/protocol";

export type Status = { kind: string; text: string };

const SAVE_DEBOUNCE_MS = 600;
const MAX_RETRY_MS = 30000;

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

let api: Api | null = null;
let admin: AdminState | null = null;
let draft: DeviceConfig = clone(DEFAULT_CONFIG);
let rev = 0;
let version = 0;
let dirty = false;
let inFlight = false;
let saveTimer = 0;
let retryTimer = 0;
let retries = 0;
let save: Status = { kind: "idle", text: "" };
let conn: Status = { kind: "connecting", text: "Connecting" };

const changeListeners: Array<() => void> = [];
const statusListeners: Array<() => void> = [];
const savedListeners: Array<() => void> = [];

export function initState(a: Api, s: AdminState): void {
  api = a;
  admin = s;
  draft = clone(s.config);
  rev = s.config_rev;
  conn = s.mock ? { kind: "mock", text: "Mock mode" } : { kind: "ok", text: "Connected" };
}

export function getApi(): Api {
  if (!api) throw new Error("Studio is not ready yet.");
  return api;
}

export function getAdmin(): AdminState {
  if (!admin) throw new Error("Studio is not ready yet.");
  return admin;
}

/** The live draft. Read it freely; change it only through edit(). */
export function getDraft(): DeviceConfig {
  return draft;
}

export function saveStatus(): Status {
  return save;
}

export function connStatus(): Status {
  return conn;
}

export function onChange(fn: () => void): void {
  changeListeners.push(fn);
}

export function onStatus(fn: () => void): void {
  statusListeners.push(fn);
}

export function onSaved(fn: () => void): void {
  savedListeners.push(fn);
}

function emitChange(): void {
  for (const fn of changeListeners) fn();
}

function emitStatus(): void {
  for (const fn of statusListeners) fn();
}

function setSave(kind: string, text: string): void {
  save = { kind, text };
  emitStatus();
}

/** Apply a local edit to the draft, then schedule a debounced save and re-sync the UI. */
export function edit(mutate: (d: DeviceConfig) => void): void {
  mutate(draft);
  version++;
  scheduleSave();
  emitChange();
}

/** Mutate the admin snapshot (devices, public URL) after a successful admin call. */
export function patchAdmin(mutate: (a: AdminState) => void): void {
  if (admin) mutate(admin);
  emitChange();
}

function scheduleSave(): void {
  dirty = true;
  retries = 0;
  window.clearTimeout(retryTimer);
  retryTimer = 0;
  window.clearTimeout(saveTimer);
  setSave("pending", "Saving…");
  saveTimer = window.setTimeout(() => {
    saveTimer = 0;
    void flush();
  }, SAVE_DEBOUNCE_MS);
}

async function flush(): Promise<void> {
  if (!api || inFlight || !dirty) return;
  inFlight = true;
  const sent = version;
  const body = clone(draft);
  setSave("saving", "Saving…");
  try {
    const res = await api.adminSaveConfig(body);
    rev = res.config_rev;
    if (admin) admin.config_rev = rev;
    retries = 0;
    if (version === sent) {
      // Nothing changed while the request was in flight: adopt the server's normalized copy.
      draft = clone(res.config);
      dirty = false;
      setSave("saved", "Saved ✓");
      emitChange();
    } else {
      // The user kept editing; the newer edits stay in the draft and save next.
      scheduleSave();
      emitChange();
    }
    for (const fn of savedListeners) fn();
  } catch (err) {
    if (err instanceof ApiError && err.status >= 400 && err.status < 500 && err.status !== 429) {
      // Validation or auth error: retrying the same body will not help. Wait for the next edit.
      setSave("error", "Couldn't save: " + err.message);
    } else {
      retries++;
      setSave("error", "Couldn't save — retrying");
      const delay = Math.min(MAX_RETRY_MS, 1000 * 2 ** Math.min(retries, 5));
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(() => {
        retryTimer = 0;
        void flush();
      }, delay);
    }
  } finally {
    inFlight = false;
  }
}

/** Refresh the device list and bot states every few seconds without clobbering unsaved edits. */
export async function refreshAdmin(): Promise<void> {
  if (!api) return;
  try {
    const fresh = await api.adminState();
    admin = fresh;
    conn = fresh.mock ? { kind: "mock", text: "Mock mode" } : { kind: "ok", text: "Connected" };
    if (!dirty && !inFlight && fresh.config_rev !== rev) {
      // Changed elsewhere (another Studio tab or the CLI): adopt it, since nothing here is pending.
      rev = fresh.config_rev;
      draft = clone(fresh.config);
    }
    emitChange();
    emitStatus();
  } catch (e) {
    conn =
      e instanceof ApiError && e.status === 401
        ? { kind: "error", text: "Token rejected" }
        : { kind: "error", text: "Offline" };
    emitStatus();
  }
}
