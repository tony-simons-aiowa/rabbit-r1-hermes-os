// Diagnostics: runs the capability probes from SPEC section 6 and prints one line each.
import { h } from "../ui";
import type { AppEvent } from "../platform";
import type { Screen } from "../nav";
import { Api } from "../../../shared/api";
import { state } from "../state";
import { storage } from "../platform";
import { probeVoice, probeMic, playableAudio, pickVoiceMode } from "../voice";
import { screenEl } from "./base";

type Line = { ok: boolean | null; text: string };

export function createDiagnostics(): Screen {
  const el = screenEl("diag", "Diagnostics");
  const box = h("div", { class: "diag" });
  el.appendChild(box);
  const w = window as unknown as Record<string, unknown>;

  const print = (l: Line): void => {
    const mark = l.ok === null ? "•" : l.ok ? "✓" : "✗";
    box.appendChild(h("div", { class: "dline " + (l.ok === false ? "bad" : l.ok ? "good" : "") }, mark + " " + l.text));
  };

  const run = async (): Promise<void> => {
    const v = probeVoice();
    print({ ok: v.native, text: "Native voice bridge: " + (v.native ? "present" : "absent") });

    const probeKey = "r1.diag";
    let storeOk = false;
    try {
      await storage.setPlain(probeKey, "ok");
      storeOk = (await storage.getPlain(probeKey)) === "ok";
      await storage.remove(probeKey);
    } catch (e) {
      storeOk = false;
    }
    print({ ok: storeOk, text: "Storage read/write (creationStorage " + (w.creationStorage ? "yes" : "no") + ")" });

    print({ ok: null, text: "Mic: asking…" });
    const mic = await probeMic();
    box.lastChild && box.removeChild(box.lastChild);
    print({ ok: mic, text: "Microphone (getUserMedia)" });

    print({ ok: v.mediaRecorder, text: "MediaRecorder" + (v.mediaRecorder ? "" : " missing") });
    print({ ok: v.mimes.length > 0, text: "Recorder mime: " + (v.mimes.join(", ") || "none") });
    print({ ok: playableAudio(), text: "Audio playback (mp3/webm)" });
    print({ ok: pickVoiceMode(state.config.voice.input, state.voice.stt) !== null, text: "Voice mode: " + (pickVoiceMode(state.config.voice.input, state.voice.stt) || "none") });

    const t0 = Date.now();
    try {
      const api = new Api({ token: "", timeoutMs: 8000 });
      await api.health();
      print({ ok: true, text: "Bridge round trip: " + (Date.now() - t0) + " ms" });
    } catch (e) {
      print({ ok: false, text: "Bridge round trip failed" });
    }

    print({ ok: null, text: "UA: " + navigator.userAgent });
    print({ ok: null, text: "Screen: " + window.innerWidth + "×" + window.innerHeight + " · dpr " + window.devicePixelRatio });
  };

  void run();

  return {
    el,
    onInput(ev: AppEvent): boolean {
      if (ev === "up") {
        box.scrollTop -= 48;
        return true;
      }
      if (ev === "down") {
        box.scrollTop += 48;
        return true;
      }
      return false;
    },
  };
}
