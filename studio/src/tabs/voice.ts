// Voice & buttons tab: voice input mode, read-aloud, side-button gestures, chat display toggles.
import type { DeviceConfig } from "../../../shared/protocol";
import { card, h, select, setValue, toggle, type Tab } from "../dom";
import { edit, getAdmin, getDraft, onChange } from "../state";

type VoiceInput = DeviceConfig["voice"]["input"];
type Choice = { value: string; label: string; help: string };

const VOICE_MODES: (Choice & { value: VoiceInput })[] = [
  {
    value: "auto",
    label: "Automatic",
    help: "Uses the browser microphone when the R1 allows it, otherwise the R1's native voice handler.",
  },
  {
    value: "mediarecorder",
    label: "Browser microphone",
    help: "Records a short clip in the creation and sends it to Hermes to transcribe.",
  },
  {
    value: "native",
    label: "R1 native",
    help: "Uses the R1's built-in speech-to-text bridge. Not every firmware has one.",
  },
  { value: "off", label: "Off", help: "Hold-to-talk is disabled. The side button still selects." },
];

const DOUBLE_CLICK: Choice[] = [
  { value: "back", label: "Back", help: "Go back to the previous screen." },
  { value: "menu", label: "Menu", help: "Open the chat menu." },
];

const LONG_PRESS: Choice[] = [
  { value: "talk", label: "Talk", help: "Hold to talk to the current bot." },
  { value: "menu", label: "Menu", help: "Open the chat menu." },
];

function radios(
  name: string,
  legend: string,
  options: Choice[],
  get: () => string,
  set: (v: string) => void,
): { el: HTMLElement; sync: () => void } {
  const inputs: HTMLInputElement[] = [];
  const items = options.map((o) => {
    const input = h("input", { type: "radio", name, value: o.value, onchange: () => set(o.value) });
    inputs.push(input);
    return h(
      "label",
      { class: "radio" },
      input,
      h("span", null, h("span", { class: "radio-title" }, o.label), " ", h("span", { class: "hint" }, o.help)),
    );
  });
  const el = h("fieldset", { class: "radio-group" }, h("legend", { class: "field-label" }, legend), items);
  return {
    el,
    sync: () => {
      const cur = get();
      options.forEach((o, i) => {
        inputs[i].checked = o.value === cur;
      });
    },
  };
}

export function mountVoice(): Tab {
  const voiceSelect = select({
    label: "Voice input mode",
    options: VOICE_MODES.map((m) => ({ value: m.value, label: m.label })),
    value: "auto",
    onChange: (v) =>
      edit((d) => {
        d.voice.input = v as VoiceInput;
      }),
  });
  const voiceHelp = h("p", { class: "hint" });
  const sttNote = h(
    "div",
    { class: "callout warn" },
    "No speech-to-text provider is configured in Hermes, so the R1 can't transcribe your voice yet.",
  );

  const autoSpeak = toggle({
    label: "Read answers aloud",
    hint: "Plays the final answer of each turn through the R1 speaker.",
    checked: false,
    onChange: (v) =>
      edit((d) => {
        d.voice.auto_speak = v;
      }),
  });
  const ttsNote = h(
    "div",
    { class: "callout warn" },
    "No text-to-speech provider is configured in Hermes, so answers can't be read aloud.",
  );

  const dbl = radios(
    "dbl-click",
    "Double-click the side button",
    DOUBLE_CLICK,
    () => getDraft().buttons.double_click,
    (v) =>
      edit((d) => {
        d.buttons.double_click = v as "back" | "menu";
      }),
  );
  const longp = radios(
    "long-press",
    "Long-press the side button",
    LONG_PRESS,
    () => getDraft().buttons.long_press,
    (v) =>
      edit((d) => {
        d.buttons.long_press = v as "talk" | "menu";
      }),
  );

  const tools = toggle({
    label: "Show tool activity",
    hint: "Small chips for each tool call while a chat answer is produced.",
    checked: true,
    onChange: (v) =>
      edit((d) => {
        d.chat.show_tools = v;
      }),
  });
  const notes = toggle({
    label: "Show commentary",
    hint: "Dimmed mid-turn notes from the assistant.",
    checked: true,
    onChange: (v) =>
      edit((d) => {
        d.chat.show_notes = v;
      }),
  });

  function sync(): void {
    const d = getDraft();
    const a = getAdmin();
    setValue(voiceSelect, d.voice.input);
    const mode = VOICE_MODES.find((m) => m.value === d.voice.input);
    voiceHelp.textContent = mode ? mode.help : "";
    sttNote.hidden = a.voice.stt || d.voice.input === "off";
    autoSpeak.input.checked = d.voice.auto_speak;
    ttsNote.hidden = a.voice.tts;
    dbl.sync();
    longp.sync();
    tools.input.checked = d.chat.show_tools;
    notes.input.checked = d.chat.show_notes;
  }

  const el = h(
    "div",
    { class: "stack-lg" },
    card(
      "Voice input",
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Mode"), voiceSelect),
      voiceHelp,
      sttNote,
    ),
    card("Read answers aloud", autoSpeak.el, ttsNote),
    card(
      "Side button",
      h("p", { class: "muted" }, "A single click always selects. Choose what a double-click and a long press do."),
      dbl.el,
      longp.el,
    ),
    card("Chat", h("div", { class: "switch-grid" }, tools.el, notes.el)),
  );

  onChange(sync);
  sync();
  return { el, sync };
}
