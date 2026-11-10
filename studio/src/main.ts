// Studio entry: reads the admin token, loads state, then builds the tabs and the live emulator.
import { Api, ApiError } from "../../shared/api";
import { injectAvatarCss } from "../../shared/avatar";
import type { AdminState } from "../../shared/protocol";
import { errText, h, type Tab } from "./dom";
import { Emulator } from "./emulator";
import { connStatus, getAdmin, initState, onSaved, onStatus, refreshAdmin, saveStatus } from "./state";
import { mountAppearance } from "./tabs/appearance";
import { mountBots } from "./tabs/bots";
import { mountDevices } from "./tabs/devices";
import { mountPrompts } from "./tabs/prompts";
import { mountVoice } from "./tabs/voice";

const TOKEN_KEY = "hermes-r1-studio-admin";
const REFRESH_MS = 10000;

const TABS: { id: string; label: string; mount: () => Tab }[] = [
  { id: "devices", label: "Devices", mount: mountDevices },
  { id: "appearance", label: "Appearance", mount: mountAppearance },
  { id: "bots", label: "Bots", mount: mountBots },
  { id: "prompts", label: "Quick prompts", mount: mountPrompts },
  { id: "voice", label: "Voice & buttons", mount: mountVoice },
];

/** Reads #admin=<token> from the URL, stores it for this tab, and strips it from the address bar. */
function readToken(): string {
  const m = /(?:^|[#&])admin=([^&]+)/.exec(location.hash);
  if (m) {
    let token = m[1];
    try {
      token = decodeURIComponent(token);
    } catch {
      /* keep the raw value */
    }
    storeToken(token);
    history.replaceState(null, "", location.pathname + location.search);
    return token;
  }
  try {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

function storeToken(token: string): void {
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* storage blocked: the token only lives for this page load */
  }
}

function clearToken(): void {
  try {
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

function showAuth(root: HTMLElement, message: string): void {
  const input = h("input", {
    type: "password",
    autocomplete: "off",
    spellcheck: false,
    placeholder: "Paste the admin token",
    "aria-label": "Admin token",
    class: "mono",
  });
  const form = h(
    "form",
    {
      class: "auth-form",
      onsubmit: (e: Event) => {
        e.preventDefault();
        const token = input.value.trim();
        if (!token) return;
        storeToken(token);
        void start(root, token);
      },
    },
    h("label", { class: "field" }, h("span", { class: "field-label" }, "Admin token"), input),
    h("button", { type: "submit", class: "btn primary" }, "Open Studio"),
  );
  root.replaceChildren(
    h(
      "div",
      { class: "auth-wrap" },
      h(
        "section",
        { class: "auth-card" },
        h("h1", null, "Open Studio from the link printed by hermes r1 serve"),
        h(
          "p",
          { class: "muted" },
          "Studio manages your bridge from this computer. The link carries the admin token. If you lost the link, paste the token here.",
        ),
        message ? h("div", { class: "callout err", role: "alert" }, message) : null,
        form,
      ),
    ),
  );
  input.focus();
}

async function start(root: HTMLElement, token: string): Promise<void> {
  if (!token) {
    showAuth(root, "");
    return;
  }
  const api = new Api({ token });
  let admin: AdminState;
  try {
    admin = await api.adminState();
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      clearToken();
      showAuth(root, "That admin token wasn't accepted. Open the current link from hermes r1 serve, or paste the token again.");
    } else if (e instanceof ApiError && e.status === 403 && e.code === "admin_local_only") {
      showAuth(
        root,
        "Studio must be opened on the computer that runs the bridge. Admin access is local-only by default.",
      );
    } else {
      showAuth(root, errText(e) + " Check that hermes r1 serve is running, then reload.");
    }
    return;
  }
  initState(api, admin);
  mountApp(root, api);
}

function mountApp(root: HTMLElement, api: Api): void {
  injectAvatarCss();

  const pill = h("span", { class: "pill connecting", role: "status" });
  const saveEl = h("span", { class: "save-status", role: "status", "aria-live": "polite" });
  const verEl = h("span", { class: "muted small" });
  const header = h(
    "header",
    { class: "top" },
    h(
      "div",
      { class: "top-inner" },
      h("div", { class: "brand" }, h("span", { class: "mark", "aria-hidden": "true" }), h("span", null, "Hermes R1 · Studio")),
      h("div", { class: "top-right" }, pill, saveEl, verEl),
    ),
  );

  const renderStatus = (): void => {
    const c = connStatus();
    pill.className = "pill " + c.kind;
    pill.textContent = c.text;
    const s = saveStatus();
    saveEl.className = "save-status " + s.kind;
    saveEl.textContent = s.text;
    const version = getAdmin().version;
    verEl.textContent = version ? "bridge v" + version : "";
  };
  onStatus(renderStatus);

  const emu = new Emulator();
  const mounted = new Map<string, Tab>();
  const buttons = new Map<string, HTMLButtonElement>();
  const panels = new Map<string, HTMLElement>();
  let active = "";

  function activate(id: string, moveFocus = false): void {
    if (!mounted.has(id)) {
      const spec = TABS.find((t) => t.id === id);
      if (!spec) return;
      const tab = spec.mount();
      mounted.set(id, tab);
      const panel = h("div", { class: "panel", role: "tabpanel", id: "panel-" + id, "aria-labelledby": "tab-" + id }, tab.el);
      panels.set(id, panel);
      panelHost.append(panel);
    }
    active = id;
    for (const t of TABS) {
      const on = t.id === id;
      const btn = buttons.get(t.id);
      if (btn) {
        btn.setAttribute("aria-selected", String(on));
        btn.tabIndex = on ? 0 : -1;
        if (on && moveFocus) btn.focus();
      }
      const panel = panels.get(t.id);
      if (panel) panel.hidden = !on;
    }
  }

  const panelHost = h("div", { class: "panels" });
  const tabBar = h("div", { class: "tabs", role: "tablist", "aria-label": "Studio sections" });
  for (const t of TABS) {
    const btn = h("button", {
      type: "button",
      role: "tab",
      id: "tab-" + t.id,
      class: "tab",
      "aria-selected": "false",
      "aria-controls": "panel-" + t.id,
      tabindex: -1,
      onclick: () => activate(t.id),
    }, t.label);
    buttons.set(t.id, btn);
    tabBar.append(btn);
  }
  tabBar.addEventListener("keydown", (e: KeyboardEvent) => {
    const ids = TABS.map((t) => t.id);
    const i = ids.indexOf(active);
    let next = -1;
    if (e.key === "ArrowRight") next = (i + 1) % ids.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + ids.length) % ids.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = ids.length - 1;
    if (next >= 0) {
      e.preventDefault();
      activate(ids[next], true);
    }
  });

  const layout = h(
    "main",
    { class: "layout" },
    h("div", { class: "col-main" }, tabBar, panelHost),
    h("aside", { class: "col-side" }, emu.el),
  );
  root.replaceChildren(header, layout, h("div", { id: "toasts", class: "toasts", "aria-live": "polite" }));

  onSaved(() => emu.reloadConfig());
  renderStatus();
  void emu.start(api);
  activate(TABS[0].id);
  window.setInterval(() => {
    void refreshAdmin();
  }, REFRESH_MS);
}

const root = document.getElementById("app");
if (!root) throw new Error("Studio: missing #app element");
void start(root, readToken());
