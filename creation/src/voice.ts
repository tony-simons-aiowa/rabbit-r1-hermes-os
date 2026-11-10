// Voice input and output, per SPEC section 6. Everything is feature-detected.
import { hasNativeVoiceBridge } from "./platform";

export type VoiceMode = "mediarecorder" | "native";
export type Recording = {
  /** Resolves a Blob (MediaRecorder) or a transcript string (native STT), or null when nothing was captured. */
  stop(): Promise<Blob | string | null>;
  cancel(): void;
  onLevel(cb: (level: number) => void): void;
  mode: VoiceMode;
};

const NATIVE_TIMEOUT_MS = 8000;

export function canRecord(): boolean {
  const md = typeof navigator !== "undefined" ? navigator.mediaDevices : undefined;
  return !!(md && typeof md.getUserMedia === "function" && typeof MediaRecorder !== "undefined");
}

export type VoiceProbe = {
  mic: boolean;
  mediaRecorder: boolean;
  mimes: string[];
  native: boolean;
  audio: boolean;
};

const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4", "audio/aac"];

export function pickMime(): string {
  if (typeof MediaRecorder === "undefined" || typeof MediaRecorder.isTypeSupported !== "function") return "";
  for (const m of MIME_CANDIDATES) if (MediaRecorder.isTypeSupported(m)) return m;
  return "";
}

/** Synchronous capability probe. Use probeMic() for an active check. */
export function probeVoice(): VoiceProbe {
  const mimes = typeof MediaRecorder !== "undefined" && typeof MediaRecorder.isTypeSupported === "function"
    ? MIME_CANDIDATES.filter((m) => MediaRecorder.isTypeSupported(m))
    : [];
  return {
    mic: canRecord(),
    mediaRecorder: typeof MediaRecorder !== "undefined",
    mimes,
    native: hasNativeVoiceBridge(),
    audio: typeof Audio !== "undefined",
  };
}

/** Active microphone probe: asks for the mic, then releases it. */
export async function probeMic(): Promise<boolean> {
  if (!canRecord()) return false;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    return true;
  } catch (e) {
    return false;
  }
}

/** Which input path to use for config.voice.input given server STT availability. */
export function pickVoiceMode(input: string, serverStt: boolean): VoiceMode | null {
  if (input === "off") return null;
  const mr = canRecord() && serverStt;
  const native = hasNativeVoiceBridge();
  if (input === "mediarecorder") return mr ? "mediarecorder" : null;
  if (input === "native") return native ? "native" : null;
  if (mr) return "mediarecorder";
  if (native) return "native";
  return null;
}

/** Start recording with the given mode. Throws when the mic is denied or unavailable. */
export async function startRecording(mode: VoiceMode): Promise<Recording> {
  return mode === "native" ? startNative() : startMedia();
}

async function startMedia(): Promise<Recording> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
  const mime = pickMime();
  const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  const chunks: Blob[] = [];
  rec.ondataavailable = (e: BlobEvent) => {
    if (e.data && e.data.size) chunks.push(e.data);
  };
  rec.start(250);

  let ctx: AudioContext | null = null;
  let raf = 0;
  let levelCb: ((l: number) => void) | null = null;
  const teardown = (): void => {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    stream.getTracks().forEach((t) => t.stop());
    if (ctx) {
      ctx.close().catch(() => undefined);
      ctx = null;
    }
  };

  // Level meter (optional).
  try {
    const AC = (window as unknown as { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext }).AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (AC) {
      const c = new AC();
      ctx = c;
      const src = c.createMediaStreamSource(stream);
      const an = c.createAnalyser();
      an.fftSize = 256;
      src.connect(an);
      const buf = new Uint8Array(an.fftSize);
      const tick = (): void => {
        an.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length);
        if (levelCb) levelCb(Math.min(1, rms * 3));
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    }
  } catch (e) {
    ctx = null;
  }

  return {
    mode: "mediarecorder",
    onLevel(cb) {
      levelCb = cb;
    },
    cancel() {
      try {
        rec.onstop = null;
        if (rec.state !== "inactive") rec.stop();
      } catch (e) {
        /* ignore */
      }
      teardown();
    },
    stop() {
      return new Promise<Blob | null>((resolve) => {
        const finish = (): void => {
          teardown();
          const type = rec.mimeType || mime || "audio/webm";
          const blob = chunks.length ? new Blob(chunks, { type }) : null;
          resolve(blob && blob.size > 0 ? blob : null);
        };
        if (rec.state === "inactive") {
          finish();
          return;
        }
        rec.onstop = finish;
        try {
          rec.stop();
        } catch (e) {
          finish();
        }
      });
    },
  };
}

type NativeHandler = { postMessage(msg: string): void };

function nativeHandler(): NativeHandler | null {
  const h = (window as unknown as { CreationVoiceHandler?: NativeHandler }).CreationVoiceHandler;
  return h && typeof h.postMessage === "function" ? h : null;
}

function parseNativeData(data: unknown): { type?: string; transcript?: string } | null {
  let d: any = data;
  if (typeof d === "string") {
    try {
      d = JSON.parse(d);
    } catch (e) {
      return null;
    }
  }
  if (!d || typeof d !== "object") return null;
  const inner = typeof d.data === "string" ? safeJson(d.data) : d.data;
  const type = d.type || (inner && inner.type);
  const transcript =
    d.transcript ?? d.message ?? (inner && (inner.transcript ?? inner.message)) ?? "";
  return { type, transcript: typeof transcript === "string" ? transcript : "" };
}

function safeJson(s: string): any {
  try {
    return JSON.parse(s);
  } catch (e) {
    return null;
  }
}

async function startNative(): Promise<Recording> {
  const h = nativeHandler();
  if (!h) throw new Error("native voice unavailable");

  let transcript: string | null = null;
  let waiter: ((t: string) => void) | null = null;
  const w = window as unknown as { onPluginMessage?: (d: unknown) => void };
  const prev = w.onPluginMessage;
  const restore = (): void => {
    if (w.onPluginMessage === listener) w.onPluginMessage = prev;
  };
  const listener = (data: unknown): void => {
    const parsed = parseNativeData(data);
    if (parsed && parsed.type === "sttEnded") {
      restore();
      if (waiter) {
        const f = waiter;
        waiter = null;
        f(parsed.transcript || "");
      } else {
        transcript = parsed.transcript || "";
      }
      return;
    }
    if (prev) prev(data);
  };
  w.onPluginMessage = listener;
  h.postMessage("start");

  return {
    mode: "native",
    onLevel() {
      /* native STT exposes no level */
    },
    cancel() {
      restore();
      waiter = null;
      try {
        h.postMessage("stop");
      } catch (e) {
        /* ignore */
      }
    },
    stop() {
      try {
        h.postMessage("stop");
      } catch (e) {
        /* ignore */
      }
      return new Promise<string | null>((resolve) => {
        if (transcript !== null) {
          const t = transcript;
          restore();
          resolve(t || null);
          return;
        }
        const timer = setTimeout(() => {
          restore();
          waiter = null;
          resolve(null);
        }, NATIVE_TIMEOUT_MS);
        waiter = (t: string) => {
          clearTimeout(timer);
          resolve(t || null);
        };
      });
    },
  };
}

let audioEl: HTMLAudioElement | null = null;

/** Play a (signed) audio URL with one reused element. Resolves false when playback is rejected. */
export function playAudio(url: string): Promise<boolean> {
  try {
    if (!audioEl) audioEl = new Audio();
    audioEl.src = url;
    const p = audioEl.play();
    if (!p || typeof p.then !== "function") return Promise.resolve(true);
    return p.then(
      () => true,
      () => false,
    );
  } catch (e) {
    return Promise.resolve(false);
  }
}

/** Whether the browser can decode the audio types the bridge produces (mp3 for TTS, webm/opus for recordings). */
export function playableAudio(): boolean {
  try {
    const a = new Audio();
    return a.canPlayType("audio/mpeg") !== "" || a.canPlayType('audio/webm; codecs="opus"') !== "";
  } catch (e) {
    return false;
  }
}

export function stopAudio(): void {
  if (!audioEl) return;
  try {
    audioEl.pause();
  } catch (e) {
    /* ignore */
  }
}
