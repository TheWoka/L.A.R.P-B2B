import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError, getLlmModes, submitQuery, uploadDocument } from "../src/api/client.ts";

const task = { task_id: "task-synthetic", document_id: "document-synthetic" };

test("fetches the server default and availability of all modes", async (t) => {
  const response = { default: "local", modes: [{ mode: "local", available: true, model: "demo", reason: null }] };
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(url, "/api/llm/modes");
    assert.equal(init, undefined);
    return Response.json(response);
  });
  assert.deepEqual(await getLlmModes(), response);
});

test("reports a modes API failure to the UI", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "Сервис недоступен" }, { status: 503 }));
  await assert.rejects(getLlmModes, (error) => error instanceof ApiError && error.status === 503);
});

test("sends the selected mode in file multipart data", async (t) => {
  let expected;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(url, "/api/documents");
    assert.equal(init.method, "POST");
    assert.ok(init.body instanceof FormData);
    assert.equal(init.body.get("purpose"), "publication");
    assert.equal(init.body.get("file").name, "synthetic.pdf");
    assert.equal(init.body.get("llm_mode"), expected ?? null);
    return Response.json(task, { status: 202 });
  });
  for (const mode of ["mock", "local", "cloud", undefined]) {
    expected = mode;
    assert.deepEqual(await uploadDocument(new File(["%PDF"], "synthetic.pdf"), "publication", mode), task);
  }
});

test("sends the selected text mode and preserves the default for older callers", async (t) => {
  let expected;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(url, "/api/queries");
    const body = JSON.parse(init.body);
    assert.equal(body.text, "Синтетический документ");
    assert.equal(body.purpose, "internal");
    assert.equal(body.llm_mode, expected);
    return Response.json(task, { status: 202 });
  });
  for (const mode of ["mock", "local", "cloud", undefined]) {
    expected = mode;
    assert.deepEqual(await submitQuery({ text: "Синтетический документ", purpose: "internal" }, mode), task);
  }
  expected = "local";
  await submitQuery({ text: "Синтетический документ", purpose: "internal", llm_mode: "local" });
});

test("sends batch_id and llm_mode together for a bundle upload", async (t) => {
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(url, "/api/documents");
    assert.equal(init.body.get("batch_id"), "batch-synthetic");
    assert.equal(init.body.get("llm_mode"), "local");
    return Response.json(task, { status: 202 });
  });
  assert.deepEqual(
    await uploadDocument(new File(["%PDF"], "synthetic.pdf"), "publication", { batchId: "batch-synthetic", llmMode: "local" }),
    task,
  );
});
