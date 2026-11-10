// Theme presets, echoing Hermes Desktop's built-in themes (MIT, Nous Research).
import type { DeviceConfig } from "./protocol";

export const TOKENS = ["bg", "surface", "border", "fg", "muted", "primary", "accent", "ok", "warn", "err"] as const;
export type Token = (typeof TOKENS)[number];
export type Theme = Record<Token, string>;

const STATUS = { ok: "#55a583", warn: "#f59e0b", err: "#f85149" };

function t(bg: string, surface: string, border: string, fg: string, muted: string, primary: string, accent: string): Theme {
  return { bg, surface, border, fg, muted, primary, accent, ...STATUS };
}

export const THEMES: Record<string, Theme> = {
  nous: t("#0d1117", "#161b22", "#30363d", "#e6edf3", "#8b949e", "#4a84fe", "#17243a"),
  "nous-light": t("#ffffff", "#f6f8fa", "#d0d7de", "#1f2328", "#656d76", "#0053fd", "#e3edff"),
  github: t("#0d1117", "#161b22", "#30363d", "#e6edf3", "#8b949e", "#4f9e5e", "#192a24"),
  catppuccin: t("#1e1e2e", "#262637", "#585b70", "#cdd6f4", "#9399b2", "#cba6f7", "#3d3652"),
  everforest: t("#2d353b", "#343f44", "#4f585e", "#d3c6aa", "#9da9a0", "#a7c080", "#434e47"),
  solarized: t("#002b36", "#073642", "#234751", "#839496", "#657b83", "#6ea1c4", "#144050"),
  midnight: t("#08081c", "#101030", "#1e1e52", "#ddd6ff", "#8f88c0", "#8b80e8", "#1a1a44"),
  ember: t("#160800", "#221004", "#3a1c08", "#ffd8b0", "#b08860", "#d97316", "#301600"),
  mono: t("#0e0e0e", "#181818", "#2a2a2a", "#eaeaea", "#8a8a8a", "#9a9a9a", "#222222"),
  slate: t("#0d1117", "#151b23", "#30363d", "#c9d1d9", "#8b949e", "#58a6ff", "#1e2530"),
  cyberpunk: t("#000a00", "#001400", "#003000", "#00ff41", "#00a82b", "#00ff41", "#002000"),
  classic: t("#1a1a2e", "#22223a", "#cd7f32", "#fff8dc", "#b8a888", "#ffbf00", "#2e2a1c"),
  rabbit: t("#000000", "#111111", "#2a2a2a", "#ffffff", "#8a8a8a", "#fe5000", "#2a1205"),
};

export const THEME_NAMES = Object.keys(THEMES);

/** Parse #rgb / #rrggbb / rgb() / hsl() into [r,g,b] 0-255. Returns null if unknown. */
export function parseColor(input: string): [number, number, number] | null {
  const s = input.trim().toLowerCase();
  let m = /^#([0-9a-f]{3})$/.exec(s);
  if (m) {
    const h = m[1];
    return [parseInt(h[0] + h[0], 16), parseInt(h[1] + h[1], 16), parseInt(h[2] + h[2], 16)];
  }
  m = /^#([0-9a-f]{6})/.exec(s);
  if (m) {
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  m = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/.exec(s);
  if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
  m = /^hsla?\(\s*([\d.]+)(?:deg)?[\s,]+([\d.]+)%[\s,]+([\d.]+)%/.exec(s);
  if (m) {
    const h = (((Number(m[1]) % 360) + 360) % 360) / 360;
    const sat = Number(m[2]) / 100;
    const l = Number(m[3]) / 100;
    if (sat === 0) {
      const v = Math.round(l * 255);
      return [v, v, v];
    }
    const q = l < 0.5 ? l * (1 + sat) : l + sat - l * sat;
    const p = 2 * l - q;
    const hue = (x: number): number => {
      let k = x;
      if (k < 0) k += 1;
      if (k > 1) k -= 1;
      if (k < 1 / 6) return p + (q - p) * 6 * k;
      if (k < 1 / 2) return q;
      if (k < 2 / 3) return p + (q - p) * (2 / 3 - k) * 6;
      return p;
    };
    return [Math.round(hue(h + 1 / 3) * 255), Math.round(hue(h) * 255), Math.round(hue(h - 1 / 3) * 255)];
  }
  return null;
}

/** Perceived luminance 0-255. Unknown colors count as mid-dark. */
export function luminance(color: string): number {
  const rgb = parseColor(color);
  if (!rgb) return 100;
  return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2];
}

export function onColor(color: string): string {
  return luminance(color) > 140 ? "#000000" : "#ffffff";
}

/** Preset + overrides → a complete theme. Unknown presets fall back to nous. */
export function resolveTheme(cfg: DeviceConfig["theme"]): Theme {
  const base = THEMES[cfg.preset] || THEMES.nous;
  const out: Theme = { ...base };
  for (const key of TOKENS) {
    const v = cfg.overrides ? cfg.overrides[key] : undefined;
    if (typeof v === "string" && v.trim() && parseColor(v)) out[key] = v.trim();
  }
  return out;
}

/** Write the theme as CSS custom properties (--bg, --fg, … plus --on-primary). */
export function applyTheme(el: HTMLElement, theme: Theme): void {
  for (const key of TOKENS) el.style.setProperty("--" + key, theme[key]);
  el.style.setProperty("--on-primary", onColor(theme.primary));
}
