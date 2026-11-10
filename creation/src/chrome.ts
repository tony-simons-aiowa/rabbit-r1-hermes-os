// Persistent chrome: 18px status bar, one reusable toast, and the 16px offline banner.
import { AVATAR_CSS, PET_CSS } from "../../shared/avatar";
import { h } from "./ui";
import { state, subscribe } from "./state";

let clockEl: HTMLElement;
let connEl: HTMLElement;
let botEl: HTMLElement;
let bannerEl: HTMLElement;
let toastEl: HTMLElement;
let toastTimer = 0;
let clockTimer = 0;
let online = true;

function injectCss(id: string, css: string): void {
  if (document.getElementById(id)) return;
  const s = document.createElement("style");
  s.id = id;
  s.textContent = css;
  document.head.appendChild(s);
}

/** Build the chrome around the screen container. */
export function initChrome(app: HTMLElement): HTMLElement {
  injectCss("av-css", AVATAR_CSS);
  injectCss("pet-css", PET_CSS);
  const screens = h("div", { class: "screens", id: "screens" });
  const bar = h(
    "div",
    { class: "sbar" },
    (clockEl = h("span", { class: "clock" })),
    h("span", { class: "sbar-r" }, (connEl = h("span", { class: "conn" })), (botEl = h("span", { class: "bname" }))),
  );
  bannerEl = h("div", { class: "banner" }, "Can't reach the bridge. Retrying…");
  toastEl = h("div", { class: "toast", role: "status" });
  app.appendChild(bar);
  app.appendChild(screens);
  app.appendChild(bannerEl);
  app.appendChild(toastEl);
  subscribe(refreshChrome);
  clockTimer = window.setInterval(updateClock, 15000) as unknown as number;
  refreshChrome();
  return screens;
}

function updateClock(): void {
  if (!state.config.home.clock) {
    clockEl.textContent = "";
    return;
  }
  const d = new Date();
  const hh = d.getHours();
  const mm = d.getMinutes();
  clockEl.textContent = (hh < 10 ? "0" : "") + hh + ":" + (mm < 10 ? "0" : "") + mm;
}

export function refreshChrome(): void {
  updateClock();
  const bot = state.bots.find((b) => b.id === state.currentBot);
  botEl.textContent = bot ? bot.name : "";
  connEl.classList.toggle("ok", online);
  connEl.classList.toggle("bad", !online);
}

export function toast(msg: string): void {
  toastEl.textContent = msg;
  toastEl.classList.add("on");
  if (toastTimer) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toastEl.classList.remove("on");
    toastTimer = 0;
  }, 2000);
}

/** Show or hide the offline banner. Calls are cheap; no-op when unchanged. */
export function setOnline(ok: boolean): void {
  if (ok === online) return;
  online = ok;
  bannerEl.classList.toggle("on", !ok);
  refreshChrome();
}

export function isOnline(): boolean {
  return online;
}

export function destroyChrome(): void {
  window.clearInterval(clockTimer);
}
