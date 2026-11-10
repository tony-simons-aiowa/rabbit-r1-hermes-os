import { parseColor } from "../../shared/themes";

/** Best-effort #rrggbb for an input[type=color]. Unknown colors fall back to black. */
export function hexOf(color: string): string {
  const rgb = parseColor(color);
  if (!rgb) return "#000000";
  return "#" + rgb.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");
}
