import assert from "node:assert/strict";
import { test } from "node:test";

import { LABELS, loadSavedMode, pickInitialMode, saveMode } from "../src/lib/llmMode.ts";

const modes = [
  { mode: "mock", available: true, model: null, reason: null },
  { mode: "local", available: true, model: "local-model", reason: null },
  { mode: "cloud", available: true, model: "cloud-model", reason: null },
];

test("prefers an available saved mode over the server default", () => {
  for (const saved of ["mock", "local", "cloud"]) {
    assert.equal(pickInitialMode(modes, saved, "local"), saved);
  }
});

test("uses the available default when there is no saved mode or it is unavailable", () => {
  assert.equal(pickInitialMode(modes, null, "cloud"), "cloud");
  const unavailableCloud = modes.map((item) => ({ ...item, available: item.mode !== "cloud" }));
  assert.equal(pickInitialMode(unavailableCloud, "cloud", "local"), "local");
  assert.equal(pickInitialMode(modes.slice(0, 2), "cloud", "local"), "local");
});

test("falls back to mock when the default is unavailable or modes are empty", () => {
  assert.equal(pickInitialMode(modes.slice(0, 1), "local", "cloud"), "mock");
  assert.equal(pickInitialMode([], null, "local"), "mock");
  assert.equal(pickInitialMode(modes.map((item) => ({ ...item, available: false })), "cloud", "local"), "mock");
});

function stubStorage(t, getStorage) {
  const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get: getStorage });
  t.after(() => {
    if (original) Object.defineProperty(globalThis, "localStorage", original);
    else delete globalThis.localStorage;
  });
}

test("saves and restores a selection using the project storage key", (t) => {
  const entries = new Map();
  stubStorage(t, () => ({ getItem: (key) => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value) }));
  assert.equal(loadSavedMode(), null);
  for (const mode of ["mock", "local", "cloud"]) {
    saveMode(mode);
    assert.equal(entries.get("grif-llm-mode"), mode);
    assert.equal(loadSavedMode(), mode);
  }
  assert.deepEqual(Object.values(LABELS), ["Без нейросети", "Локально (Ollama)", "Облако"]);
});

test("ignores malformed saved modes", (t) => {
  let saved = "other";
  stubStorage(t, () => ({ getItem: () => saved }));
  for (const value of ["other", "", '"cloud"', "CLOUD"]) {
    saved = value;
    assert.equal(loadSavedMode(), null);
  }
});

test("storage access and write errors do not prevent checking a document", (t) => {
  stubStorage(t, () => { throw new Error("Storage disabled"); });
  assert.equal(loadSavedMode(), null);
  assert.doesNotThrow(() => saveMode("local"));
});

test("handles storage method errors", (t) => {
  stubStorage(t, () => ({
    getItem: () => { throw new Error("Read failed"); },
    setItem: () => { throw new Error("Quota exceeded"); },
  }));
  assert.equal(loadSavedMode(), null);
  assert.doesNotThrow(() => saveMode("cloud"));
});
