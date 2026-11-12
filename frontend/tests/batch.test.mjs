import assert from "node:assert/strict";
import { test } from "node:test";
import { documentCountLabel, parseBatchTasks, submitBatchFiles, validateBatchFile } from "../src/lib/batch.ts";

test("uses natural Russian forms for document count", () => {
  assert.equal(documentCountLabel(1), "1 документ");
  assert.equal(documentCountLabel(4), "4 документа");
  assert.equal(documentCountLabel(10), "10 документов");
  assert.equal(documentCountLabel(11), "11 документов");
  assert.equal(documentCountLabel(21), "21 документ");
});

test("accepts supported files and rejects empty, oversized and unsupported files", () => {
  assert.equal(validateBatchFile({ name: "report.PDF", size: 120 }), null);
  assert.equal(validateBatchFile({ name: "report.pdf", size: 0 }), "Файл пустой");
  assert.equal(validateBatchFile({ name: "report.docx", size: 50 * 1024 * 1024 + 1 }), "Файл больше 50 МБ");
  assert.equal(validateBatchFile({ name: "report.exe", size: 12 }), "Поддерживаются PDF, DOCX, XLSX, XML или изображение скана (PNG, JPG, TIFF)");
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

test("uses its own purpose for every file when a function is given", async () => {
  const files = [{ name: "a.pdf" }, { name: "b.xml" }];
  const purposes = ["contract", "publication"];
  const seen = [];
  await submitBatchFiles(files, (file, index) => purposes[index], async (file, purpose) => {
    seen.push([file.name, purpose]);
    return { task_id: file.name, document_id: "doc" };
  });
  assert.deepEqual(seen, [["a.pdf", "contract"], ["b.xml", "publication"]]);
});


test("accepts scans and propagates the batch id on every upload", async () => {
  for (const ext of ["png", "JPG", "jpeg", "tif", "tiff", "xml"]) {
    assert.equal(validateBatchFile({name: `synthetic.${ext}`, size: 100}), null);
  }
  const optionsSeen = [];
  await submitBatchFiles([{name: "a.pdf"}, {name: "b.png"}], "internal", async (_file, _purpose, options) => {
    optionsSeen.push(options);
    return {task_id: String(optionsSeen.length), document_id: "doc"};
  }, "batch-123");
  assert.deepEqual(optionsSeen, [{batchId: "batch-123"}, {batchId: "batch-123"}]);
});

test("limits legacy task links to the batch limit of unique valid ids", () => {
  assert.equal(parseBatchTasks("?tasks=" + Array.from({length: 105}, (_, i) => `task-${i}`).join(",")).length, 100);
});

test("reports upload progress after every file, including failed ones", async () => {
  const progress = [];
  const files = [{ name: "a.pdf" }, { name: "b.pdf" }, { name: "c.pdf" }];
  const upload = async (file) => {
    if (file.name === "b.pdf") throw new Error("Сервер недоступен");
    return { task_id: `task-${file.name}` };
  };
  const result = await submitBatchFiles(files, "internal", upload, "batch-1", (sent, total) => progress.push([sent, total]));
  assert.deepEqual(progress, [[1, 3], [2, 3], [3, 3]]);
  assert.equal(result.created.length, 2);
  assert.equal(result.failed.length, 1);
});

test("allows a batch of 100 files", async () => {
  const { MAX_BATCH_FILES } = await import("../src/lib/batch.ts");
  assert.equal(MAX_BATCH_FILES, 100);
});
