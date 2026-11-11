// Typed client for the bridge API (docs/SPEC.md section 3). Same-origin, fetch only.
import type {
  AdminRoutines,
  AdminState,
  Bootstrap,
  Bot,
  DeviceConfig,
  InboxItem,
  FleetData,
  GroupChat,
  GroupSummary,
  MemoryGraph,
  Message,
  PairInfo,
  Routine,
  Session,
  TurnEvent,
} from "./protocol";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

export type ApiOptions = { base?: string; token: string; timeoutMs?: number };

export class Api {
  base: string;
  token: string;
  timeoutMs: number;

  constructor(opts: ApiOptions) {
    const configuredBase = document.querySelector<HTMLMetaElement>('meta[name="r1-api-base"]')?.content || "";
    this.base = (opts.base ?? configuredBase).replace(/\/$/, "") + "/api/v1";
    this.token = opts.token;
    this.timeoutMs = opts.timeoutMs || 15000;
  }

  /** status 0 + code "network" means the request never got an HTTP answer. */
  async request<T>(method: string, path: string, body?: unknown, timeoutMs?: number): Promise<T> {
    const headers: Record<string, string> = {};
    if (this.token) headers["Authorization"] = "Bearer " + this.token;
    let payload: BodyInit | undefined;
    if (body instanceof Blob) {
      headers["Content-Type"] = body.type || "application/octet-stream";
      payload = body;
    } else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = ctl ? setTimeout(() => ctl.abort(), timeoutMs || this.timeoutMs) : 0;
    let res: Response;
    try {
      res = await fetch(this.base + path, { method, headers, body: payload, signal: ctl ? ctl.signal : undefined, cache: "no-store" });
    } catch (e) {
      throw new ApiError(0, "network", "Can't reach the bridge");
    } finally {
      if (timer) clearTimeout(timer);
    }
    let data: any = null;
    try {
      data = await res.json();
    } catch (e) {
      data = null;
    }
    if (!res.ok) {
      const err = data && data.error ? data.error : {};
      throw new ApiError(res.status, err.code || "http_" + res.status, err.message || "Request failed (" + res.status + ")");
    }
    return data as T;
  }

  // ---- device ----
  health() {
    return this.request<{ ok: boolean; name: string; version: string; mock: boolean }>("GET", "/health");
  }
  pair(code: string, name: string) {
    return this.request<{ token: string; device: { id: string; name: string } }>("POST", "/pair", { code, name });
  }
  bootstrap() {
    return this.request<Bootstrap>("GET", "/bootstrap");
  }
  bots() {
    return this.request<{ bots: Bot[]; config_rev: number }>("GET", "/bots");
  }
  sessions(bot: string, limit = 20) {
    return this.request<{ sessions: Session[] }>("GET", `/bots/${encodeURIComponent(bot)}/sessions?limit=${limit}`);
  }
  memoryGraph(bot: string) {
    return this.request<MemoryGraph>("GET", `/bots/${encodeURIComponent(bot)}/memory-graph`);
  }
  messages(bot: string, sid: string, limit = 30) {
    return this.request<{ messages: Message[] }>(
      "GET",
      `/bots/${encodeURIComponent(bot)}/sessions/${encodeURIComponent(sid)}/messages?limit=${limit}`,
    );
  }
  startTurn(bot: string, text: string, sessionId: string | null, speak = false) {
    return this.request<{ turn_id: string; session_id: string }>("POST", `/bots/${encodeURIComponent(bot)}/turns`, {
      text,
      session_id: sessionId,
      speak,
    });
  }
  turnEvents(turnId: string, after: number, wait = 20) {
    return this.request<{ events: TurnEvent[]; next: number; done: boolean }>(
      "GET",
      `/turns/${encodeURIComponent(turnId)}/events?after=${after}&wait=${wait}`,
      undefined,
      (wait + 10) * 1000,
    );
  }
  stopTurn(turnId: string) {
    return this.request<{ ok: boolean }>("POST", `/turns/${encodeURIComponent(turnId)}/stop`, {});
  }
  respond(turnId: string, requestId: string, reply: { choice?: string; answer?: string }) {
    return this.request<{ ok: boolean }>("POST", `/turns/${encodeURIComponent(turnId)}/respond`, {
      request_id: requestId,
      ...reply,
    });
  }
  transcribe(audio: Blob) {
    return this.request<{ text: string }>("POST", "/voice/transcribe", audio, 60000);
  }
  speak(text: string) {
    return this.request<{ url: string; mime: string }>("POST", "/voice/speak", { text }, 60000);
  }
  inbox(after = 0, wait = 0) {
    return this.request<{ items: InboxItem[]; next: number }>("GET", `/inbox?after=${after}&wait=${wait}`, undefined, (wait + 10) * 1000);
  }
  fleet() {
    return this.request<FleetData>("GET", "/fleet");
  }
  routines() {
    return this.request<{ routines: Routine[] }>("GET", "/routines");
  }
  groups() {
    return this.request<{ groups: GroupSummary[] }>("GET", "/groups");
  }
  createGroup(name: string, bots: string[]) {
    return this.request<GroupChat>("POST", "/groups", { name, bots });
  }
  group(id: string) {
    return this.request<GroupChat>("GET", `/groups/${encodeURIComponent(id)}`);
  }
  sendGroup(id: string, text: string) {
    return this.request<GroupChat>("POST", `/groups/${encodeURIComponent(id)}/messages`, { text });
  }

  // ---- admin (Studio) ----
  adminState() {
    return this.request<AdminState>("GET", "/admin/state");
  }
  adminSetupStatus() {
    return this.request<{ gateways: FleetData["gateways"] }>("GET", "/admin/setup-status");
  }
  adminSaveConfig(config: DeviceConfig) {
    return this.request<{ config: DeviceConfig; config_rev: number }>("PUT", "/admin/config", config);
  }
  adminPair() {
    return this.request<PairInfo>("POST", "/admin/pair", {});
  }
  adminRemoveDevice(id: string) {
    return this.request<{ ok: boolean }>("DELETE", `/admin/devices/${encodeURIComponent(id)}`);
  }
  adminRoutines(deviceId: string) {
    return this.request<AdminRoutines>("GET", `/admin/devices/${encodeURIComponent(deviceId)}/routines`);
  }
  adminSaveRoutines(deviceId: string, routines: Routine[]) {
    return this.request<AdminRoutines>("PUT", `/admin/devices/${encodeURIComponent(deviceId)}/routines`, { routines });
  }
  adminPreviewToken() {
    return this.request<{ token: string }>("POST", "/admin/preview-token", {});
  }
  adminSetPublicUrl(url: string) {
    return this.request<{ public_url: string }>("PUT", "/admin/public-url", { url });
  }
  adminTestInbox(item: { title: string; body: string; bot: string; level: "info" | "success" | "warn" }) {
    return this.request<{ item: InboxItem }>("POST", "/admin/inbox", item);
  }
}

/**
 * Follow a turn by chained long-polls. Calls onEvent for each event in order; resolves when the
 * turn is done. Retries network errors with backoff (0.5s → 4s). Call the returned cancel() to stop following.
 */
export function followTurn(api: Api, turnId: string, onEvent: (e: TurnEvent) => void, onNetwork?: (ok: boolean) => void) {
  let cancelled = false;
  const done = (async () => {
    let after = 0;
    let delay = 500;
    while (!cancelled) {
      try {
        const res = await api.turnEvents(turnId, after, 20);
        if (cancelled) return;
        if (onNetwork) onNetwork(true);
        delay = 500;
        for (const e of res.events) onEvent(e);
        after = res.next;
        if (res.done) return;
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) {
          onEvent({ seq: after + 1, t: "end", status: "failed", text: "", error: "This turn is no longer available.", usage: { input: 0, output: 0 } });
          return;
        }
        if (e instanceof ApiError && e.status === 401) throw e;
        if (onNetwork) onNetwork(false);
        await new Promise((r) => setTimeout(r, delay));
        delay = Math.min(delay * 2, 4000);
      }
    }
  })();
  return { done, cancel: () => { cancelled = true; } };
}
