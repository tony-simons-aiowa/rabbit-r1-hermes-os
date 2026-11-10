import type { MemoryGraph } from "../../../shared/protocol";
import { guard } from "../app";
import { pop, push } from "../nav";
import { state } from "../state";
import { h, List } from "../ui";
import { row, screenEl } from "./base";
import { openSheet } from "./menu";

const SVG = "http://www.w3.org/2000/svg";
const WIDTH = 224;
const HEIGHT = 74;
const CENTER = { x: WIDTH / 2, y: HEIGHT / 2 };

export function openMemoryGraph(): void {
  const profiles = state.bots.filter((bot) => !bot.gateway_id || bot.gateway_id === "local");
  if (profiles.length <= 1) {
    showGraph(profiles[0]?.id ?? "default", profiles[0]?.name ?? "Local");
    return;
  }
  openSheet({
    title: "Memory Graph · Profile",
    rows: profiles.map((bot) => ({ label: bot.name, onPick: () => showGraph(bot.id, bot.name) })),
  });
}

function showGraph(botId: string, name: string): void {
  const el = screenEl("memory-graph");
  el.append(h("div", { class: "fleet-head" },
    h("button", { class: "fleet-back", type: "button", onclick: pop, "aria-label": "Back to Settings" }, "‹ Back"),
    h("div", { class: "fleet-title" }, "Memory Graph"),
  ));
  const status = h("div", { class: "fleet-summary", role: "status" }, "Loading " + name + "…");
  const graphEl = document.createElementNS(SVG, "svg");
  graphEl.setAttribute("viewBox", `0 0 ${WIDTH} ${HEIGHT}`);
  graphEl.setAttribute("class", "memory-map");
  graphEl.setAttribute("role", "img");
  graphEl.setAttribute("hidden", "");
  const listEl = h("div", { class: "memory-node-list" });
  el.append(status, graphEl, listEl);

  let graph: MemoryGraph = { nodes: [], edges: [], total: 0 };
  let disposed = false;
  const list = new List(listEl, () => draw(), () => draw());
  list.bindTouch();

  function draw(): void {
    const selected = graph.nodes[list.index];
    if (!selected) return;
    const linked = new Set<string>();
    for (const { source, target } of graph.edges) {
      if (source === selected.id && target !== selected.id) linked.add(target);
      if (target === selected.id && source !== selected.id) linked.add(source);
    }
    const neighbors = graph.nodes.filter((node) => linked.has(node.id)).slice(0, 10);
    graphEl.replaceChildren();
    graphEl.setAttribute("aria-label", `${selected.label}, ${linked.size} connections`);
    neighbors.forEach((node, index) => {
      const angle = 2 * Math.PI * index / neighbors.length;
      const x = CENTER.x + 93 * Math.cos(angle);
      const y = CENTER.y + 28 * Math.sin(angle);
      const line = document.createElementNS(SVG, "line");
      for (const [key, value] of Object.entries({ x1: CENTER.x, y1: CENTER.y, x2: x, y2: y })) {
        line.setAttribute(key, String(value));
      }
      graphEl.append(line, dot(x, y, 4, node.kind));
    });
    graphEl.append(dot(CENTER.x, CENTER.y, 8, selected.kind));
    status.textContent = `${name} · ${linked.size} links · ${graph.nodes.length}/${graph.total} nodes`;
  }

  function dot(x: number, y: number, radius: number, kind: string): SVGCircleElement {
    const circle = document.createElementNS(SVG, "circle");
    circle.setAttribute("cx", String(x));
    circle.setAttribute("cy", String(y));
    circle.setAttribute("r", String(radius));
    circle.setAttribute("class", kind);
    return circle;
  }

  push({
    el,
    onInput(ev) {
      if (ev === "up" || ev === "down") {
        list.move(ev === "up" ? -1 : 1);
        return true;
      }
      if (ev === "click") {
        list.activate();
        return true;
      }
      if (ev === "menu") {
        pop();
        return true;
      }
      return false;
    },
    destroy() { disposed = true; },
  });

  if (!state.api) {
    status.textContent = "Pair the R1 to load the Memory Graph.";
    return;
  }
  void guard(state.api.memoryGraph(botId)).then((result) => {
    if (disposed) return;
    graph = result;
    if (!graph.nodes.length) {
      status.textContent = "No learned skills or memories yet.";
      return;
    }
    graphEl.removeAttribute("hidden");
    list.setRows(graph.nodes.map((node) => row({
      title: node.label,
      sub: node.kind === "skill" ? "Learned skill" : "Stored memory",
      cls: "sheet-row",
    })), false);
    draw();
  }).catch((error: unknown) => {
    if (!disposed) status.textContent = error instanceof Error ? error.message : "Could not load Memory Graph.";
  });
}
