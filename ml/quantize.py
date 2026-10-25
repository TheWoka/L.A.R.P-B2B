"""Сжатие ONNX-модели классификатора без заметной потери точности. Задача T-036a.

Почему не onnxruntime.quantize_dynamic: на rubert-tiny2 она даёт max |Δp| ≈ 0.2 против
PyTorch (int8 для MatMul во FFN и одна шкала на всю таблицу эмбеддингов). Здесь:
- большие таблицы Gather (эмбеддинги — 90% весов) → int8 со шкалой на каждые GROUP чисел
  строки: Gather(int8) → Cast → Reshape → Mul(шкала) → Reshape;
- веса MatMul/Gemm → fp16 + Cast в fp32 перед умножением (вычисления остаются в fp32).
Итог для rubert-tiny2: ≈ 34 МБ вместо 111 МБ; p99 |Δp| ≈ 0.005, max ≈ 0.03 на 1 247
фрагментах. Только стандартные операторы ONNX.
"""

from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

GROUP = 26               # 312 = 12 × 26: 12 шкал на строку (выбрано по Δ на валидации)
MIN_TABLE = 500_000      # таблицы меньше (token_type) не трогаем


def _group_quantize(arr: np.ndarray, group: int) -> tuple[np.ndarray, np.ndarray]:
    """[V, H] float → (int8 [V, H], шкалы [V, H/group, 1]), симметрично, по группам строки."""
    rows, width = arr.shape
    if width % group:
        raise ValueError(f"ширина {width} не делится на группу {group}")
    grouped = arr.reshape(rows, width // group, group)
    scale = np.abs(grouped).max(axis=2, keepdims=True) / 127.0
    scale[scale == 0] = 1.0
    q = np.clip(np.round(grouped / scale), -127, 127).astype(np.int8)
    return q.reshape(rows, width), scale.astype(np.float32)


def _dequant_nodes(name: str, table: str, ids: str, out: str, groups: int, group: int,
                   width: int) -> tuple[list[onnx.NodeProto], list[onnx.TensorProto]]:
    shape_g, shape_h = table + "_shape_g", table + "_shape_h"
    inits = [numpy_helper.from_array(np.array([0, 0, groups, group], np.int64), shape_g),
             numpy_helper.from_array(np.array([0, 0, width], np.int64), shape_h)]
    nodes = [
        helper.make_node("Gather", [table + "_q8", ids], [out + "_q"], name=name + "_q", axis=0),
        helper.make_node("Cast", [out + "_q"], [out + "_f"], name=name + "_cast",
                         to=TensorProto.FLOAT),
        helper.make_node("Reshape", [out + "_f", shape_g], [out + "_g"], name=name + "_rg"),
        helper.make_node("Gather", [table + "_scale", ids], [out + "_s"], name=name + "_s",
                         axis=0),
        helper.make_node("Mul", [out + "_g", out + "_s"], [out + "_m"], name=name + "_mul"),
        helper.make_node("Reshape", [out + "_m", shape_h], [out], name=name + "_rh"),
    ]
    return nodes, inits


def quantize_embeddings(model: onnx.ModelProto, group: int = GROUP) -> int:
    """Таблицы Gather ≥ MIN_TABLE чисел (2D, ids — 2D) → int8 по группам. Возвращает их число.

    Предполагается вход ids формы [batch, seq] — так у BERT-эмбеддингов.
    """
    inits = {i.name: i for i in model.graph.initializer}
    nodes: list[onnx.NodeProto] = []
    done = 0
    for node in model.graph.node:
        w = inits.get(node.input[0]) if node.op_type == "Gather" else None
        if w is None or len(w.dims) != 2 or int(np.prod(w.dims)) < MIN_TABLE:
            nodes.append(node)
            continue
        arr = numpy_helper.to_array(w).astype(np.float32)
        q, scale = _group_quantize(arr, group)
        model.graph.initializer.remove(w)
        model.graph.initializer.extend([numpy_helper.from_array(q, w.name + "_q8"),
                                        numpy_helper.from_array(scale, w.name + "_scale")])
        extra_nodes, extra_inits = _dequant_nodes(
            node.name, w.name, node.input[1], node.output[0], arr.shape[1] // group, group,
            arr.shape[1])
        model.graph.initializer.extend(extra_inits)
        nodes.extend(extra_nodes)
        done += 1
    del model.graph.node[:]
    model.graph.node.extend(nodes)
    return done


def matmul_weights_fp16(model: onnx.ModelProto, min_size: int = 1000) -> int:
    """Константные веса MatMul/Gemm → fp16 + Cast в fp32. Возвращает число весов."""
    inits = {i.name: i for i in model.graph.initializer}
    casts: dict[str, str] = {}           # вес → выход Cast (общий вес конвертируется один раз)
    nodes: list[onnx.NodeProto] = []
    for node in model.graph.node:
        if node.op_type in ("MatMul", "Gemm"):
            for k, name in enumerate(node.input):
                w = inits.get(name)
                if name not in casts and (w is None or w.data_type != TensorProto.FLOAT
                                          or np.prod(w.dims) < min_size):
                    continue
                if name not in casts:
                    model.graph.initializer.remove(w)
                    half = numpy_helper.to_array(w).astype(np.float16)
                    model.graph.initializer.append(numpy_helper.from_array(half, name + "_f16"))
                    nodes.append(helper.make_node("Cast", [name + "_f16"], [name + "_f32"],
                                                  name=name + "_cast", to=TensorProto.FLOAT))
                    casts[name] = name + "_f32"
                node.input[k] = casts[name]
        nodes.append(node)
    del model.graph.node[:]
    model.graph.node.extend(nodes)
    return len(casts)


def compress(fp32: Path, out: Path, group: int = GROUP) -> dict[str, int]:
    """fp32.onnx → сжатая модель (int8-эмбеддинги + fp16-веса), с проверкой графа."""
    model = onnx.load(str(fp32))
    stats = {"embedding_tables": quantize_embeddings(model, group),
             "fp16_weights": matmul_weights_fp16(model)}
    onnx.checker.check_model(model)
    out.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(out))
    return stats
