# Hermes R1 OS

Canonical path: `/Users/tony/projects/rabbit-r1-hermes-OS`

**Purpose:** an open-source Hermes Agent experience for the Rabbit R1 — bot roster
with Hermes Desktop-style avatars, streaming chat with tool activity, voice,
approvals, and a desktop Studio to customize it all.

## Main files

| Path | What |
|---|---|
| `docs/SPEC.md` | The contract: architecture, bridge API, visual language, UX |
| `docs/fixtures/` | Real (trimmed) Hermes API server captures used by tests |
| `plugin/hermes-r1/` | Hermes plugin + bridge server (Python, aiohttp) |
| `plugin/hermes-r1/static/` | Built web apps served by the bridge (build output) |
| `creation/` | The R1 app (vanilla TS, 240×282) |
| `studio/` | Desktop customizer + emulator (vanilla TS) |
| `shared/` | Protocol types, API client, avatars, themes |
| `scripts/build.mjs` | esbuild build for both web apps |
| `scripts/dev_bridge.py` | Run the bridge from the repo without installing the plugin |

## Run

```bash
npm install
npm run build
npm run bridge:mock        # mock bots, no Hermes needed → open the Studio link it prints
npm run test:py            # bridge tests
npm run typecheck
```

Against real Hermes: install the plugin (see README), then `hermes r1 serve`.

## Deliverables

The deliverable is the repository itself (plugin + built apps under
`plugin/hermes-r1/`). There is no separate `outputs/` folder.
