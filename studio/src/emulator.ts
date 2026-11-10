// Live emulator: an R1-shaped frame around the creation app, loaded in a 240x282 iframe scaled 1.25x.
// Wheel, side button and keyboard are forwarded to the iframe as the R1's window events.
import type { Api } from "../../shared/api";
import { h } from "./dom";

const HOLD_MS = 350;
const WHEEL_GAP_MS = 80;

function isFormField(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return t.isContentEditable || t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT";
}

function inEmulator(t: EventTarget | null): boolean {
  return t instanceof Element && t.closest(".emu") !== null;
}

const kbd = (text: string): HTMLElement => h("kbd", null, text);

export class Emulator {
  readonly el: HTMLElement;
  private readonly frame: HTMLIFrameElement;
  private readonly note: HTMLElement;
  private api: Api | null = null;
  private holdTimer = 0;
  private holdSource: "pointer" | "key" | null = null;
  private longPressed = false;
  private wheelAt = 0;

  constructor() {
    this.note = h("div", { class: "screen-note", role: "status", hidden: true });
    this.frame = h("iframe", { class: "screen", title: "Device screen preview", onload: () => this.onLoad() });

    const sideBtn = h("button", {
      type: "button",
      class: "side-btn",
      "aria-label": "Side button: click to select, hold to talk",
      onpointerdown: (e: PointerEvent) => this.sidePointerDown(e),
      onpointerup: () => this.holdEnd("pointer"),
      onpointercancel: () => this.holdEnd("pointer", true),
      onclick: (e: MouseEvent) => {
        // detail 0 means keyboard-initiated (Enter); pointer clicks are handled on pointerup.
        if (e.detail === 0) this.dispatch("sideClick");
      },
    });
    const wheelUp = h("button", { type: "button", "aria-label": "Scroll up", onclick: () => this.dispatch("scrollUp") }, "▲");
    const wheelDown = h("button", { type: "button", "aria-label": "Scroll down", onclick: () => this.dispatch("scrollDown") }, "▼");

    const device = h(
      "div",
      { class: "device", onwheel: (e: WheelEvent) => this.onWheel(e) },
      h("div", { class: "bezel" }, h("div", { class: "screen-box" }, this.frame, this.note)),
      h(
        "div",
        { class: "edge" },
        sideBtn,
        h("div", { class: "wheel", role: "group", "aria-label": "Scroll wheel" }, wheelUp, wheelDown),
      ),
    );
    const restart = h("button", { type: "button", class: "btn", onclick: () => this.restart() }, "Restart");

    this.el = h(
      "section",
      { class: "emu", "aria-label": "Live device preview" },
      device,
      h("div", { class: "emu-actions" }, restart),
      h(
        "p",
        { class: "emu-hint" },
        "Scroll wheel or ",
        kbd("↑"),
        " ",
        kbd("↓"),
        " scrolls · ",
        kbd("Enter"),
        " or click selects · hold ",
        kbd("Space"),
        " or the side button to talk",
      ),
    );

    window.addEventListener("keydown", (e) => this.onKeyDown(e));
    window.addEventListener("keyup", (e) => this.onKeyUp(e));
  }

  /** Probe that the creation app is built, fetch a preview token, then load the frame. */
  async start(api: Api): Promise<void> {
    this.api = api;
    this.frame.removeAttribute("src");
    this.setNote("Starting preview…");
    try {
      const probe = await fetch("/?emu=1", { cache: "no-store" });
      const type = probe.headers.get("content-type") || "";
      if (!probe.ok || !type.includes("text/html")) {
        this.setNote("The device app isn't built yet. Run npm run build, then restart the bridge.");
        return;
      }
      const { token } = await api.adminPreviewToken();
      this.frame.src = "/?emu=1#token=" + encodeURIComponent(token);
      this.setNote(null);
    } catch (e) {
      const detail = e instanceof Error ? ": " + e.message : "";
      this.setNote("Preview unavailable" + detail + ". Press Restart to try again.");
    }
  }

  restart(): void {
    if (this.api) void this.start(this.api);
  }

  /** Tell the device to re-bootstrap its config after a successful save. */
  reloadConfig(): void {
    const win = this.frame.contentWindow;
    if (!win || !this.frame.getAttribute("src")) return;
    try {
      win.postMessage({ type: "r1:reload-config" }, location.origin);
    } catch {
      /* frame not ready */
    }
  }

  private setNote(text: string | null): void {
    this.note.hidden = !text;
    this.note.textContent = text ?? "";
  }

  private onLoad(): void {
    const win = this.frame.contentWindow;
    if (!win) return;
    try {
      win.addEventListener("wheel", (e: WheelEvent) => this.onWheel(e), { passive: false });
    } catch {
      /* not same origin */
    }
  }

  private dispatch(name: string): void {
    const win = this.frame.contentWindow;
    if (!win) return;
    try {
      win.dispatchEvent(new Event(name));
    } catch {
      /* frame not ready */
    }
  }

  private onWheel(e: WheelEvent): void {
    e.preventDefault();
    if (Math.abs(e.deltaY) < 1) return;
    const now = Date.now();
    if (now - this.wheelAt < WHEEL_GAP_MS) return;
    this.wheelAt = now;
    this.dispatch(e.deltaY > 0 ? "scrollDown" : "scrollUp");
  }

  private sidePointerDown(e: PointerEvent): void {
    if (e.button !== 0) return;
    try {
      (e.currentTarget as HTMLElement | null)?.setPointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    this.holdStart("pointer");
  }

  private holdStart(src: "pointer" | "key"): void {
    if (this.holdSource) return;
    this.holdSource = src;
    this.longPressed = false;
    window.clearTimeout(this.holdTimer);
    this.holdTimer = window.setTimeout(() => {
      this.holdTimer = 0;
      this.longPressed = true;
      this.dispatch("longPressStart");
    }, HOLD_MS);
  }

  private holdEnd(src: "pointer" | "key", cancelled = false): void {
    if (this.holdSource !== src) return;
    this.holdSource = null;
    if (this.holdTimer) {
      window.clearTimeout(this.holdTimer);
      this.holdTimer = 0;
    }
    if (this.longPressed) {
      this.longPressed = false;
      this.dispatch("longPressEnd");
    } else if (!cancelled) {
      this.dispatch("sideClick");
    }
  }

  private onKeyDown(e: KeyboardEvent): void {
    if (isFormField(e.target)) return;
    const t = e.target;
    const onButton = t instanceof HTMLButtonElement || t instanceof HTMLAnchorElement;
    switch (e.key) {
      case "ArrowUp":
        e.preventDefault();
        this.dispatch("scrollUp");
        break;
      case "ArrowDown":
        e.preventDefault();
        this.dispatch("scrollDown");
        break;
      case "Enter":
        // Buttons already fire their own click on Enter.
        if (onButton) return;
        e.preventDefault();
        this.dispatch("sideClick");
        break;
      case " ":
        // Space on a Studio button outside the emulator keeps its normal behavior.
        if (onButton && !inEmulator(t)) return;
        e.preventDefault();
        if (!e.repeat) this.holdStart("key");
        break;
      default:
        break;
    }
  }

  private onKeyUp(e: KeyboardEvent): void {
    if (e.key !== " " || this.holdSource !== "key") return;
    e.preventDefault();
    this.holdEnd("key");
  }
}
