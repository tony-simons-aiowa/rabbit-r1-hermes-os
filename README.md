# Hermes R1 OS

A [Hermes Agent](https://hermes-agent.nousresearch.com/) experience for the Rabbit R1 that feels like Hermes Desktop in your hand: a roster of your bots with animated avatars, streaming chat with live tool activity, approvals, hold-to-talk, themes — and a desktop **Studio** to customize all of it with a live emulator.

> **Status: v0.1, early.** Everything runs end to end against Hermes and in the emulator. It has **not yet been verified on a physical R1** — see [What is not verified yet](#what-is-not-verified-yet).
>
> Unofficial. Not affiliated with rabbit inc. or Nous Research.

## How it works

```
R1 app ──HTTPS tunnel──▶ bridge on your computer (127.0.0.1:8717) ──▶ Hermes API server (127.0.0.1:8642)
Studio (browser on your computer) ──▶ bridge
```

| Piece | Where | What it does |
|---|---|---|
| **Plugin + bridge** | `plugin/hermes-r1/` | A Hermes plugin. `hermes r1 serve` runs a small server that hosts the two web apps and a JSON API, and talks to your Hermes API server. Your Hermes keys stay in this process. Also gives your agents an `r1_notify` tool to push a card to the R1. |
| **R1 app** | `creation/` | A rabbit "creation" (a 240×282 web app installed by QR code — no jailbreak, no firmware changes). |
| **Studio** | `studio/` | Pair devices, pick themes, reorder and restyle bots, edit quick prompts, voice and button behavior, with a live R1 emulator. |

## Features

- **Bot roster** — every Hermes profile is a bot, with its Bot Mode avatar (shape + color, or uploaded photo), status dot, last reply and an "Active now" strip.
- **Animated avatars** — the geometric faces from Hermes Desktop: they blink when idle and think while a turn runs. Pets (petdex sprites you have installed) sit beside the highlighted bot.
- **Streaming chat** — markdown, code blocks, live tool chips, mid-turn commentary, stop.
- **Approvals and questions** — when Hermes needs a decision it appears as a sheet you answer with the wheel.
- **Voice** — hold the side button to talk (uses Hermes' speech-to-text), optional read-aloud (Hermes' text-to-speech).
- **Sessions, quick prompts, inbox** — resume chats, one-click prompts, notification cards your agents send with `r1_notify`.
- **13 themes** — the Hermes Desktop presets plus `rabbit`, each token overridable.
- **Hardware-first input** — wheel = move/scroll, click = select, double-click = back, hold = talk. Touch works too.

## Try it in 2 minutes (no R1, no Hermes needed)

Requires Node 20+ and [uv](https://docs.astral.sh/uv/).

```bash
npm install
npm run build
npm run bridge:mock
```

Open the `studio:` link it prints. The emulator on the right is the real R1 app talking to scripted demo bots. Arrow keys = wheel, Enter = side click, hold Space = hold. Try typing a message containing `approve`, `clarify`, `long` or `fail`.

## Use it with your Hermes

1. **Enable the Hermes API server** if you have not already: set `API_SERVER_ENABLED=true` and an `API_SERVER_KEY` in `~/.hermes/.env`, then start `hermes gateway`. ([Hermes docs](https://hermes-agent.nousresearch.com/docs))
2. **Install the plugin** — link or copy `plugin/hermes-r1` into `~/.hermes/plugins/hermes-r1` and enable it:
   ```bash
   ln -s "$PWD/plugin/hermes-r1" ~/.hermes/plugins/hermes-r1
   hermes plugins enable hermes-r1
   ```
3. **Check your setup:**
   ```bash
   hermes r1 doctor
   ```
4. **(Optional) unlock your other profiles.** Hermes only lets a client reach a named profile if that profile has its own `API_SERVER_KEY` and `gateway.multiplex_profiles` is on. This generates missing keys (it backs up each `.env` first and never prints a key):
   ```bash
   hermes r1 setup --dry-run
   hermes r1 setup
   hermes gateway restart
   ```
5. **Run the bridge with a public HTTPS address** (the R1 can only load HTTPS pages):
   ```bash
   hermes r1 serve --tunnel tailscale
   ```
   `--tunnel tailscale` (Tailscale Funnel) gives a stable address and is recommended. `--tunnel cloudflared` gives a temporary address that changes on every restart, so you would have to re-add the app each time. You can also pass `--public-url https://…` if you run your own reverse proxy.
6. **Pair your R1** — open the `studio:` link printed by the bridge, go to **Devices → Pair a device**, and scan the QR code with the R1.

## Security model

The bridge can drive an agent that controls your computer, and the tunnel makes it reachable from the internet. So:

- The R1 holds only a random device token (stored hashed on the bridge). Hermes API keys never leave the bridge.
- Pairing codes are single-use and expire after 10 minutes.
- Studio's admin API needs the admin token **and** only answers requests that arrive directly on loopback with a loopback `Host` — it is not reachable through the tunnel unless you start the bridge with `--allow-remote-admin`.
- Images and audio are served through short-lived signed URLs.
- All text from Hermes is rendered as text, never as HTML; the apps ship a strict Content-Security-Policy.

Anyone who has a paired device's token can talk to your agents. Remove lost devices in **Studio → Devices**.

## What is not verified yet

These depend on the R1's WebView and have only been tested in desktop browsers:

- network calls from a creation to the bridge's tunnel origin,
- microphone capture (`getUserMedia` / `MediaRecorder`) and the community-reported native speech bridge,
- audio playback for read-aloud,
- the on-screen keyboard for the Type screen.

The app probes for each and degrades (voice hides itself; typing and quick prompts still work). **Settings → Diagnostics** on the device shows what it supports — if you run this on an R1, please open an issue with that screen.

Also not yet exercised for real: clarification prompts and approvals against a live Hermes (tested against recorded fixtures and a fake upstream only), the `--tunnel` helpers, loading the plugin through `hermes plugins enable`, and voice through Hermes' speech providers.

## Known limits

- The R1 can only open chats it started. Hermes does not let API clients attach to sessions owned by Desktop, the CLI or the TUI.
- Hermes Desktop's newer "blobatar" faces are drawn as the nearest of the seven classic shapes.
- It is an app you launch, not a replacement for the R1 home screen.
- No group chats, routines editor or camera attachments yet.

## Development

```bash
npm run watch          # rebuild web apps on change
npm run bridge:mock    # bridge with demo bots on :8717
npm run typecheck
npm run test:py        # bridge + plugin tests
```

[`docs/SPEC.md`](docs/SPEC.md) is the contract for the bridge API, the visual language and the UX. Change it first.

## Credits

- Visual language (avatar geometry, theme palettes, tool icon mapping) follows [Hermes Agent / Hermes Desktop](https://github.com/NousResearch/hermes-agent), MIT © Nous Research.
- R1 platform facts from [rabbit-hmi-oss/creations-sdk](https://github.com/rabbit-hmi-oss/creations-sdk).
- QR codes by [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator), MIT.

## License

MIT
