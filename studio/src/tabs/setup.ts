// Guided first-run checklist. Bridge/admin checks are local; physical R1 steps require confirmation.
import { Api, followTurn } from "../../../shared/api";
import { button, card, errText, h, type Tab } from "../dom";
import { drawQr } from "../qr";
import { getAdmin, getApi, onChange } from "../state";

type Check = { state: "idle" | "busy" | "ok" | "fail"; detail: string; next: string };
const idle = (detail: string, next: string): Check => ({ state: "idle", detail, next });

export function mountSetup(): Tab {
  const checks: Record<string, Check> = {
    bridge: idle("Not checked yet", "Check that Studio can reach the local bridge."),
    gateway: idle("Not checked yet", "Check the configured Hermes gateway connection."),
    https: idle("Not checked yet", "Save a public HTTPS address in Devices."),
    pair: idle("Not paired yet", "Create a pairing QR code, then scan it with the physical R1."),
    message: idle("Not sent yet", "Send a first message through Studio's emulator preview identity."),
  };
  let pairBox = h("div", { class: "setup-pair" });
  let sample = h("textarea", { rows: 2, maxlength: 1000, "aria-label": "First message", value: "Hello! Please introduce yourself in one sentence." });
  let physicalConfirmed = false;
  let httpsConfirmed = false;
  let savedAddress = getAdmin().public_url.trim();
  let pairExpires = 0;
  let pairBusy = false;
  let sending = false;
  let botSig = "";
  const botPicker = h("select", { "aria-label": "Bot for first message" });
  const botDestination = h("span", { class: "muted small" });
  const httpsBox = h("input", { type: "checkbox" });
  const rows = h("ol", { class: "setup-checklist" });
  const checkButton = button("Run computer checks", () => void runChecks(), { variant: "primary" });
  const pairButton = button("Create pairing QR", () => void createPair(), { variant: "primary" });
  const sendButton = button("Send first message", () => void sendFirstMessage(), { variant: "primary" });
  const physicalBox = h("input", { type: "checkbox", disabled: true });

  function line(id: string, title: string, note: string, action?: HTMLElement): HTMLElement {
    const c = checks[id];
    const badge = c.state === "ok" ? "Done" : c.state === "fail" ? "Needs attention" : c.state === "busy" ? "Checking…" : "Not checked";
    return h("li", { class: "setup-step setup-" + c.state },
      h("div", { class: "setup-step-head" }, h("strong", null, title), h("span", { class: "setup-badge" }, badge)),
      h("p", { class: "muted" }, c.detail),
      h("span", { class: "setup-source" }, note),
      c.state === "fail" ? h("p", { class: "setup-next" }, "Next: " + c.next) : null,
      action ?? null,
    );
  }

  function render(): void {
    const admin = getAdmin();
    const address = admin.public_url.trim();
    if (savedAddress !== address) {
      savedAddress = address;
      httpsConfirmed = false;
      httpsBox.checked = false;
      physicalConfirmed = false;
      physicalBox.checked = false;
      pairExpires = 0;
      pairBox.replaceChildren();
      checks.https = idle("Open the saved bridge's health endpoint and confirm it loads.", "Check the tunnel and saved address in Devices.");
      checks.pair = idle("Not paired yet", "Create a pairing QR code, then scan it with the physical R1.");
    }
    const allReady = checks.bridge.state === "ok" && checks.gateway.state === "ok" && checks.https.state === "ok";
    const availableBots = admin.bots.filter((b) => b.reachable);
    const newBotSig = availableBots.map((b) => b.id + ":" + b.name).join("|");
    if (newBotSig !== botSig) {
      const previous = botPicker.value;
      botSig = newBotSig;
      botPicker.replaceChildren(...availableBots.map((b) => h("option", { value: b.id }, b.name)));
      botPicker.value = availableBots.some((b) => b.id === previous)
        ? previous
        : (availableBots.find((b) => b.is_default) ?? availableBots[0])?.id ?? "";
    }
    const destination = availableBots.find((b) => b.id === botPicker.value);
    botDestination.textContent = destination ? `Studio preview → ${destination.name}` : "No reachable bot is configured";
    checkButton.disabled = checks.bridge.state === "busy" || checks.gateway.state === "busy" || checks.https.state === "busy";
    pairButton.disabled = !allReady || pairBusy;
    physicalBox.disabled = !pairExpires;
    sendButton.disabled = checks.gateway.state !== "ok" || sending;
    rows.replaceChildren(
      line("bridge", "1. Bridge health", "Computer check · local admin API"),
      line("gateway", "2. Hermes gateway", "Computer check · transport reachability"),
      line("https", "3. Public HTTPS", address ? "Computer check · " + address : "Computer check · no address saved", address
        ? h("div", { class: "stack" }, h("p", { class: "muted small" }, "The page should show JSON with ok: true and name: hermes-r1."), button("Open HTTPS health check", () => {
          const base = address.endsWith("/") ? address : address + "/";
          const target = new URL("api/v1/health", base);
          window.open(target.href, "_blank", "noopener,noreferrer");
        }, { variant: "ghost" }), h("label", { class: "setup-confirm" }, httpsBox, " I confirmed the HTTPS health endpoint loads in the new tab"))
        : h("p", { class: "setup-next" }, "Next: Open Devices and save the HTTPS address.")),
      line("pair", "4. Pair the physical R1", "Physical-device confirmation · scanning the QR is not verified by the emulator", h("div", { class: "stack" }, pairButton, pairBox, h("label", { class: "setup-confirm" }, physicalBox, " I scanned the code and Hermes opened on my physical R1"))),
      line("message", "5. Send a first message", "Computer/emulator check · sends a real Hermes turn using Studio's preview identity", h("div", { class: "stack" }, botPicker, botDestination, sample, sendButton, h("p", { class: "muted small" }, "Physical follow-through: on the paired R1, open a bot, choose Type, send a message, and verify its reply. Preview completion does not confirm physical-device messaging. Optionally open a group and hold then release to test voice."))),
    );
  }

  async function runChecks(): Promise<void> {
    for (const key of ["bridge", "gateway", "https"] as const) checks[key] = { state: "busy", detail: "Checking…", next: "" };
    render();
    try {
      const health = await getApi().health();
      checks.bridge = health.ok
        ? { state: "ok", detail: `Bridge v${health.version}${health.mock ? " · mock mode" : ""}`, next: "" }
        : { state: "fail", detail: "Bridge health check failed.", next: "Restart hermes r1 serve and retry." };
    } catch (e) {
      checks.bridge = { state: "fail", detail: errText(e), next: "Restart hermes r1 serve, then reload Studio." };
    }
    try {
      const result = await getApi().adminSetupStatus();
      const online = result.gateways.filter((g) => g.status === "online");
      checks.gateway = online.length
        ? { state: "ok", detail: online.map((g) => g.name).join(", ") + " · transport reachable", next: "" }
        : { state: "fail", detail: result.gateways.map((g) => `${g.name}: ${g.status}`).join(", ") || "No gateway status available.", next: "Start or reconnect the Hermes gateway, then retry." };
    } catch (e) {
      checks.gateway = { state: "fail", detail: errText(e), next: "Check hermes gateway status, then retry." };
    }
    const address = getAdmin().public_url.trim();
    if (!/^https:\/\//i.test(address)) {
      checks.https = { state: "fail", detail: "A public HTTPS address is required.", next: "Open Devices and save the HTTPS tunnel address." };
    } else {
      checks.https = httpsConfirmed
        ? { state: "ok", detail: "You confirmed the HTTPS health endpoint loads in your browser.", next: "" }
        : idle("Open the saved bridge's health endpoint and confirm it loads.", "If it fails, check the tunnel, DNS, and saved address in Devices.");
    }
    render();
  }

  async function createPair(): Promise<void> {
    if (pairBusy) return;
    pairBusy = true;
    const requestedAddress = getAdmin().public_url.trim();
    physicalConfirmed = false;
    physicalBox.checked = false;
    pairExpires = 0;
    pairBox.replaceChildren();
    checks.pair = { state: "busy", detail: "Creating a short-lived pairing code…", next: "" };
    render();
    try {
      const pair = await getApi().adminPair();
      if (getAdmin().public_url.trim() !== requestedAddress) {
        checks.pair = { state: "fail", detail: "The saved HTTPS address changed while creating this code; the response was discarded.", next: "Run checks again and create a fresh code." };
        pairBusy = false;
        render();
        return;
      }
      pairExpires = pair.expires * 1000;
      const canvas = h("canvas", { class: "qr", role: "img", "aria-label": "Physical R1 pairing QR code" });
      const drawn = drawQr(canvas, JSON.stringify(pair.qr));
      pairBox.replaceChildren(drawn ? canvas : h("p", { class: "callout err" }, "QR is too long to draw. Use the pairing link: " + pair.url), h("p", { class: "muted" }, "Code " + pair.code + " · expires " + new Date(pair.expires * 1000).toLocaleTimeString()));
      checks.pair = idle("Code created. Scan it and open Hermes on the physical R1, then confirm below.", "Pairing is not complete until you confirm it on the R1.");
    } catch (e) {
      checks.pair = { state: "fail", detail: errText(e), next: "Save a valid HTTPS address in Devices, then create a new code." };
    }
    pairBusy = false;
    render();
  }

  async function sendFirstMessage(): Promise<void> {
    const admin = getAdmin();
    const bot = admin.bots.find((b) => b.id === botPicker.value && b.reachable);
    if (!bot) {
      checks.message = { state: "fail", detail: "No reachable bot is configured.", next: "Configure a bot and gateway, then retry." };
      render();
      return;
    }
    sending = true;
    checks.message = { state: "busy", detail: "Starting a real Hermes turn…", next: "" };
    render();
    try {
      const preview = await getApi().adminPreviewToken();
      const api = new Api({ base: getApi().base.replace(/\/api\/v1$/, ""), token: preview.token });
      const turn = await api.startTurn(bot.id, sample.value.trim() || "Hello!", null, false);
      checks.message = { state: "busy", detail: `Message sent from Studio preview to ${bot.name}; waiting for the first reply…`, next: "" };
      const watcher = followTurn(api, turn.turn_id, (event) => {
        if (event.t === "end") {
          if (event.status === "completed") checks.message = { state: "ok", detail: `Reply from ${bot.name}: ${(event.text || "Turn completed without a text reply.").slice(0, 240)}`, next: "" };
          else checks.message = { state: "fail", detail: event.error || "The first turn did not complete.", next: "Check the gateway and bot configuration, then try again." };
          sending = false;
          render();
        }
      });
      void watcher.done.catch((e) => {
        checks.message = { state: "fail", detail: "Could not read the turn result: " + errText(e), next: "Check the bridge connection. If the turn may still be running, inspect it before sending another message." };
        sending = false;
        render();
      });
    } catch (e) {
      checks.message = { state: "fail", detail: errText(e), next: "Check the gateway and bot configuration, then retry." };
      sending = false;
    }
    render();
  }

  physicalBox.addEventListener("change", () => {
    physicalConfirmed = physicalBox.checked;
    checks.pair = physicalConfirmed
      ? { state: "ok", detail: "You confirmed pairing on the physical R1.", next: "" }
      : idle("Code created; physical pairing is not confirmed yet.", "Scan the code with the R1, open Hermes, then confirm here.");
    render();
    physicalBox.checked = physicalConfirmed;
  });
  httpsBox.addEventListener("change", () => {
    httpsConfirmed = httpsBox.checked;
    checks.https = httpsConfirmed
      ? { state: "ok", detail: "You confirmed the HTTPS health endpoint loads in your browser.", next: "" }
      : idle("Open the saved bridge's health endpoint and confirm it loads.", "If it fails, check the tunnel, DNS, and saved address in Devices.");
    render();
    httpsBox.checked = httpsConfirmed;
  });
  physicalBox.addEventListener("click", (event) => {
    if (!pairExpires || Date.now() >= pairExpires) {
      event.preventDefault();
      physicalBox.checked = false;
      checks.pair = { state: "fail", detail: "The pairing code expired or has not been created.", next: "Create a fresh QR code and scan it on the physical R1." };
      render();
    }
  });

  const el = h("div", { class: "stack-lg" },
    card("Guided setup", h("p", { class: "muted" }, "Computer checks verify the local bridge, gateway transport, and saved HTTPS endpoint. Emulator checks only verify the desktop preview; pairing must be confirmed on the physical R1."), h("div", { class: "row" }, checkButton), rows),
  );
  onChange(render);
  render();
  return { el, sync: render };
}
