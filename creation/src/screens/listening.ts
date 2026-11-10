// Listening overlay: big avatar, Listening… → Transcribing… → sends the text to the bot's chat.
import { ApiError } from "../../../shared/api";
import { h } from "../ui";
import { push, pop, current, type Screen } from "../nav";
import type { AppEvent } from "../platform";
import { state, botById } from "../state";
import { toast } from "../chrome";
import { renderAvatar, setAvatarLevel } from "../../../shared/avatar";
import { pickVoiceMode, startRecording, type Recording } from "../voice";
import { guard, sendTo } from "../app";

const MIN_HOLD_MS = 300;

/** True when some voice input path is usable for the current config. */
export function canTalk(): boolean {
  return pickVoiceMode(state.config.voice.input, state.voice.stt) !== null;
}

export function openListening(bot: string, mode: "hold" | "tap"): void {
  const vm = pickVoiceMode(state.config.voice.input, state.voice.stt);
  if (!vm) {
    toast("Voice not available — use Type");
    return;
  }
  const b = botById(bot);
  const el = h("div", { class: "scr listen" });
  const avatar = renderAvatar(
    b ? b.avatar : { kind: "shape", shape: "circle", color: "", url: "" },
    { botId: bot, size: 96, listening: true, animate: true },
  );
  const label = h("div", { class: "listen-label" }, "Listening…");
  const hint = h("div", { class: "listen-hint" }, mode === "hold" ? "release to send" : "click to send");
  el.append(h("div", { class: "listen-av" }, avatar), label, hint);

  const startedAt = Date.now();
  let rec: Recording | null = null;
  let phase: "rec" | "busy" | "done" = "rec";
  let released = false;
  let closedEarly = false;

  const close = (): void => {
    phase = "done";
    if (current() === screen) pop();
  };

  const finish = (): void => {
    if (phase !== "rec") return;
    if (!rec) {
      released = true;
      return;
    }
    const held = Date.now() - startedAt;
    if (mode === "hold" && held < MIN_HOLD_MS) {
      rec.cancel();
      rec = null;
      close();
      return;
    }
    phase = "busy";
    label.textContent = "Transcribing…";
    hint.textContent = "";
    const r = rec;
    r.stop()
      .then(async (res) => {
        let text = "";
        if (typeof res === "string") text = res;
        else if (res) text = (await guard(state.api!.transcribe(res))).text;
        const t = (text || "").trim();
        close();
        if (t) sendTo(bot, t);
        else toast("Didn't catch that");
      })
      .catch((e) => {
        close();
        if (e instanceof ApiError && e.code === "stt_unavailable") toast("Transcription unavailable — use Type");
        else toast("Voice failed — use Type");
      });
  };

  const screen: Screen = {
    el,
    onShow() {
      /* recording starts in the constructor below */
    },
    onInput(ev: AppEvent): boolean {
      if (ev === "holdEnd") {
        if (mode === "hold") finish();
        return true;
      }
      if (ev === "click") {
        finish();
        return true;
      }
      if (ev === "holdStart" || ev === "up" || ev === "down") return true;
      return false; // back: nav pops and destroy() cancels the recording
    },
    destroy() {
      if (phase === "rec") {
        closedEarly = true;
        if (rec) rec.cancel();
        phase = "done";
      }
    },
  };

  push(screen);

  startRecording(vm)
    .then((r) => {
      if (closedEarly || phase !== "rec") {
        r.cancel();
        return;
      }
      rec = r;
      r.onLevel((l) => setAvatarLevel(avatar, l));
      if (released) finish();
    })
    .catch(() => {
      toast("Microphone blocked");
      close();
    });
}
