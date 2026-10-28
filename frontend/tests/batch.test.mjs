import assert from "node:assert/strict";
import { test } from "node:test";
import { parseBatchTasks, submitBatchFiles, validateBatchFile } from "../src/lib/batch.ts";

test("accepts supported files and rejects empty, oversized and unsupported files", () => {
  assert.equal(validateBatchFile({ name: "report.PDF", size: 120 }), null);
  assert.equal(validateBatchFile({ name: "report.pdf", size: 0 }), "Файл пустой");
  assert.equal(validateBatchFile({ name: "report.docx", size: 50 * 1024 * 1024 + 1 }), "Файл больше 50 МБ");
  assert.equal(validateBatchFile({ name: "report.exe", size: 12 }), "Поддерживаются PDF, DOCX и XLSX");
});

test("reads unique task ids and ignores malformed links", () => {
  assert.deepEqual(parseBatchTasks("?tasks=id-1,id_2,id-1"), ["id-1", "id_2"]);
  assert.deepEqual(parseBatchTasks("?tasks=bad%20id,,!"), []);
});

test("submits files in order and preserves successful tasks when one fails", async () => {
  const files = [{ name: "a.pdf" }, { name: "b.pdf" }, { name: "c.pdf" }];
  const seen = [];
  const result = await submitBatchFiles(files, "publication", async (file) => {
    seen.push(file.name);
    if (file.name === "b.pdf") throw new Error("Сервис недоступен");
    return { task_id: file.name, document_id: "doc" };
  });
  assert.deepEqual(seen, ["a.pdf", "b.pdf", "c.pdf"]);
  assert.deepEqual(result.created.map((entry) => entry.id), ["a.pdf", "c.pdf"]);
  assert.deepEqual(result.failed, [{ name: "b.pdf", reason: "Сервис недоступен" }]);
});


