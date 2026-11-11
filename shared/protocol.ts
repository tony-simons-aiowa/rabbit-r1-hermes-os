// Wire types for the bridge API. Mirrors docs/SPEC.md section 3.2 exactly.

export type Avatar = {
  kind: "shape" | "image";
  shape: string;
  color: string;
  url: string;
};

export type Pet = {
  url: string;
  cols: number;
  rows: number;
  cw: number;
  ch: number;
  frames: number;
  loop_ms: number;
};

export type BotState = "idle" | "busy" | "ask";

export type GroupSummary = {
  id: string;
  name: string;
  members: Array<{ id: string; name: string; gateway: string }>;
};

export type GroupChat = GroupSummary & {
  rounds: Array<{
    text: string;
    replies: Array<{ bot: string; status: "pending" | "completed" | "failed" | "cancelled"; text: string; error: string }>;
  }>;
};

export type Bot = {
  id: string;
  /** Gateway owning this bot. Older bridges omit these fields and imply `local`. */
  gateway_id?: string;
  gateway_name?: string;
  name: string;
  title: string;
  description: string;
  model: string;
  is_default: boolean;
  reachable: boolean;
  locked_reason: string;
  avatar: Avatar;
  pet: Pet | null;
  state: BotState;
  active: boolean;
  last: { text: string; ts: number } | null;
};

export type Session = {
  id: string;
  title: string;
  preview: string;
  last_active: number;
  message_count: number;
};

export type ToolSummary = { name: string; ok: boolean; preview: string };

export type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  ts: number;
  tools: ToolSummary[];
};

export type AskChoice = { id: string; label: string };

export interface MemoryGraph {
  nodes: { id: string; label: string; kind: "skill" | "memory" }[];
  edges: { source: string; target: string }[];
  total: number;
}

export type FleetAsk = {
  t: "ask";
  request_id: string;
  kind: "approval" | "clarify";
  title: string;
  detail: string;
  choices: AskChoice[];
};

export type FleetFinished =
  | { kind: "chat"; bot: string; session_id: string; turn_id: string; status: "completed" | "failed" | "cancelled"; text: string; ts: number }
  | { kind: "group"; room_id: string; name: string; status: "completed" | "failed" | "cancelled"; text: string; ts: number };

export type FleetData = {
  needs_you: Array<{ turn_id: string; bot_id: string; ask: FleetAsk }>;
  working: Array<{ turn_id: string; bot_id: string }>;
  gateways: Array<{ id: string; name: string; status: "online" | "offline" | "unknown" }>;
  /** Recent completed direct and group replies; absent on older bridges. */
  finished?: FleetFinished[];
};

export type TurnEvent = { seq: number } & (
  | { t: "start"; session_id: string }
  | { t: "delta"; text: string }
  | { t: "note"; text: string }
  | { t: "tool"; id: string; name: string; phase: "start" | "end"; preview: string; ok: boolean; ms: number }
  | { t: "ask"; request_id: string; kind: "approval" | "clarify"; title: string; detail: string; choices: AskChoice[] }
  | { t: "ask_done"; request_id: string }
  | { t: "speech"; url: string; mime: string }
  | { t: "end"; status: "completed" | "failed" | "cancelled"; text: string; error: string; usage: { input: number; output: number } }
);

export type InboxItem = {
  id: number;
  ts: number;
  bot: string;
  title: string;
  body: string;
  level: "info" | "success" | "warn";
};

export type QuickPrompt = { id: string; label: string; text: string; bot: string };

export type DeviceConfig = {
  theme: { preset: string; overrides: Record<string, string> };
  font_scale: number;
  home: { greeting: string; clock: boolean };
  roster: { order: string[]; hidden: string[]; show_pets: boolean; active_strip: boolean };
  avatar_overrides: Record<string, { shape?: string; color?: string }>;
  quick_prompts: QuickPrompt[];
  voice: { input: "auto" | "mediarecorder" | "native" | "off"; auto_speak: boolean };
  buttons: { side_click: "select"; double_click: "back" | "menu"; long_press: "talk" | "menu" };
  chat: { show_tools: boolean; show_notes: boolean };
};

export type VoiceCaps = { stt: boolean; tts: boolean };

export type Bootstrap = {
  server: { version: string; mock: boolean };
  device: { id: string; name: string };
  config: DeviceConfig;
  config_rev: number;
  bots: Bot[];
  voice: VoiceCaps;
};

export type DeviceInfo = { id: string; name: string; created: number; last_seen: number };

export type AdminState = {
  config: DeviceConfig;
  config_rev: number;
  devices: DeviceInfo[];
  bots: Bot[];
  public_url: string;
  local_url: string;
  voice: VoiceCaps;
  mock: boolean;
  version: string;
};

export type PairInfo = {
  code: string;
  expires: number;
  url: string;
  qr: { title: string; url: string; description: string; iconUrl: string; themeColor: string };
};

export type ApiErrorBody = { error: { code: string; message: string } };

export const DEFAULT_CONFIG: DeviceConfig = {
  theme: { preset: "nous", overrides: {} },
  font_scale: 1,
  home: { greeting: "", clock: true },
  roster: { order: [], hidden: [], show_pets: true, active_strip: true },
  avatar_overrides: {},
  quick_prompts: [
    { id: "q1", label: "Status", text: "Give me a 2-sentence status update on what you're working on.", bot: "" },
    { id: "q2", label: "What's next?", text: "What should I look at next? Keep it short.", bot: "" },
  ],
  voice: { input: "auto", auto_speak: false },
  buttons: { side_click: "select", double_click: "back", long_press: "talk" },
  chat: { show_tools: true, show_notes: true },
};
