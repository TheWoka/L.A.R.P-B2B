"""Предсказатели вероятностей классов для фрагментов: PyTorch (после train.py) и ONNX. T-036a.

Оба возвращают матрицу [n, 3] в порядке common.LABELS. ONNX-вариант использует только
`tokenizers` + `onnxruntime` — так же будет работать конвейер в контейнере (T-036b).
"""

import json
from pathlib import Path
from typing import Protocol

import numpy as np
from common import LABELS, MAX_LEN


class Predictor(Protocol):
    name: str

    def predict_proba(self, texts: list[str]) -> np.ndarray: ...


def _softmax(logits: np.ndarray) -> np.ndarray:
    z = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def _reorder(order: list[str]) -> list[int]:
    """Индексы столбцов модели в порядке LABELS (порядок в модели может отличаться)."""
    return [order.index(lab) for lab in LABELS]


class TorchPredictor:
    """Модель из ml/out/best (save_pretrained)."""

    name = "torch"

    def __init__(self, model_dir: Path, batch: int = 64) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_dir).eval()
        id2label = self.model.config.id2label
        self.columns = _reorder([id2label[i] for i in range(len(id2label))])
        self.batch = batch

    def predict_proba(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, len(LABELS)), dtype=np.float32)
        out = []
        with self._torch.no_grad():
            for i in range(0, len(texts), self.batch):
                enc = self.tokenizer(texts[i:i + self.batch], truncation=True,
                                     max_length=MAX_LEN, padding=True, return_tensors="pt")
                logits = self.model(**enc).logits.float().numpy()
                out.append(_softmax(logits)[:, self.columns])
        return np.concatenate(out)


class OnnxPredictor:
    """data/models/grif-cls: model.onnx + tokenizer.json + labels.json."""

    name = "onnx"

    def __init__(self, model_dir: Path, batch: int = 64, model_file: str = "model.onnx") -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(MAX_LEN)
        self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id("[PAD]") or 0)
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(model_dir / model_file), options,
                                            providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}
        labels = json.loads((model_dir / "labels.json").read_text(encoding="utf-8"))
        self.columns = _reorder(list(labels))
        self.batch = batch

    def predict_proba(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, len(LABELS)), dtype=np.float32)
        out = []
        for i in range(0, len(texts), self.batch):
            enc = self.tokenizer.encode_batch(texts[i:i + self.batch])
            feeds = {
                "input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
            }
            if "token_type_ids" in self.inputs:
                feeds["token_type_ids"] = np.zeros_like(feeds["input_ids"])
            logits = self.session.run(None, {k: v for k, v in feeds.items() if k in self.inputs})
            out.append(_softmax(logits[0])[:, self.columns])
        return np.concatenate(out)
