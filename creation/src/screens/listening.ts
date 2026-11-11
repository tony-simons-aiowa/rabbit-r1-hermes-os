// Listening overlay shared by direct and group chats.
import { ApiError } from "../../../shared/api";
import { h } from "../ui";
import { push, pop, current, type Screen } from "../nav";
import type { AppEvent } from "../platform";
import { state, botById } from "../state";
import { toast } from "../chrome";
import { renderAvatar, setAvatarLevel } from "../../../shared/avatar";
import { pickVoiceMode, startRecording, type Recording } from "../voice";
import { guard } from "../app";

const MIN_HOLD_MS = 300;

/** True when some voice input path is usable for the current config. */
export function canTalk(): boolean {
  return pickVoiceMode(state.config.voice.input, state.voice.stt) !== null;
}

export function openListening(opts: {
  mode: "hold" | "tap";
  bot?: string;
  group?: string;
  onTranscript: (text: string) => void;
}): void {
  const vm = pickVoiceMode(state.config.voice.input, state.voice.stt);
  if (!vm) {
    toast("Voice not available — use Type");
    return;
  }
  const { bot, group, mode, onTranscript } = opts;
  const b = bot ? botById(bot) : undefined;
  const el = h("div", { class: "scr listen" });
  const avatar = renderAvatar(
    b ? b.avatar : { kind: "shape", shape: "circle", color: "", url: "" },
    { botId: bot ?? "group", size: 96, listening: true, animate: true },
  );
  const label = h("div", { class: "listen-label" }, "Listening…");
  const hint = h("div", { class: "listen-hint" }, mode === "hold" ? "release to send" : "click to send");
  el.append(h("div", { class: "listen-av" }, avatar), label);
  if (group) el.append(h("div", { class: "listen-hint" }, group));
  el.append(hint);

  const startedAt = Date.now();
  let rec: Recording | null = null;
  let phase: "rec" | "busy" | "done" = "rec";
  let releasedAt: number | undefined;
  let cancelled = false;

  const close = (): void => {
    phase = "done";
    if (current() === screen) pop();
  };

  const finish = (): void => {
    if (phase !== "rec") return;
    releasedAt ??= Date.now();
    if (!rec) {
      return;
    }
    const held = releasedAt - startedAt;
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
    const api = state.api;
    r.stop()
      .then(async (res) => {
        if (cancelled) return;
        let text = "";
        if (typeof res === "string") text = res;
        else if (res && api) text = (await guard(api.transcribe(res))).text;
        if (cancelled) return;
        const t = (text || "").trim();
        close();
        if (t) onTranscript(t);
        else toast("Didn't catch that");
      })
      .catch((e) => {
        if (cancelled) return;
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
      if (phase === "done") return;
      cancelled = true;
      if (phase === "rec") rec?.cancel();
      phase = "done";
    },
  };

  push(screen);

  startRecording(vm)
    .then((r) => {
      if (phase !== "rec") {
        r.cancel();
        return;
      }
      rec = r;
      r.onLevel((l) => setAvatarLevel(avatar, l));
      if (releasedAt !== undefined) finish();
    })
    .catch(() => {
      if (cancelled) return;
      toast("Microphone blocked");
      close();
    });
}
