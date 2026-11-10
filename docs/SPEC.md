# Hermes R1 OS — specification (v0.1)

This is the contract every component is built against. If code and this file
disagree, this file wins; change it deliberately.

## 1. What it is

An open-source Hermes Agent experience for the Rabbit R1:

| Piece | Path | Tech | Role |
|---|---|---|---|
| **Bridge + Hermes plugin** | `plugin/hermes-r1/` | Python 3.11+, aiohttp | Hermes plugin (`hermes r1 …` CLI, `r1_notify` tool). Serves the web apps and JSON API, and routes requests to the primary Hermes API server and any configured additional gateways. The bridge sends each API key only to its configured upstream. |
| **Creation** | `creation/` | vanilla TypeScript, esbuild, no framework | The app that runs on the R1 (240×282 WebView), installed by QR code. |
| **Studio** | `studio/` | vanilla TypeScript, esbuild, no framework | Desktop web app: pair devices, customize everything, live emulator. |
| **Shared** | `shared/` | TypeScript | Protocol types, API client, avatar renderer, themes, tool icons. |

```
R1 creation ──HTTPS (tunnel)──▶ bridge (127.0.0.1:8717) ──HTTP+SSE──▶ Hermes API server (127.0.0.1:8642)
Studio (browser on the Mac) ──▶ bridge /studio/ + /api/v1/admin/*
```

Everything the device needs is same-origin with the bridge, so there is no CORS
and no mixed content. The device never uses WebSocket or SSE (unverified on R1):
streaming is **long-polling** over plain `fetch`.

Build output goes to `plugin/hermes-r1/static/app/` (creation) and
`plugin/hermes-r1/static/studio/` (studio). The bridge serves `static/app/` at
`/` and `static/studio/` at `/studio/`.

## 2. R1 platform facts (verified from rabbit-hmi-oss/creations-sdk)

- Viewport: exactly 240×282 CSS px, portrait. `<meta name="viewport" content="width=240, initial-scale=1.0, user-scalable=no">`. `body{width:240px;height:282px;overflow:hidden}`.
- Hardware is slow. Animate only `transform` and `opacity`; small DOM; no JS animation loops when a CSS animation will do; no blur/backdrop-filter/box-shadow animation; cap any list at ~40 rows.
- Events dispatched on `window`: `scrollUp`, `scrollDown`, `sideClick`, `longPressStart`, `longPressEnd`. A double click arrives as two `sideClick` ~50 ms apart.
- Native objects (all may be `undefined` in a desktop browser — always feature-detect):
  - `PluginMessageHandler.postMessage(JSON.stringify({message, useLLM, wantsR1Response, wantsJournalEntry}))`, replies via `window.onPluginMessage = (data) => …` (`data.message` string, `data.data` JSON string or object).
  - `closeWebView.postMessage("")`.
  - `window.creationStorage.plain|secure` with async `getItem/setItem/removeItem/clear`; values must be Base64 strings.
  - `window.creationSensors.accelerometer` (`isAvailable()`, `start(cb,{frequency})`, `stop()`).
- **Unverified** (the creation must probe at runtime and degrade gracefully): `navigator.mediaDevices.getUserMedia` + `MediaRecorder` (mic), `<audio>` playback, a community-reported native STT bridge `CreationVoiceHandler.postMessage('start'|'stop')` → `onPluginMessage` with `data.type === 'sttEnded'` and a transcript, on-screen keyboard for `<input>`.
- Install: QR code containing JSON `{"title","url","description","iconUrl","themeColor"}`; `url` must be HTTPS.

## 3. Bridge HTTP API

Base path `/api/v1`. JSON in/out, UTF-8. Errors: HTTP status + `{"error":{"code":"snake_case","message":"human text"}}`.

### 3.1 Auth

- **Device token**: `Authorization: Bearer <token>`; obtained by pairing. Stored hashed (sha256) on the bridge.
- **Admin token**: `Authorization: Bearer <admin_token>` for `/api/v1/admin/*`. Generated on first run. Additionally admin routes are refused (403 `admin_local_only`) unless the TCP peer is loopback **and** the request has no `X-Forwarded-For`/`CF-Connecting-IP`/`Tailscale-User-Login` style proxy header — unless the bridge was started with `--allow-remote-admin`.
- **Signed asset URLs**: images/audio are loaded by `<img>`/`<audio>` which cannot send headers, so the API returns relative URLs like `/api/v1/assets/avatar/coach?exp=1794300000&sig=<hex>`; `sig = HMAC-SHA256(secret, path + "\n" + exp)`, hex, first 32 chars. Default lifetime 24 h.
- Pairing attempts are rate limited (5 failures / 10 min per peer → 429).

### 3.2 Types

```ts
type Bot = {
  id: string;            // primary: profile name; additional gateway: "<gateway_id>:<profile_id>"
  gateway_id?: string;   // "local" for the primary gateway; absent on older bridges
  gateway_name?: string; // display label for grouping bots
  name: string;          // display name
  title: string;         // short role line, may be ""
  description: string;
  model: string;         // may be ""
  is_default: boolean;
  reachable: boolean;    // false → cannot chat; see locked_reason
  locked_reason: string; // "" when reachable. e.g. "No API key for this profile. Run: hermes r1 setup"
  avatar: Avatar;
  pet: Pet | null;
  state: "idle" | "busy" | "ask";   // from turns this bridge is running
  active: boolean;       // any activity in the last 90 s
  last: { text: string; ts: number } | null;  // ts = unix seconds
};
type Avatar = {
  kind: "shape" | "image";
  shape: string;         // circle|squircle|pill|triangle|hexagon|cloud|drop, or blobatar[:seed[:kind]]
  color: string;         // any CSS color, e.g. "hsl(210 68% 58%)"
  url: string;           // signed asset URL when kind == "image", else ""
};
type Pet = { url: string; cols: number; rows: number; cw: number; ch: number; frames: number; loop_ms: number };
type Session = { id: string; title: string; preview: string; last_active: number; message_count: number };
type Message = {
  id: string; role: "user" | "assistant"; text: string; ts: number;
  tools: { name: string; ok: boolean; preview: string }[];   // tool calls made while producing this assistant message
};
type TurnEvent = { seq: number } & (
  | { t: "start"; session_id: string }
  | { t: "delta"; text: string }                       // append to the assistant message
  | { t: "note"; text: string }                        // mid-turn commentary, shown dimmed
  | { t: "tool"; id: string; name: string; phase: "start" | "end"; preview: string; ok: boolean; ms: number }
  | { t: "ask"; request_id: string; kind: "approval" | "clarify"; title: string; detail: string;
      choices: { id: string; label: string }[] }       // clarify with no choices = free text answer
  | { t: "ask_done"; request_id: string }
  | { t: "speech"; url: string; mime: string }         // TTS of the final answer (signed URL)
  | { t: "end"; status: "completed" | "failed" | "cancelled"; text: string; error: string;
      usage: { input: number; output: number } }
);
type InboxItem = { id: number; ts: number; bot: string; title: string; body: string; level: "info" | "success" | "warn" };
type DeviceConfig = {
  theme: { preset: string; overrides: Record<string, string> };   // token name → CSS color
  font_scale: number;                                              // 0.85 – 1.3
  home: { greeting: string; clock: boolean };
  roster: { order: string[]; hidden: string[]; show_pets: boolean; active_strip: boolean };
  avatar_overrides: Record<string, { shape?: string; color?: string }>;
  quick_prompts: { id: string; label: string; text: string; bot: string }[];  // bot "" = current bot
  voice: { input: "auto" | "mediarecorder" | "native" | "off"; auto_speak: boolean };
  buttons: { side_click: "select"; double_click: "back" | "menu"; long_press: "talk" | "menu" };
  chat: { show_tools: boolean; show_notes: boolean };
};
```

Default `DeviceConfig`:
```json
{"theme":{"preset":"nous","overrides":{}},"font_scale":1,
 "home":{"greeting":"","clock":true},
 "roster":{"order":[],"hidden":[],"show_pets":true,"active_strip":true},
 "avatar_overrides":{},
 "quick_prompts":[{"id":"q1","label":"Status","text":"Give me a 2-sentence status update on what you're working on.","bot":""},
                  {"id":"q2","label":"What's next?","text":"What should I look at next? Keep it short.","bot":""}],
 "voice":{"input":"auto","auto_speak":false},
 "buttons":{"side_click":"select","double_click":"back","long_press":"talk"},
 "chat":{"show_tools":true,"show_notes":true}}
```

### 3.3 Device routes

| Method & path | Auth | Request | Response |
|---|---|---|---|
| `GET /api/v1/health` | none | | `{"ok":true,"name":"hermes-r1","version":"0.1.0","mock":bool}` |
| `POST /api/v1/pair` | none | `{"code":"ABCD2345","name":"Tony's R1"}` | `{"token":"…","device":{"id":"dev_…","name":"…"}}`; 400 `bad_code` |
| `GET /api/v1/bootstrap` | device | | `{"server":{"version","mock"},"device":{"id","name"},"config":DeviceConfig,"config_rev":int,"bots":Bot[],"voice":{"stt":bool,"tts":bool}}` |
| `GET /api/v1/bots` | device | | `{"bots":Bot[],"config_rev":int}` — `config_rev` lets the device notice Studio edits and re-bootstrap |
| `GET /api/v1/fleet` | device | | `{"needs_you":[{"turn_id","bot_id","ask"}],"working":[{"turn_id","bot_id"}],"gateways":[{"id","name","status"}]}` — `ask` is a turn ask event without `seq`. Status is `online`, `offline`, or `unknown`. Activity is limited to turns started by the authenticated device through this bridge. |
| `GET /api/v1/bots/{bot}/sessions?limit=20` | device | | `{"sessions":Session[]}` newest first |
| `GET /api/v1/bots/{bot}/sessions/{sid}/messages?limit=30` | device | | `{"messages":Message[]}` oldest→newest, last `limit` user/assistant messages; tool results folded into `tools` |
| `POST /api/v1/bots/{bot}/turns` | device | `{"text":"…","session_id":"…"\|null,"speak":bool}` | `{"turn_id":"t_…","session_id":"…"}`; 409 `bot_locked`, 429 `busy` |
| `GET /api/v1/turns/{tid}/events?after=0&wait=20` | device | | `{"events":TurnEvent[],"next":int,"done":bool}` — returns immediately if events with `seq > after` exist, else waits up to `wait` s (max 25). `next` = last seq returned (or `after`). `done` true once `end` has been delivered. |
| `POST /api/v1/turns/{tid}/stop` | device | | `{"ok":true}` |
| `POST /api/v1/turns/{tid}/respond` | device | `{"request_id":"…","choice":"once"}` or `{"request_id":"…","answer":"text"}` | `{"ok":true}` |
| `POST /api/v1/voice/transcribe` | device | raw audio body, `Content-Type: audio/*`, ≤ 10 MB | `{"text":"…"}`; 501 `stt_unavailable` |
| `POST /api/v1/voice/speak` | device | `{"text":"…"}` (≤ 2000 chars) | `{"url":"<signed>","mime":"audio/mpeg"}`; 501 `tts_unavailable` |
| `GET /api/v1/inbox?after=0&wait=0` | device | | `{"items":InboxItem[],"next":int}` |
| `GET /api/v1/assets/{kind}/{name}?exp&sig` | signature | kinds: `avatar` (bot id; optional `&s=96` square size), `pet` (bot id), `audio` (file id) | bytes with correct `Content-Type`, `Cache-Control: private, max-age=3600` |

Approval choice ids: `once`, `session`, `always`, `deny` (the bridge passes the id upstream).

Turn retention: a finished turn's event log is kept 10 minutes. One running turn per bot per device (second → 429 `busy`).

### 3.4 Admin routes (Studio)

| Method & path | Response |
|---|---|
| `GET /api/v1/admin/state` | `{"config":DeviceConfig,"config_rev":int,"devices":[{"id","name","created","last_seen"}],"bots":Bot[],"public_url":string,"local_url":string,"voice":{"stt","tts"},"mock":bool,"version":string}` |
| `PUT /api/v1/admin/config` (body `DeviceConfig`) | `{"config":DeviceConfig,"config_rev":int}`; validates + fills defaults; 400 `bad_config` |
| `POST /api/v1/admin/pair` | `{"code":"ABCD2345","expires":unix,"url":"<public_url>/?pair=ABCD2345","qr":{"title":"Hermes","url":…,"description":"Hermes Agent for rabbit r1","iconUrl":"<public_url>/nous-girl.png","themeColor":"#4a84fe"}}` — code: 8 chars from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, single use, 10 min |
| `DELETE /api/v1/admin/devices/{id}` | `{"ok":true}` |
| `POST /api/v1/admin/preview-token` | `{"token":"…"}` — a device token valid 12 h, device name "Studio preview", not listed in devices |
| `PUT /api/v1/admin/public-url` (`{"url":"https://…"}`) | `{"public_url":…}` |
| `POST /api/v1/admin/inbox` (`{"title","body","bot","level"}`) | `{"item":InboxItem}` — test notification |

## 4. Bridge internals

Python package directory `plugin/hermes-r1/` (hyphenated plugin dir, loaded by Hermes with relative imports; tests add it to `sys.path` under the alias `hermes_r1`).

```
plugin/hermes-r1/
  plugin.yaml
  __init__.py          # register(ctx): CLI command `r1`, tool `r1_notify`. Must import nothing heavy at module import.
  cli.py               # argparse setup/handlers: serve, pair, setup, doctor, status
  bridge/
    __init__.py
    host.py            # Host protocol + dataclasses  (CONTRACT — do not change signatures)
    mock_host.py       # MockHost: scripted bots, streaming, tools, approvals — no Hermes needed
    hermes_host.py     # HermesHost: real implementation
    gateways.py        # gateway configuration and host routing
    store.py           # JSON state file: admin token, signing secret, devices, config, pair codes, inbox
    config.py          # DeviceConfig defaults + validation/normalization
    auth.py            # token hashing, signing, loopback/admin checks, rate limiter
    turns.py           # TurnManager: runs Host.run_turn into an event log; long-poll waiters
    server.py          # aiohttp app factory `create_app(host, store, *, static_dir, allow_remote_admin)` + routes
    __main__.py        # `python -m bridge` style entry used by cli.py: argument parsing, tunnel helper
  static/app/ , static/studio/   # build output (committed)
  tests/
```

- State dir: `$HERMES_R1_HOME` or `$HERMES_HOME/r1` or `~/.hermes/r1`. Files: `state.json` (0600, atomic write), `inbox.jsonl` (appended by the `r1_notify` tool from the gateway process, tailed by the bridge), `audio/` (TTS cache, pruned > 1 h).
- Default bind `127.0.0.1:8717`.
- With `--gateways-file`, the bridge loads additional gateways at startup. Bot IDs are namespaced by gateway so sessions, turn controls, roster preferences and avatar overrides route independently even when profile or session IDs match. Primary bot IDs are unchanged. Clients receive gateway IDs and display names; the bridge retains the upstream URLs and API keys.
- Each entry in the JSON array has `id`, `name`, `url`, and `key_env`. IDs are unique and use 1–48 ASCII letters, digits, underscores, or hyphens; `local` is reserved. `key_env` names an environment variable set for the bridge process. An optional `profiles` object maps named profile IDs to their own `name` and `key_env`. The bridge sends those requests to `/p/<profile>/…` using that profile's key. Gateway URLs must use HTTPS or loopback HTTP and must not require redirects.
- Speech-to-text and text-to-speech use the primary bridge's local Hermes tools for every bot.
- `HermesHost` talks to the Hermes API server (`http://127.0.0.1:8642` by default):
  - default profile → unprefixed routes with `API_SERVER_KEY` from `$HERMES_HOME/.env`;
  - named profile → `/p/<profile>/…` with that profile's own `API_SERVER_KEY` from `$HERMES_HOME/profiles/<profile>/.env` (Hermes requires this; a profile without a key is `reachable:false`). `hermes r1 setup` offers to generate missing keys.
  - sessions: `GET/POST /api/sessions`, `GET /api/sessions/{id}/messages?inline_images=false`; only sessions with `source == "api_server"` are listed (Hermes refuses API turns on sessions owned by other surfaces with 409).
  - turn: `POST /api/sessions/{id}/chat/stream` (SSE). Real captures are in `docs/fixtures/`. Event mapping: `run.started`→`start`; `assistant.delta.delta`→`delta`; `assistant.commentary`→`note`; `tool.started`/`tool.completed`/`tool.failed`→`tool`; `approval.request`/clarify→`ask`; `run.completed|failed|cancelled`→`end`.
  - stop: `POST /v1/runs/{run_id}/stop`; approvals: `POST /v1/runs/{run_id}/approval` `{request_id, execution_generation, choice}`; clarify: `POST /v1/runs/{run_id}/clarify` `{request_id, execution_generation, answer}`.
  - profiles and avatars are read from disk: `$HERMES_HOME/profiles/<name>/profile.yaml` (`description`, `display_name`, `ui_meta["hermes-bots"]` = `{shape,color,title,imageKind,hidden,pinned}`), `assets/avatar.{png,jpg,webp}`, `config.yaml` `model.default`. The default profile uses `$HERMES_HOME/profile.yaml`, `$HERMES_HOME/assets/avatar.*`, `$HERMES_HOME/config.yaml`. Avatar `kind` is `image` when `imageKind == "photo"` and the file exists.
  - voice: imports Hermes' `tools.transcription_tools.transcribe_audio(file_path)` and `tools.tts_tool.text_to_speech_tool(text=…)` lazily; if the import fails, voice is reported unavailable.

## 5. Visual language (from Hermes Desktop, MIT © Nous Research)

### 5.1 Themes

Tokens (CSS custom properties on `:root`): `--bg`, `--surface`, `--border`, `--fg`, `--muted`, `--primary`, `--on-primary`, `--accent` (tinted surface for selection/user bubble), `--ok`, `--warn`, `--err`.

| preset | bg | surface | border | fg | muted | primary | accent |
|---|---|---|---|---|---|---|---|
| nous (default) | #0d1117 | #161b22 | #30363d | #e6edf3 | #8b949e | #4a84fe | #17243a |
| nous-light | #ffffff | #f6f8fa | #d0d7de | #1f2328 | #656d76 | #0053fd | #e3edff |
| github | #0d1117 | #161b22 | #30363d | #e6edf3 | #8b949e | #4f9e5e | #192a24 |
| catppuccin | #1e1e2e | #262637 | #585b70 | #cdd6f4 | #9399b2 | #cba6f7 | #3d3652 |
| everforest | #2d353b | #343f44 | #4f585e | #d3c6aa | #9da9a0 | #a7c080 | #434e47 |
| solarized | #002b36 | #073642 | #234751 | #839496 | #657b83 | #6ea1c4 | #144050 |
| midnight | #08081c | #101030 | #1e1e52 | #ddd6ff | #8f88c0 | #8b80e8 | #1a1a44 |
| ember | #160800 | #221004 | #3a1c08 | #ffd8b0 | #b08860 | #d97316 | #301600 |
| mono | #0e0e0e | #181818 | #2a2a2a | #eaeaea | #8a8a8a | #9a9a9a | #222222 |
| slate | #0d1117 | #151b23 | #30363d | #c9d1d9 | #8b949e | #58a6ff | #1e2530 |
| cyberpunk | #000a00 | #001400 | #003000 | #00ff41 | #00a82b | #00ff41 | #002000 |
| classic | #1a1a2e | #22223a | #cd7f32 | #fff8dc | #b8a888 | #ffbf00 | #2e2a1c |
| rabbit | #000000 | #111111 | #2a2a2a | #ffffff | #8a8a8a | #fe5000 | #2a1205 |

For every preset: `--ok #55a583`, `--warn #f59e0b`, `--err #f85149`; `--on-primary` is `#000` or `#fff`, whichever contrasts better with `--primary`.
Fonts: UI `-apple-system, 'Segoe UI', Roboto, system-ui, sans-serif`; mono `'JetBrains Mono', ui-monospace, Menlo, monospace` (system fallbacks only — no web-font downloads on the device).

### 5.2 Avatars (`shared/avatar.ts`)

Geometric "math face": a colored body shape with two eyes, on a `viewBox="0 0 40 40"` SVG.

Body shapes (verbatim geometry from Hermes Desktop):
- circle: `<circle cx="20" cy="20" r="17.5"/>`
- squircle: `<rect x="3" y="3" width="34" height="34" rx="11"/>`
- pill: `<rect x="2" y="7" width="36" height="26" rx="13"/>`
- triangle: `M20 5.5 L36 33.5 L4 33.5 Z`
- hexagon: `M20 3.5 L34.5 11.75 L34.5 28.25 L20 36.5 L5.5 28.25 L5.5 11.75 Z`
- cloud: `M11 32 a7.5 7.5 0 0 1 -1 -14.9 A9.5 9.5 0 0 1 29 12.5 A7 7 0 0 1 30 32 Z`
- drop: `M20 3 C20 3 6 20 6 27 a14 13.5 0 0 0 28 0 C34 20 20 3 20 3 Z`

Eyes: ellipses at x=15.4 and x=24.6, y=17.2 (cloud and triangle and drop: y=22), rx 2.2, ry 2.3; catchlight circle r 0.65 offset (-0.6,-0.7) in white at 0.9 opacity. Eye color `rgba(232,220,195,0.95)` when the body color's luminance < 110 (0–255), else `rgba(0,0,0,0.85)`.

Shape string mapping: the 7 names map to themselves. `blobatar[:seed[:kind]]` maps by kind: round→circle, boxy→squircle, capsule→pill, triangle→triangle, hexagon→hexagon, cloud→cloud, droplet→drop, organic→squircle, nub→pill, sun→circle; with no kind, pick from the 7 by hashing the seed (or the bot id). Anything unknown → hash of bot id.

Hash (verbatim from Hermes Desktop): `let h=0; for (const ch of name) h=(h*31+ch.charCodeAt(0))>>>0;` → shape `SHAPES[h%7]`, default color `hsl(${h%360} 68% 58%)`. The `default` profile's default color is `#8b5cf6`.

Animation (CSS only, classes on the `<svg>`): 
- idle: eyes blink — `scaleY(0.1)` for ~120 ms every ~3.2 s (transform-origin at the eye center).
- `.is-busy`: blink every 1.45 s, body gently rocks (`rotate(-4deg)`↔`rotate(4deg)`, 2.4 s), and three "thinking" dots under the face pulse in sequence (opacity 0.2→1, 0.3 s stagger).
- `.is-ask`: a small `--warn` badge dot at top-right.
- `.is-listening`: body scales with CSS variable `--level` (0–1): `scale(calc(1 + var(--level) * 0.12))`.
Image avatars: `<img>` clipped to a 30%-radius rounded square, same status classes on the wrapper.

### 5.3 Pets

Petdex spritesheets: 8 columns × 9 rows of 192×208 cells, 6 frames per row, 1100 ms loop. Rows: 0 idle, 1 running-right, 2 running-left, 3 waving, 4 jumping, 5 failed, 6 waiting, 7 running, 8 review. Render as a `div` with `background-image`, `background-size: calc(8 * W) calc(9 * H)`, animated with `steps(6)` on `background-position-x`; row chosen by state: idle→0, busy→7, ask→6, just finished→3 (for 2 s), failed→5. Display height ≤ 40 px. Sheets are the user's own installed pets served by the bridge; none are bundled.

### 5.4 Chat

- User message: bubble, `background: var(--accent)`, 1px `--border`, radius 10px, right-aligned, max-width 85%.
- Assistant message: no bubble, full width.
- Streaming cursor: a `▍` after the last character, `opacity` blink 1 s `step-end` infinite.
- Tool chip: one line, mono 10px, `--muted`: `icon name preview…` truncated with ellipsis; running = pulsing opacity; failed = `--err`. Icon by tool name (emoji stand-ins for Desktop's Phosphor set):
  `terminal`/`execute_code` ⌨ · `read_file` 📄 · `write_file`/`edit_file`/`patch` ✎ · `list_files` 🗂 · `search_files`/`web_search`/`session_search_recall` 🔍 · `web_extract`/`browser_*`/`web_*` 🌐 · `image_generate` 🖼 · `vision_analyze` 👁 · `memory` 🧠 · `todo` ☑ · `cronjob` ⏱ · `clarify` ❓ · `skill_view`/`skill_*` 📘 · `delegate*`/`subagent*` 👥 · default ⚙.
- Status dot: 6px circle — busy `--primary`, ask `--warn`, idle `--muted` at 40% opacity (4px).
- Context bar style for progress: `[██████░░░░]` mono characters.

## 6. Creation UX (240×282)

Layout: 18px status bar (clock left if enabled; connection dot + current bot name right) + 264px content.

Input model (all screens): **wheel** moves the highlighted item or scrolls; **side click** activates; **double click** = back (or menu, per config); **hold** = push-to-talk to the highlighted/current bot (per config). Touch works everywhere too (tap = activate, native scroll). In a desktop browser: ArrowUp/ArrowDown = wheel, Enter = click, Backspace/Escape = back, hold Space = hold.

Screens:
1. **Pair** — shown when there is no token. Auto-pairs from `?pair=CODE`; otherwise explains "Open Studio on your computer and scan the QR".
2. **Home / roster** — optional greeting line; "Active now" strip (small avatars of bots with `active || state != idle`) when enabled and non-empty; list rows 46px: 34px avatar (animated by state) + name (13px semi-bold) + last-message preview (10px muted, one line) + status dot; pet (if any and enabled) sits to the right of the highlighted row's avatar. Locked bots are dimmed with a 🔒. Final rows: "Inbox (n)" when there are items, "Settings". Order: `config.roster.order` first, then default bot, then reachable before locked, then alphabetical; `hidden` removed.
3. **Chat** — header 30px: avatar 22px, bot name, session title (muted); transcript; footer hint line 16px ("hold to talk · click for menu"). Opens the bot's most recent session (or a new one on first send). Wheel scrolls transcript; auto-sticks to bottom while streaming unless the user scrolled up.
4. **Menu sheet** (side click in chat) — Talk (if voice available), Type, Quick prompts, Stop (only while busy), New chat, Sessions, Read aloud (if TTS), Back to bots.
5. **Type** — a text `<input>` autofocused + Send row + quick-prompt rows.
6. **Listening overlay** — big avatar (96px, `.is-listening`), "Listening…", then "Transcribing…", then sends. Releasing in < 300 ms cancels.
7. **Ask sheet** — title, detail (scrollable), choice rows; resolves via `/respond`.
8. **Sessions** — rows: title, preview, relative time; first row "＋ New chat".
9. **Inbox** — notification cards.
10. **Settings** — theme (cycles presets, local preview only), font size, Diagnostics (runs capability probes: native bridge, storage, mic, MediaRecorder mime types, audio playback, network round-trip ms, user agent — shows ✓/✗ lines), Unpair, About.
11. **Offline / error banner** — non-blocking 16px banner at top when a request fails; retries with backoff.
12. **Fleet Now** — open from the home roster. Needs You shows pending approvals and questions, Working shows active turns without pending questions, and Gateways shows connection status across the fleet. Selecting a question opens its controls and returns to Fleet Now after answering.

Persistence on device: token in `creationStorage.secure` (fallback `localStorage`); last bot + last session per bot in `creationStorage.plain` (fallback `localStorage`).

Voice input strategy (`config.voice.input`): `auto` = MediaRecorder if `getUserMedia` works, else native `CreationVoiceHandler` if present, else voice is hidden. Recording: mono, prefer `audio/webm;codecs=opus`, fall back to whatever `MediaRecorder.isTypeSupported` accepts; upload to `/voice/transcribe`.

Polling: roster refresh every 8 s while Home is visible (pause when `document.hidden`); turn events by chained long-poll (`wait=20`), re-issued immediately; on network error back off 0.5 s → 4 s.

## 7. Studio UX

Single page at `/studio/`, opened as `http://127.0.0.1:8717/studio/#admin=<token>` (token then kept in `sessionStorage` and stripped from the URL). Left: tabbed editor. Right: sticky **live emulator** — an R1-shaped frame containing an `<iframe src="/?emu=1#token=<preview-token>">` at exactly 240×282 CSS px scaled ×2, with on-screen wheel ▲▼, side button (click / hold), and keyboard shortcuts forwarded to the iframe as the R1 window events. After every successful save the iframe is told to re-bootstrap via `postMessage({type:"r1:reload-config"})`.

Tabs: **Devices** (public URL field, "Pair a device" → QR code rendered client-side + code + expiry countdown, device list with remove, "Send test notification") · **Appearance** (preset gallery with swatches, per-token color overrides, font scale, greeting, clock) · **Bots** (drag/▲▼ reorder, hide toggle, avatar shape + color override with live avatar preview, pets toggle, active strip toggle) · **Quick prompts** (add/edit/remove/reorder, optional bot) · **Voice & buttons** (voice input mode, auto-speak, double-click and long-press behavior, show tools / notes).

Edits apply to a local draft → debounced `PUT /admin/config` (600 ms) → "Saved ✓".

## 8. Security posture

The bridge can drive an agent that controls the user's computer, and it is exposed to the internet through a tunnel. Therefore: device tokens are 256-bit random and stored hashed; pairing codes are single-use and short-lived; admin is loopback-only by default; upstream Hermes keys never reach the browser; request bodies are size-limited (64 KB JSON, 10 MB audio); all text from Hermes is rendered with `textContent` (never `innerHTML`); the static apps send `Content-Security-Policy: default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; connect-src 'self'`.

## 9. Not in v0.1

Faithful Blobatar rendering (mapped to the 7 shapes for now), group chats, routines editor, image attachments from the R1 camera, per-device configs, WebSocket transport.
