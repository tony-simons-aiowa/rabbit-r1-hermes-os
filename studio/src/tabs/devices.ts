// Devices tab: public HTTPS address, pairing (QR + code), paired devices, test notification.
import type { PairInfo } from "../../../shared/protocol";
import { button, card, errText, focusKey, fmtDate, h, relTime, select, setValue, toast, type Tab } from "../dom";
import { drawQr } from "../qr";
import { getAdmin, getApi, onChange, patchAdmin } from "../state";

export function isHttps(url: string): boolean {
  return /^https:\/\/[^\s/]+/i.test(url.trim());
}

async function copyText(text: string, btn: HTMLButtonElement): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = "Copied ✓";
    window.setTimeout(() => (btn.textContent = "Copy"), 1500);
  } catch {
    toast("Copy failed. Select the link and copy it by hand.", "error");
  }
}

export function mountDevices(): Tab {
  let pair: PairInfo | null = null;
  let pairBusy = false;
  let confirmId = "";
  let pendingFocus = "";
  let countdown: HTMLElement | null = null;
  let devSig = "";
  const seenCells = new Map<string, HTMLElement>();

  // ---- public address ----
  const urlInput = h("input", {
    type: "url",
    placeholder: "https://your-bridge.example.ts.net",
    autocomplete: "off",
    spellcheck: false,
    "aria-label": "Public HTTPS address",
  });
  const urlWarn = h("div", { class: "callout warn", role: "status" });
  const urlStatus = h("span", { class: "status-slot muted", role: "status" });

  async function saveUrl(): Promise<void> {
    urlStatus.textContent = "Saving…";
    try {
      const res = await getApi().adminSetPublicUrl(urlInput.value.trim());
      patchAdmin((a) => {
        a.public_url = res.public_url;
      });
      urlStatus.textContent = "Saved ✓";
    } catch (e) {
      urlStatus.textContent = "";
      toast(errText(e), "error");
    }
  }

  const urlForm = h(
    "form",
    {
      class: "inline-form",
      onsubmit: (e: Event) => {
        e.preventDefault();
        void saveUrl();
      },
    },
    h("label", { class: "field grow" }, h("span", { class: "field-label" }, "Address"), urlInput),
    h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn primary" }, "Save"), urlStatus),
  );

  const urlCard = card(
    "Public HTTPS address",
    h(
      "p",
      { class: "muted" },
      "The R1 can only load HTTPS pages, so the bridge needs a public HTTPS address. The easiest options:",
    ),
    h(
      "ul",
      { class: "bullets" },
      h(
        "li",
        null,
        h("code", null, "hermes r1 serve --tunnel tailscale"),
        " gives a stable address and is recommended.",
      ),
      h(
        "li",
        null,
        h("code", null, "hermes r1 serve --tunnel cloudflared"),
        " gives a temporary address that changes every restart, so the R1 app must be re-added each time.",
      ),
    ),
    urlWarn,
    urlForm,
  );

  // ---- pairing ----
  const pairBtn = button("Pair a device", () => void doPair(), { variant: "primary" });
  const pairNote = h(
    "div",
    { class: "callout warn" },
    "Pairing needs a public HTTPS address. Set one above first.",
  );
  const pairBox = h("div", { class: "pair-box" });

  async function doPair(): Promise<void> {
    if (pairBusy) return;
    pairBusy = true;
    sync();
    let next: PairInfo | null = null;
    try {
      next = await getApi().adminPair();
    } catch (e) {
      toast(errText(e), "error");
    }
    pairBusy = false;
    if (next) {
      pair = next;
      renderPair();
    }
    sync();
  }

  function tick(): void {
    const p = pair;
    const cd = countdown;
    if (!p || !cd) return;
    const left = Math.floor(p.expires - Date.now() / 1000);
    cd.classList.toggle("expired", left <= 0);
    cd.textContent =
      left <= 0
        ? "Expired. Make a new code."
        : `Expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  }

  function renderPair(): void {
    pairBox.replaceChildren();
    countdown = null;
    const p = pair;
    if (!p) {
      pairBox.append(h("p", { class: "muted" }, "Create a code, then scan the QR code on your R1 to pair it."));
      return;
    }
    const canvas = h("canvas", { class: "qr", role: "img", "aria-label": "Pairing QR code" });
    const drawn = drawQr(canvas, JSON.stringify(p.qr));
    const link = h("input", {
      type: "text",
      readonly: true,
      value: p.url,
      class: "mono",
      "aria-label": "Pairing link",
      onfocus: (e: Event) => (e.target as HTMLInputElement).select(),
    });
    const copy = button("Copy", () => void copyText(link.value, copy), { variant: "ghost" });
    countdown = h("span", { class: "countdown" });
    pairBox.append(
      h(
        "div",
        { class: "pair-grid" },
        drawn
          ? canvas
          : h("p", { class: "callout err" }, "This code is too long to draw. Copy the link instead."),
        h(
          "div",
          { class: "pair-info stack" },
          h("span", { class: "muted small" }, "Pairing code"),
          h("span", { class: "pair-code mono" }, p.code),
          countdown,
          h(
            "ol",
            { class: "steps" },
            h("li", null, "On your R1, open the camera / QR scanner for creations."),
            h("li", null, "Scan this code."),
            h("li", null, "Open Hermes when the R1 asks."),
          ),
          h("div", { class: "link-row" }, link, copy),
          button("New code", () => void doPair(), { variant: "ghost" }),
        ),
      ),
    );
    tick();
  }

  const pairCard = card(
    "Pair a device",
    h("p", { class: "muted" }, "Shows a QR code and a short code for the R1's camera / QR scanner."),
    pairNote,
    h("div", { class: "row" }, pairBtn),
    pairBox,
  );

  // ---- paired devices ----
  const devicesBox = h("div", { class: "table-wrap" });

  async function removeDevice(id: string): Promise<void> {
    try {
      await getApi().adminRemoveDevice(id);
      confirmId = "";
      patchAdmin((a) => {
        a.devices = a.devices.filter((d) => d.id !== id);
      });
      toast("Device removed.", "ok");
    } catch (e) {
      toast(errText(e), "error");
    }
    sync();
  }

  function renderDevices(): void {
    const a = getAdmin();
    seenCells.clear();
    if (a.devices.length === 0) {
      devicesBox.replaceChildren(h("p", { class: "muted" }, "No devices paired yet. Pair one above."));
    } else {
      const rows = a.devices.map((d) => {
        const seen = h("td", { class: "muted" }, relTime(d.last_seen));
        seenCells.set(d.id, seen);
        const action =
          confirmId === d.id
            ? h(
                "span",
                { class: "confirm-row" },
                "Remove this device?",
                button("Yes, remove", () => void removeDevice(d.id), {
                  variant: "danger",
                  attrs: { "data-fk": "confirm-" + d.id },
                }),
                button(
                  "Cancel",
                  () => {
                    confirmId = "";
                    sync();
                  },
                  { variant: "ghost" },
                ),
              )
            : button(
                "Remove",
                () => {
                  confirmId = d.id;
                  pendingFocus = "confirm-" + d.id;
                  sync();
                },
                { variant: "ghost", attrs: { "data-fk": "remove-" + d.id } },
              );
        return h(
          "tr",
          null,
          h("td", null, d.name || "Unnamed device"),
          h("td", { class: "muted" }, fmtDate(d.created)),
          seen,
          h("td", { class: "right" }, action),
        );
      });
      devicesBox.replaceChildren(
        h(
          "table",
          { class: "table" },
          h(
            "thead",
            null,
            h(
              "tr",
              null,
              h("th", null, "Name"),
              h("th", null, "Paired"),
              h("th", null, "Last seen"),
              h("th", { class: "right" }, "Actions"),
            ),
          ),
          h("tbody", null, rows),
        ),
      );
    }
    if (pendingFocus) {
      focusKey(devicesBox, pendingFocus);
      pendingFocus = "";
    }
  }

  const devicesCard = card("Paired devices", devicesBox);

  // ---- test notification ----
  const testTitle = h("input", { type: "text", maxlength: 80, placeholder: "e.g. Build finished", "aria-label": "Notification title" });
  const testBody = h("textarea", { rows: 2, maxlength: 400, placeholder: "Optional message", "aria-label": "Notification message" });
  const testLevel = select({
    label: "Level",
    options: [
      { value: "info", label: "Info" },
      { value: "success", label: "Success" },
      { value: "warn", label: "Warning" },
    ],
    value: "info",
    onChange: () => undefined,
  });

  async function sendTest(): Promise<void> {
    const title = testTitle.value.trim();
    if (!title) {
      testTitle.focus();
      toast("Give the notification a title.", "error");
      return;
    }
    const a = getAdmin();
    const bot = a.bots.find((b) => b.is_default)?.id ?? "default";
    const level = testLevel.value === "success" ? "success" : testLevel.value === "warn" ? "warn" : "info";
    try {
      await getApi().adminTestInbox({ title, body: testBody.value.trim(), bot, level });
      toast("Test notification sent to the device inbox.", "ok");
    } catch (e) {
      toast(errText(e), "error");
    }
  }

  const testCard = card(
    "Send a test notification",
    h("p", { class: "muted" }, "Adds an item to the R1 inbox."),
    h(
      "form",
      {
        class: "stack",
        onsubmit: (e: Event) => {
          e.preventDefault();
          void sendTest();
        },
      },
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Title"), testTitle),
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Message"), testBody),
      h(
        "div",
        { class: "form-actions" },
        h("label", { class: "field inline" }, h("span", { class: "field-label" }, "Level"), testLevel),
        h("button", { type: "submit", class: "btn" }, "Send"),
      ),
    ),
  );

  // ---- sync ----
  function syncDevices(): void {
    const a = getAdmin();
    const sig = confirmId + "|" + a.devices.map((d) => d.id + ":" + d.name).join(",");
    if (sig !== devSig) {
      devSig = sig;
      renderDevices();
      return;
    }
    for (const d of a.devices) {
      const cell = seenCells.get(d.id);
      if (cell) cell.textContent = relTime(d.last_seen);
    }
  }

  function sync(): void {
    const a = getAdmin();
    setValue(urlInput, a.public_url);
    const https = isHttps(a.public_url);
    urlWarn.hidden = https;
    urlWarn.textContent = a.public_url.trim()
      ? "That address isn't HTTPS, so the R1 can't load it."
      : "No public address yet. Set an HTTPS address before pairing.";
    pairNote.hidden = https;
    pairBtn.disabled = !https || pairBusy;
    syncDevices();
  }

  const el = h("div", { class: "stack-lg" }, urlCard, pairCard, devicesCard, testCard);
  onChange(sync);
  renderPair();
  sync();
  return { el, sync };
}
