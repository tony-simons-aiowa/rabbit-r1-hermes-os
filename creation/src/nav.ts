// Screen stack. Screens are absolutely positioned layers; only the top one has class "on" (display: flex).
import type { AppEvent } from "./platform";

export interface Screen {
  el: HTMLElement;
  onShow?(): void;
  onHide?(): void;
  /** Return true when the event was handled. Unhandled "back" pops the stack. */
  onInput(ev: AppEvent): boolean;
  destroy?(): void;
}

let container: HTMLElement | null = null;
const stack: Screen[] = [];

export function initNav(el: HTMLElement): void {
  container = el;
}

export function current(): Screen | undefined {
  return stack[stack.length - 1];
}

export function depth(): number {
  return stack.length;
}

function mount(s: Screen): void {
  if (!container) throw new Error("nav not initialised");
  s.el.classList.add("screen");
  container.appendChild(s.el);
  s.el.classList.add("on");
  s.onShow?.();
}

function unmount(s: Screen): void {
  s.onHide?.();
  s.el.classList.remove("on");
  s.el.remove();
  s.destroy?.();
}

export function push(s: Screen): void {
  const top = current();
  if (top) {
    top.onHide?.();
    top.el.classList.remove("on");
  }
  stack.push(s);
  mount(s);
}

export function pop(): void {
  if (stack.length <= 1) return;
  const s = stack.pop() as Screen;
  unmount(s);
  const top = current();
  if (top) {
    top.el.classList.add("on");
    top.onShow?.();
  }
}

/** Replace the top screen (or push when empty). */
export function replace(s: Screen): void {
  const old = stack.pop();
  if (old) unmount(old);
  push(s);
}

/** Pop everything above the root and replace the root. */
export function reset(root: Screen): void {
  while (stack.length) {
    const s = stack.pop() as Screen;
    unmount(s);
  }
  push(root);
}

export function dispatch(ev: AppEvent): void {
  const top = current();
  if (!top) return;
  let handled = false;
  try {
    handled = top.onInput(ev);
  } catch (e) {
    console.error(e);
  }
  if (!handled && ev === "back") pop();
}
