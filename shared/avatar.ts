// Geometric "math face" avatars, after Hermes Desktop Bot Mode (MIT, Nous Research).
// Pure DOM/SVG + CSS classes; all motion is CSS transform/opacity so it stays cheap on the R1.
import type { Avatar, BotState } from "./protocol";
import { luminance } from "./themes";

export const SHAPES = ["circle", "squircle", "pill", "triangle", "hexagon", "cloud", "drop"] as const;
export type Shape = (typeof SHAPES)[number];

const BLOB_KIND_TO_SHAPE: Record<string, Shape> = {
  round: "circle",
  boxy: "squircle",
  capsule: "pill",
  triangle: "triangle",
  hexagon: "hexagon",
  cloud: "cloud",
  droplet: "drop",
  organic: "squircle",
  nub: "pill",
  sun: "circle",
};

const BODY: Record<Shape, string> = {
  circle: '<circle cx="20" cy="20" r="17.5"/>',
  squircle: '<rect x="3" y="3" width="34" height="34" rx="11"/>',
  pill: '<rect x="2" y="7" width="36" height="26" rx="13"/>',
  triangle: '<path d="M20 5.5 L36 33.5 L4 33.5 Z" stroke-linejoin="round"/>',
  hexagon: '<path d="M20 3.5 L34.5 11.75 L34.5 28.25 L20 36.5 L5.5 28.25 L5.5 11.75 Z"/>',
  cloud: '<path d="M11 32 a7.5 7.5 0 0 1 -1 -14.9 A9.5 9.5 0 0 1 29 12.5 A7 7 0 0 1 30 32 Z"/>',
  drop: '<path d="M20 3 C20 3 6 20 6 27 a14 13.5 0 0 0 28 0 C34 20 20 3 20 3 Z"/>',
};

const LOW_EYES: Record<Shape, boolean> = {
  circle: false,
  squircle: false,
  pill: false,
  hexagon: false,
  triangle: true,
  cloud: true,
  drop: true,
};

/** Hermes Desktop's name hash. */
export function nameHash(name: string): number {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h;
}

export function defaultColor(botId: string): string {
  if (botId === "default") return "#8b5cf6";
  return `hsl(${nameHash(botId) % 360} 68% 58%)`;
}

/** Map a stored shape string (legacy name or blobatar[:seed[:kind]]) to one of the 7 shapes. */
export function resolveShape(shape: string, botId: string): Shape {
  const s = (shape || "").trim();
  if ((SHAPES as readonly string[]).includes(s)) return s as Shape;
  if (s.startsWith("blobatar")) {
    const parts = s.split(":");
    const kind = parts[2];
    if (kind && BLOB_KIND_TO_SHAPE[kind]) return BLOB_KIND_TO_SHAPE[kind];
    return SHAPES[nameHash(parts[1] || botId) % SHAPES.length];
  }
  return SHAPES[nameHash(botId) % SHAPES.length];
}

export type AvatarOptions = {
  botId: string;
  size: number; // CSS px
  state?: BotState;
  listening?: boolean;
  /** Set false for dense lists of non-highlighted rows to avoid running many animations at once. */
  animate?: boolean;
};

/** SVG markup for a shape avatar. Colors are validated by the caller's CSS engine; no user text is injected. */
export function shapeSvg(shape: Shape, color: string, size: number): string {
  const eyeY = LOW_EYES[shape] ? 22 : 17.2;
  const eyeColor = luminance(color) < 110 ? "rgba(232,220,195,0.95)" : "rgba(0,0,0,0.85)";
  const eye = (x: number): string =>
    `<g class="av-eye" style="transform-origin:${x}px ${eyeY}px">` +
    `<ellipse cx="${x}" cy="${eyeY}" rx="2.2" ry="2.3" fill="${eyeColor}"/>` +
    `<circle cx="${x - 0.6}" cy="${eyeY - 0.7}" r="0.65" fill="#fff" opacity="0.9"/></g>`;
  return (
    `<svg class="av-svg" viewBox="0 0 40 40" width="${size}" height="${size}" aria-hidden="true">` +
    `<g class="av-body"><g class="av-fill">${BODY[shape]}</g>${eye(15.4)}${eye(24.6)}</g></svg>`
  );
}

function safeColor(color: string, botId: string): string {
  // Only allow plain color syntax into the style attribute.
  return /^[#a-z0-9(),.%\s-]{1,40}$/i.test(color || "") ? color : defaultColor(botId);
}

/**
 * Build an avatar element: <span class="av is-idle|is-busy|is-ask [is-listening] [av-still]">.
 * Update later with setAvatarState() instead of re-rendering.
 */
export function renderAvatar(avatar: Avatar, opts: AvatarOptions): HTMLElement {
  const el = document.createElement("span");
  el.className = "av";
  el.style.width = opts.size + "px";
  el.style.height = opts.size + "px";
  if (avatar.kind === "image" && avatar.url) {
    const img = document.createElement("img");
    img.className = "av-img";
    img.alt = "";
    img.width = opts.size;
    img.height = opts.size;
    img.decoding = "async";
    img.src = avatar.url;
    img.onerror = () => {
      // Fall back to the generated face if the image cannot load.
      const color = safeColor(avatar.color, opts.botId);
      el.innerHTML = shapeSvg(resolveShape(avatar.shape, opts.botId), color, opts.size);
      (el.querySelector(".av-fill") as SVGElement).style.fill = color;
      appendExtras(el);
    };
    el.appendChild(img);
  } else {
    const color = safeColor(avatar.color || defaultColor(opts.botId), opts.botId);
    el.innerHTML = shapeSvg(resolveShape(avatar.shape, opts.botId), color, opts.size);
    (el.querySelector(".av-fill") as SVGElement).style.fill = color;
  }
  appendExtras(el);
  setAvatarState(el, opts.state || "idle", !!opts.listening, opts.animate !== false);
  return el;
}

function appendExtras(el: HTMLElement): void {
  if (el.querySelector(".av-dots")) return;
  const dots = document.createElement("span");
  dots.className = "av-dots";
  dots.innerHTML = "<i></i><i></i><i></i>";
  el.appendChild(dots);
  const badge = document.createElement("span");
  badge.className = "av-badge";
  el.appendChild(badge);
}

export function setAvatarState(el: HTMLElement, state: BotState, listening = false, animate = true): void {
  el.classList.toggle("is-idle", state === "idle");
  el.classList.toggle("is-busy", state === "busy");
  el.classList.toggle("is-ask", state === "ask");
  el.classList.toggle("is-listening", listening);
  el.classList.toggle("av-still", !animate);
}

/** Mic level 0–1 for the listening pulse. */
export function setAvatarLevel(el: HTMLElement, level: number): void {
  el.style.setProperty("--level", String(Math.max(0, Math.min(1, level))));
}

/** CSS for avatars. Inject once (injectAvatarCss) or include in a stylesheet. */
export const AVATAR_CSS = `
.av{position:relative;display:inline-block;flex:none;line-height:0;--level:0}
.av-svg{display:block;overflow:visible}
.av-img{display:block;border-radius:30%;object-fit:cover}
.av-body{transform-origin:20px 20px}
.av-eye{animation:av-blink 3.2s infinite}
.av.is-busy .av-eye{animation-duration:1.45s}
.av.is-busy .av-body,.av.is-busy .av-img{animation:av-rock 2.4s ease-in-out infinite}
.av.is-listening .av-body,.av.is-listening .av-img{animation:none;transform:scale(calc(1 + var(--level) * 0.12));transition:transform 90ms linear}
.av-dots{position:absolute;left:0;right:0;bottom:-18%;display:none;justify-content:center;gap:8%}
.av-dots i{width:9%;padding-bottom:9%;border-radius:50%;background:var(--fg,#fff);opacity:.2;animation:av-dot 1.2s infinite}
.av-dots i:nth-child(2){animation-delay:.3s}
.av-dots i:nth-child(3){animation-delay:.6s}
.av.is-busy .av-dots{display:flex}
.av-badge{position:absolute;top:-4%;right:-4%;width:26%;height:26%;border-radius:50%;background:var(--warn,#f59e0b);border:2px solid var(--bg,#000);box-sizing:border-box;display:none}
.av.is-ask .av-badge{display:block}
.av.av-still .av-eye,.av.av-still .av-body,.av.av-still .av-img,.av.av-still .av-dots i{animation:none}
@keyframes av-blink{0%,95%{transform:scaleY(1)}97.5%{transform:scaleY(.1)}100%{transform:scaleY(1)}}
@keyframes av-rock{0%,100%{transform:rotate(-4deg)}50%{transform:rotate(4deg)}}
@keyframes av-dot{0%,100%{opacity:.2}30%{opacity:1}}
@media (prefers-reduced-motion:reduce){.av-eye,.av-body,.av-img,.av-dots i{animation:none!important}}
`;

export function injectAvatarCss(doc: Document = document): void {
  if (doc.getElementById("av-css")) return;
  const style = doc.createElement("style");
  style.id = "av-css";
  style.textContent = AVATAR_CSS;
  doc.head.appendChild(style);
}

// ---- Pets (petdex spritesheets: 8 cols x 9 rows, 6 frames per row) ----

export const PET_ROW = { idle: 0, wave: 3, failed: 5, waiting: 6, running: 7, review: 8 } as const;
export type PetMood = keyof typeof PET_ROW;

import type { Pet } from "./protocol";

/** A sprite element showing one row of the sheet, animated with CSS steps(). */
export function renderPet(pet: Pet, height: number, mood: PetMood = "idle"): HTMLElement {
  const scale = height / pet.ch;
  const w = pet.cw * scale;
  const el = document.createElement("span");
  el.className = "pet";
  el.style.width = w + "px";
  el.style.height = height + "px";
  el.style.backgroundImage = `url("${encodeURI(pet.url)}")`;
  el.style.backgroundSize = `${pet.cols * w}px ${pet.rows * height}px`;
  el.style.setProperty("--pet-end", `${-pet.frames * w}px`);
  el.style.animationDuration = pet.loop_ms + "ms";
  el.style.animationTimingFunction = `steps(${pet.frames})`;
  setPetMood(el, pet, mood, height);
  return el;
}

export function setPetMood(el: HTMLElement, pet: Pet, mood: PetMood, height: number): void {
  const row = Math.min(PET_ROW[mood], pet.rows - 1);
  el.style.backgroundPositionY = `${-row * height}px`;
}

export function petMoodFor(state: BotState): PetMood {
  return state === "busy" ? "running" : state === "ask" ? "waiting" : "idle";
}

export const PET_CSS = `
.pet{display:inline-block;flex:none;background-repeat:no-repeat;background-position-x:0;image-rendering:pixelated;animation-name:pet-walk;animation-iteration-count:infinite}
@keyframes pet-walk{from{background-position-x:0}to{background-position-x:var(--pet-end)}}
`;
