// Per-device named routines with explicit bot or device-owned room targets.
import type { AdminRoutines, Routine } from "../../../shared/protocol";
import { button, card, errText, focusKey, h, toast, type Tab } from "../dom";
import { getAdmin, getApi, onChange } from "../state";

const MAX_ROUTINES = 30;

type DeviceDraft = {
  routines: Routine[];
  groups: AdminRoutines["groups"];
  savedJson: string;
  loaded: boolean;
  loading: boolean;
  saving: boolean;
  reloading: boolean;
  error: string;
  status: string;
};

function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function newId(existing: string[]): string {
  let id = "";
  do id = "r" + Math.random().toString(36).slice(2, 10);
  while (existing.includes(id));
  return id;
}

function json(routines: Routine[]): string {
  return JSON.stringify(routines);
}

export function mountRoutines(): Tab {
  const drafts = new Map<string, DeviceDraft>();
  let selectedId = "";
  let deviceSig: string | null = null;
  let rowsSig = "";
  let attempted = false;
  let confirmReload = false;
  let pendingFocus = "";

  const devicePicker = h("select", { "aria-label": "Paired device", onchange: () => chooseDevice(devicePicker.value) });
  const deviceHint = h("p", { class: "muted" });
  const rows = h("ol", { class: "routine-list", "aria-label": "Device routines" });
  const validationNodes = new Map<string, HTMLElement>();
  const empty = h("p", { class: "muted", hidden: true }, "This device has no routines yet. Add one to create a named prompt.");
  const roomNote = h("p", { class: "muted", hidden: true });
  const errorBox = h("div", { class: "callout err", role: "alert", hidden: true });
  const status = h("span", { class: "status-slot muted", role: "status", "aria-live": "polite" });
  const addButton = button("Add routine", () => addRoutine(), { variant: "primary" });
  const saveButton = button("Save changes", () => void save(), { variant: "primary" });
  const reloadButton = button("Reload from server", () => reload(), { variant: "ghost" });
  const reloadHint = h("p", { class: "hint", hidden: true }, "Reloading discards this device's unsaved edits. Click the button again to confirm, or switch devices to keep this draft.");

  function current(): DeviceDraft | null {
    return selectedId ? drafts.get(selectedId) ?? null : null;
  }

  function selectedIsPaired(): boolean {
    return !!selectedId && getAdmin().devices.some((d) => d.id === selectedId);
  }

  function changed(d: DeviceDraft): boolean {
    return json(d.routines) !== d.savedJson;
  }

  function chooseDevice(id: string): void {
    selectedId = id;
    attempted = false;
    confirmReload = false;
    rowsSig = "";
    render();
    if (id && !drafts.has(id)) void load(id);
  }

  async function load(deviceId: string): Promise<void> {
    const entry: DeviceDraft = {
      routines: [], groups: [], savedJson: "[]", loaded: false, loading: true, saving: false,
      reloading: false, error: "", status: "Loading routines…",
    };
    drafts.set(deviceId, entry);
    render();
    try {
      const result = await getApi().adminRoutines(deviceId);
      if (drafts.get(deviceId) !== entry) return;
      entry.routines = copy(result.routines);
      entry.groups = result.groups;
      entry.savedJson = json(entry.routines);
      entry.loaded = true;
      entry.loading = false;
      entry.error = "";
      entry.status = "Loaded from server.";
      if (selectedId === deviceId) rowsSig = "";
    } catch (e) {
      if (drafts.get(deviceId) !== entry) return;
      entry.loading = false;
      entry.error = "Couldn't load routines: " + errText(e);
      entry.status = "";
    }
    render();
  }

  function reload(): void {
    const d = current();
    if (!selectedId || !d || d.loading || d.saving || d.reloading) return;
    if (changed(d) && !confirmReload) {
      confirmReload = true;
      render();
      return;
    }
    const deviceId = selectedId;
    d.reloading = true;
    d.status = "Reloading…";
    confirmReload = false;
    render();
    void getApi().adminRoutines(deviceId).then((result) => {
      if (drafts.get(deviceId) !== d) return;
      d.routines = copy(result.routines);
      d.groups = result.groups;
      d.savedJson = json(d.routines);
      d.loaded = true;
      d.reloading = false;
      d.error = "";
      d.status = "Reloaded from server.";
      if (selectedId === deviceId) rowsSig = "";
      render();
    }).catch((e: unknown) => {
      if (drafts.get(deviceId) !== d) return;
      d.reloading = false;
      d.error = "Couldn't reload routines: " + errText(e);
      d.status = "";
      render();
    });
  }

  function addRoutine(): void {
    const d = current();
    if (!d || !selectedIsPaired() || d.loading || d.saving || d.reloading || d.routines.length >= MAX_ROUTINES) return;
    const id = newId(d.routines.map((r) => r.id));
    d.routines.push({ id, label: "New routine", text: "", target: { kind: "bot", id: "" } });
    attempted = false;
    pendingFocus = "routine-label-" + id;
    rowsSig = "";
    d.status = "Unsaved changes";
    render();
  }

  function update(id: string, mutate: (routine: Routine) => void): void {
    const d = current();
    if (!d) return;
    const routine = d.routines.find((r) => r.id === id);
    if (!routine) return;
    mutate(routine);
    d.status = "Unsaved changes";
    syncValidation(d);
    render();
  }

  function remove(id: string): void {
    const d = current();
    if (!d || !selectedIsPaired() || d.saving || d.reloading) return;
    d.routines = d.routines.filter((r) => r.id !== id);
    attempted = false;
    rowsSig = "";
    d.status = "Unsaved changes";
    render();
  }

  function targetMessage(routine: Routine, groups: AdminRoutines["groups"]): string {
    if (!routine.target.id) return "Choose an explicit target.";
    if (routine.target.kind === "bot") {
      const bot = getAdmin().bots.find((b) => b.id === routine.target.id);
      if (!bot) return "This bot no longer exists. Choose an available bot.";
      if (!bot.reachable) return `${bot.name || bot.id} is offline or unavailable. Choose another bot or restore its connection.`;
      return "";
    }
    if (!groups.some((g) => g.id === routine.target.id)) {
      return "This room is unavailable for the selected device. Choose another room or create one on that device.";
    }
    return "";
  }

  function validationError(routine: Routine, d: DeviceDraft): string {
    if (!routine.label.trim()) return "Enter a routine name.";
    if (routine.label.trim().length > 64) return "Routine names can be at most 64 characters.";
    if (!routine.text.trim()) return "Enter the prompt text.";
    const maxText = routine.target.kind === "group" ? 2000 : 8000;
    if (routine.text.length > maxText) return routine.target.kind === "group" ? "Room prompts can be at most 2,000 characters; shorten the prompt before saving." : "Bot prompts can be at most 8,000 characters.";
    return targetMessage(routine, d.groups);
  }

  function syncValidation(d: DeviceDraft): void {
    for (const routine of d.routines) {
      const node = validationNodes.get(routine.id);
      if (!node) continue;
      const message = validationError(routine, d);
      node.hidden = !attempted || !message;
      node.textContent = message;
    }
  }

  function targetSelect(routine: Routine, d: DeviceDraft): HTMLSelectElement {
    const bots = getAdmin().bots;
    const nameCounts = new Map<string, number>();
    for (const bot of bots) {
      const key = (bot.name || bot.id).trim().toLocaleLowerCase();
      nameCounts.set(key, (nameCounts.get(key) ?? 0) + 1);
    }
    const choices = routine.target.kind === "bot"
      ? bots.map((b) => {
        const name = b.name || b.id;
        const key = name.trim().toLocaleLowerCase();
        const gateway = b.gateway_name || b.gateway_id;
        const qualified = (nameCounts.get(key) ?? 0) > 1 && gateway ? `${name} · ${gateway}` : name;
        return { id: b.id, name: qualified + (b.reachable ? "" : " (offline/unavailable)") };
      })
      : d.groups;
    const currentExists = choices.some((x) => x.id === routine.target.id);
    const options = [h("option", { value: "" }, routine.target.kind === "bot" ? "Choose a bot" : "Choose a room")];
    if (!choices.length) {
      options.push(h("option", { value: "", disabled: true }, routine.target.kind === "bot" ? "No bots are configured" : "No rooms belong to this device"));
    }
    if (routine.target.id && !currentExists) {
      options.push(h("option", { value: routine.target.id }, `${routine.target.id} (missing)`));
    }
    for (const choice of choices) options.push(h("option", { value: choice.id }, choice.name));
    const picker = h("select", {
      "aria-label": "Target for " + (routine.label || "routine"),
      value: routine.target.id,
      disabled: !selectedIsPaired() || d.loading || d.saving || d.reloading,
      onchange: () => update(routine.id, (r) => { r.target.id = picker.value; }),
    }, options);
    picker.value = routine.target.id;
    return picker;
  }

  function routineRow(routine: Routine, d: DeviceDraft): HTMLElement {
    const label = h("input", {
      type: "text", maxlength: 64, value: routine.label, placeholder: "Routine name",
      "aria-label": "Routine name", "data-fk": "routine-label-" + routine.id,
      disabled: !selectedIsPaired() || d.loading || d.saving || d.reloading,
      oninput: () => update(routine.id, (r) => { r.label = label.value; }),
    });
    const text = h("textarea", {
      rows: 3, maxlength: 8000, placeholder: "What should the bot do?",
      "aria-label": "Routine prompt", disabled: !selectedIsPaired() || d.loading || d.saving || d.reloading,
      oninput: () => update(routine.id, (r) => { r.text = text.value.slice(0, 8000); }),
    }, routine.text);
    const kind = h("select", {
      "aria-label": "Target type for " + (routine.label || "routine"),
      value: routine.target.kind,
      disabled: !selectedIsPaired() || d.loading || d.saving || d.reloading,
      onchange: () => {
        const nextKind = kind.value === "group" ? "group" : "bot";
        update(routine.id, (r) => { r.target = { kind: nextKind, id: "" }; });
      },
    }, h("option", { value: "bot" }, "Bot"), h("option", { value: "group" }, "Room"));
    kind.value = routine.target.kind;
    const error = validationError(routine, d);
    const validation = h("p", { class: "field-error", role: "alert", hidden: !attempted || !error }, attempted ? error : "");
    validationNodes.set(routine.id, validation);
    const removeButton = button("Delete", () => remove(routine.id), {
      variant: "ghost", disabled: !selectedIsPaired() || d.saving || d.reloading,
      attrs: { "aria-label": `Delete ${routine.label || "routine"}` },
    });
    const noRooms = routine.target.kind === "group" && d.groups.length === 0
      ? h("p", { class: "hint" }, "No rooms belong to this device yet. Create a room on the R1 before targeting it.")
      : null;
    return h("li", { class: "routine-item" },
      h("div", { class: "routine-head" },
        h("label", { class: "field grow" }, h("span", { class: "field-label" }, "Name"), label),
        h("label", { class: "field" }, h("span", { class: "field-label" }, "Target type"), kind),
        h("label", { class: "field grow" }, h("span", { class: "field-label" }, "Send to"), targetSelect(routine, d)),
        removeButton,
      ),
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Prompt"), text),
      noRooms,
      validation,
    );
  }

  function validateAll(d: DeviceDraft): string[] {
    return d.routines.flatMap((r) => {
      const error = validationError(r, d);
      return error ? [`${r.label.trim() || "Unnamed routine"}: ${error}`] : [];
    });
  }

  async function save(): Promise<void> {
    const d = current();
    if (!selectedId || !d || !selectedIsPaired() || d.loading || d.saving || d.reloading) return;
    attempted = true;
    const errors = validateAll(d);
    if (errors.length) {
      d.error = "Fix the highlighted routines before saving.";
      d.status = "";
      syncValidation(d);
      render();
      toast(errors.slice(0, 3).join(" "), "error");
      return;
    }
    const deviceId = selectedId;
    const sent = copy(d.routines);
    d.saving = true;
    d.error = "";
    d.status = "Saving…";
    render();
    try {
      const result = await getApi().adminSaveRoutines(deviceId, sent);
      if (drafts.get(deviceId) !== d) return;
      d.groups = result.groups;
      d.savedJson = json(result.routines);
      d.saving = false;
      if (selectedId === deviceId) rowsSig = "";
      if (json(d.routines) === json(result.routines)) {
        d.routines = copy(result.routines);
        d.status = "Saved.";
      } else {
        d.status = `Earlier edits saved for this device. Newer edits remain unsaved${selectedId === deviceId ? "" : "; switch back to review them"}.`;
      }
      render();
    } catch (e) {
      if (drafts.get(deviceId) !== d) return;
      d.saving = false;
      d.error = "Couldn't save routines for this device: " + errText(e);
      d.status = changed(d) ? "Unsaved changes remain." : "";
      render();
    }
  }

  function rebuildRows(d: DeviceDraft | null): void {
    validationNodes.clear();
    if (!d) {
      rows.replaceChildren();
      return;
    }
    rows.replaceChildren(...d.routines.map((r) => routineRow(r, d)));
    if (pendingFocus) {
      focusKey(rows, pendingFocus);
      pendingFocus = "";
    }
  }

  function render(): void {
    const admin = getAdmin();
    const nextDevicesSig = admin.devices.map((d) => d.id + ":" + d.name).join("|");
    if (nextDevicesSig !== deviceSig) {
      const existingSelection = selectedId;
      devicePicker.replaceChildren(h("option", { value: "" }, "Choose a paired device"));
      for (const device of admin.devices) devicePicker.append(h("option", { value: device.id }, device.name || device.id));
      if (existingSelection && !admin.devices.some((d) => d.id === existingSelection)) {
        devicePicker.append(h("option", { value: existingSelection }, `${existingSelection} (no longer paired)`));
      }
      deviceSig = nextDevicesSig;
      devicePicker.value = existingSelection;
    }
    deviceHint.textContent = admin.devices.length
      ? "Routines are stored separately for each paired device. Changes stay in that device's draft until saved."
      : "No paired devices yet. Pair an R1 in Devices before creating device routines.";
    const d = current();
    const paired = selectedIsPaired();
    const loading = !!d?.loading || !!d?.reloading;
    addButton.disabled = !d || !d.loaded || !paired || loading || !!d?.saving || d.routines.length >= MAX_ROUTINES;
    saveButton.disabled = !d || !d.loaded || !paired || loading || !!d?.saving || !changed(d);
    reloadButton.disabled = !d || loading || !!d?.saving;
    empty.hidden = !d || loading || d.routines.length > 0;
    roomNote.hidden = !d || d.groups.length > 0;
    roomNote.textContent = d && !d.groups.length ? "No rooms belong to this device. You can still target a bot; create a room on the R1 to target group routines." : "";
    errorBox.hidden = !d?.error;
    errorBox.textContent = d?.error ?? "";
    status.textContent = !d ? "" : changed(d) ? (d.saving ? "Saving…" : d.status || "Unsaved changes") : d.status;
    status.className = "status-slot " + (d && d.error ? "error" : d && changed(d) ? "pending" : "ok");
    const isMissing = !!selectedId && !paired;
    const signature = d
      ? JSON.stringify([selectedId, paired, d.loaded, d.loading, d.saving, d.reloading, d.groups, d.routines.map((r) => [r.id, r.target.kind, r.target.id]), admin.bots.map((b) => [b.id, b.name, b.gateway_id, b.gateway_name, b.reachable])])
      : JSON.stringify([selectedId, isMissing]);
    if (signature !== rowsSig) {
      rowsSig = signature;
      rebuildRows(d);
    }
    rows.hidden = !d || loading;
    if (loading) status.textContent = "Loading routines…";
    if (isMissing) {
      errorBox.hidden = false;
      errorBox.textContent = "This device is no longer paired. Its draft is preserved, but saving is disabled until it is paired again.";
    }
    if (confirmReload) {
      reloadButton.textContent = "Discard edits and reload";
      reloadHint.hidden = false;
    } else {
      reloadButton.textContent = "Reload from server";
      reloadHint.hidden = true;
    }
  }

  const el = h("div", { class: "stack-lg" },
    card("Device routines", deviceHint,
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Paired device"), devicePicker),
      errorBox,
      roomNote,
      h("div", { class: "row between" }, h("span", { class: "muted small" }, "Each routine needs a name, prompt, and explicit target. Bot prompts allow 8,000 characters; room prompts allow 2,000."), status),
      empty,
      rows,
      h("div", { class: "form-actions" }, addButton, reloadButton, saveButton),
      reloadHint,
    ),
  );
  onChange(render);
  render();
  return { el, sync: render };
}
