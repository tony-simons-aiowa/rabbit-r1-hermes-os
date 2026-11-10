// Tool name → icon, following Hermes Desktop's tool card mapping (emoji stand-ins for its Phosphor icons).

const EXACT: Record<string, string> = {
  terminal: "⌨",
  execute_code: "⌨",
  read_file: "📄",
  write_file: "✎",
  edit_file: "✎",
  patch: "✎",
  list_files: "🗂",
  search_files: "🔍",
  web_search: "🔍",
  session_search_recall: "🔍",
  web_extract: "🌐",
  image_generate: "🖼",
  vision_analyze: "👁",
  memory: "🧠",
  todo: "☑",
  cronjob: "⏱",
  clarify: "❓",
  r1_notify: "🔔",
};

const PREFIX: [string, string][] = [
  ["browser_", "🌐"],
  ["web_", "🌐"],
  ["skill", "📘"],
  ["delegate", "👥"],
  ["subagent", "👥"],
  ["mcp_", "🔌"],
];

export function toolIcon(name: string): string {
  const n = (name || "").toLowerCase();
  if (EXACT[n]) return EXACT[n];
  for (const [p, icon] of PREFIX) if (n.startsWith(p)) return icon;
  return "⚙";
}

/** "search_files" → "search files" */
export function toolLabel(name: string): string {
  return (name || "tool").replace(/_/g, " ");
}
