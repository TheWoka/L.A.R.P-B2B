"""Тесты сжатия ONNX (T-036a) на маленьком графе: эмбеддинги → MatMul."""

import sys
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import pytest
from onnx import TensorProto, helper, numpy_helper

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import quantize as qz  # noqa: E402


def tiny_model(vocab: int = 2000, width: int = 312) -> onnx.ModelProto:
    rng = np.random.default_rng(0)
    emb = numpy_helper.from_array(rng.normal(size=(vocab, width)).astype(np.float32), "emb")
    w = numpy_helper.from_array(rng.normal(size=(width, 8)).astype(np.float32), "w")
    nodes = [helper.make_node("Gather", ["emb", "ids"], ["h"], name="g", axis=0),
             helper.make_node("MatMul", ["h", "w"], ["y"], name="mm")]
    graph = helper.make_graph(
        nodes, "t", [helper.make_tensor_value_info("ids", TensorProto.INT64, ["b", "s"])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, ["b", "s", 8])], [emb, w])
    return helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=9)


def run(path: Path, ids: np.ndarray) -> np.ndarray:
    return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"]).run(
        None, {"ids": ids})[0]


def test_compress_is_close_and_smaller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qz, "MIN_TABLE", 1000)
    fp32, out = tmp_path / "fp32.onnx", tmp_path / "small.onnx"
    onnx.save(tiny_model(), str(fp32))
    stats = qz.compress(fp32, out)
    assert stats == {"embedding_tables": 1, "fp16_weights": 1}
    ids = np.array([[1, 5, 1999], [0, 7, 42]], dtype=np.int64)
    ref, got = run(fp32, ids), run(out, ids)
    assert got.shape == ref.shape
    assert np.abs(got - ref).max() / np.abs(ref).max() < 0.02
    assert out.stat().st_size < fp32.stat().st_size / 3


def test_group_must_divide_width() -> None:
    with pytest.raises(ValueError):
        qz._group_quantize(np.zeros((4, 10), dtype=np.float32), group=4)


def test_small_tables_untouched(tmp_path: Path) -> None:
    model = tiny_model(vocab=10, width=312)          # 3 120 чисел < MIN_TABLE
    assert qz.quantize_embeddings(model) == 0
