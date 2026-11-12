"""Экспорт классификатора в ONNX + сжатие (int8-эмбеддинги, fp16-веса). Задача T-036a.

ml/out/best (PyTorch) → ml/out/model_fp32.onnx → data/models/grif-cls/model.onnx (ml/quantize.py).
Проверка: вероятности сжатой ONNX и PyTorch на 200 фрагментах валидации, max |Δ| < 0.02.
Рядом кладутся tokenizer.json/vocab, labels.json, thresholds.json (из evaluate.py), card.md.

Запуск (из корня репозитория, venv с ml/requirements-ml.txt):
    python ml/export_onnx.py
"""

import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    BASE_MODEL,
    BEST_DIR,
    LABELS,
    MODEL_DIR,
    OUT_DIR,
    SEED,
    TRAIN_SETS,
    default_train_files,
    setup_logging,
    train_file,
    write_json,
)
from predictors import OnnxPredictor, TorchPredictor  # noqa: E402

logger = logging.getLogger("export_onnx")

MAX_DELTA = 0.02
TARGET_MB = 35
CHECK_FRAGMENTS = 200


class LogitsOnly(torch.nn.Module):
    """Обёртка: на выходе только logits, вход — input_ids и attention_mask."""

    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        return self.model(input_ids=input_ids, attention_mask=attention_mask).logits


def export_fp32(model_dir: Path, path: Path) -> None:
    model = AutoModelForSequenceClassification.from_pretrained(model_dir).eval()
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    sample = tokenizer(["Наценка на товар составит 14%."], return_tensors="pt")
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        LogitsOnly(model), (sample["input_ids"], sample["attention_mask"]), str(path),
        input_names=["input_ids", "attention_mask"], output_names=["logits"],
        dynamic_axes={"input_ids": {0: "batch", 1: "seq"},
                      "attention_mask": {0: "batch", 1: "seq"}, "logits": {0: "batch"}},
        opset_version=17, dynamo=False,
    )


def quantize(fp32: Path, out: Path) -> dict[str, int]:
    """int8-эмбеддинги по группам + fp16-веса MatMul (почему так — ml/quantize.py)."""
    from quantize import compress

    return compress(fp32, out)


def write_assets(model_dir: Path, target: Path, work: Path) -> dict:
    """tokenizer, labels.json, thresholds.json (без кривых) рядом с model.onnx.

    Если target совпадает с work (v3 лежит в ml/out/v3 целиком), кривые порогов уезжают
    в thresholds_curves.json, а thresholds.json остаётся в формате конвейера.
    """
    AutoTokenizer.from_pretrained(model_dir).save_pretrained(target)
    (target / "labels.json").write_text(json.dumps(list(LABELS)) + "\n", encoding="utf-8")
    source = work / "thresholds.json"
    if not source.is_file():
        logger.warning("Нет %s — сначала python ml/evaluate.py", source)
        return {}
    data = json.loads(source.read_text(encoding="utf-8"))
    thresholds = {k: v for k, v in data.items() if k != "curves"}
    if "curves" in data and source.resolve() == (target / "thresholds.json").resolve():
        write_json(work / "thresholds_curves.json", data)
    write_json(target / "thresholds.json", thresholds)
    return thresholds


def check_texts(n: int = CHECK_FRAGMENTS) -> list[str]:
    """Первые n фрагментов валидационной части fragments.jsonl (тот же split, что train.py)."""
    from train import load_split

    _, val = load_split(default_train_files(), 0.1, SEED)
    return [r["text"] for r in val[:n]]


def timing(predictor: OnnxPredictor, texts: list[str], n: int) -> float:
    batch = (texts * (n // len(texts) + 1))[:n]
    predictor.predict_proba(batch[:32])
    started = time.perf_counter()
    predictor.predict_proba(batch)
    return round(time.perf_counter() - started, 3)


def verify(model_dir: Path, target: Path, fp32: Path) -> dict:
    texts = check_texts()
    ref = TorchPredictor(model_dir).predict_proba(texts)
    shutil.copy(fp32, target / "_fp32_check.onnx")
    try:
        fp = OnnxPredictor(target, model_file="_fp32_check.onnx").predict_proba(texts)
    finally:
        (target / "_fp32_check.onnx").unlink(missing_ok=True)
    int8 = OnnxPredictor(target)
    q = int8.predict_proba(texts)
    agree = float((q.argmax(1) == ref.argmax(1)).mean())
    return {
        "fragments": len(texts),
        "max_delta_fp32": round(float(np.abs(fp - ref).max()), 5),
        "max_delta_int8": round(float(np.abs(q - ref).max()), 5),
        "argmax_agreement_int8": round(agree, 4),
        "size_mb": round((target / "model.onnx").stat().st_size / 2**20, 2),
        "fp32_size_mb": round(fp32.stat().st_size / 2**20, 2),
        "seconds_100": timing(int8, texts, 100), "seconds_1000": timing(int8, texts, 1000),
    }


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def eval_table(summary: dict) -> list[str]:
    lines = ["| Корпус | Вариант | КТ P / R | Особые P / R | Recall ПДн |", "|---|---|---|---|---|"]
    for r in summary.get("rows", []):
        def pr(m: dict | None) -> str:
            return "—" if not m else f"{m['precision']:.2f} / {m['recall']:.2f}"
        lines.append(f"| {r['corpus']} | {r['variant']} | {pr(r['TRADE_SECRET'])} "
                     f"| {pr(r['SPECIAL_CATEGORY'])} | {r['pd_recall']:.3f} |")
    return lines


def data_note() -> str:
    """«v1: 6136 шт., v2: 6350 шт., …» по stats.json имеющихся наборов."""
    parts = []
    for name in TRAIN_SETS:
        stats = _load(train_file(name).with_name("stats.json"))
        if stats:
            parts.append(f"{name}: {stats.get('fragments', '?')} шт.")
    return ", ".join(parts) or "?"


def write_card(target: Path, check: dict, thresholds: dict, work: Path = OUT_DIR) -> None:
    """card.md: что это, на чём обучено, метрики dev/seed, ограничения, лицензия."""
    train = _load(work / "train_metrics.json")
    best = train.get("best", {})
    lines = [
        "# grif-cls — классификатор фрагментов: коммерческая тайна / особые категории ПДн", "",
        f"- Базовая модель: `{BASE_MODEL}` (29 млн параметров, лицензия MIT).",
        "- Задача: фрагмент (предложение) → NONE / TRADE_SECRET / SPECIAL_CATEGORY. "
        "Вход — замаскированный текст ([PERSON_1] …), как в конвейере.",
        "- Формат: ONNX; эмбеддинги int8 (шкала на 26 чисел), веса MatMul fp16, вычисления fp32; "
        "CPU через onnxruntime, без ключа и GPU.",
        "- Обучение: дистилляция — облачная LLM сгенерировала и разметила синтетические "
        f"фрагменты ({data_note()}; всего после дедупликации "
        f"{train.get('train', 0) + train.get('val', 0)}); реальных документов и ПДн в данных нет.",
        f"- Валидация (10% синтетики): macro-F1 {best.get('macro_f1', '?')}, эпоха "
        f"{best.get('epoch', '?')}.",
        f"- Пороги (выбраны на eval/dev, {thresholds.get('criterion', '—')}): "
        f"TRADE_SECRET {thresholds.get('TRADE_SECRET', '—')}, "
        f"SPECIAL_CATEGORY {thresholds.get('SPECIAL_CATEGORY', '—')}.",
        *[f"- ⚠️ {label}: precision ≥ 0.75 на eval/dev не достигнут ни при одном пороге сетки."
          for label, ok in thresholds.get("constraint_met", {}).items() if not ok], "",
        "## Метрики на эталонных корпусах (правило совпадения run_eval)", "",
        *eval_table(_load(work / "eval" / "summary.json")), "",
        "eval/blind при обучении и выборе порогов не использовался.", "",
        "## ONNX", "",
        f"- Размер model.onnx: {check['size_mb']} МБ (fp32 — {check['fp32_size_mb']} МБ).",
        f"- Совпадение с PyTorch на {check['fragments']} фрагментах: max |Δp| fp32 "
        f"{check['max_delta_fp32']}, int8 {check['max_delta_int8']}; совпадение argmax "
        f"{check['argmax_agreement_int8']}.",
        f"- CPU (onnxruntime): {check['seconds_100']} с на 100 фрагментов, "
        f"{check['seconds_1000']} с на 1000.", "",
        "## Ограничения", "",
        "- Обучен на синтетике: реальные документы длиннее, грязнее и разнообразнее.",
        "- Сущность — весь фрагмент, а эталон — «показатель + значение»: пересечение ≥ 50% "
        "эталона засчитывается, но фрагмент с двумя фактами закроет только один эталон.",
        "- Не ищет ПДн, финансовые показатели и реквизиты — это делают regex, NER и словари.",
    ]
    (target / "card.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Экспорт классификатора в ONNX (T-036a)")
    p.add_argument("--work", type=Path, default=OUT_DIR,
                   help="папка запуска: best/, thresholds.json, eval/ (v3: ml/out/v3)")
    p.add_argument("--model", type=Path, default=None, help="по умолчанию <work>/best")
    p.add_argument("--target", type=Path, default=MODEL_DIR,
                   help="куда положить model.onnx (v3 до решения A/B: ml/out/v3)")
    p.add_argument("--card-only", action="store_true",
                   help="только пересобрать card.md (после evaluate.py --onnx)")
    args = p.parse_args(argv)
    args.model = args.model or args.work / BEST_DIR.name
    return args


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = parse_args(argv)
    if args.card_only:
        thresholds = _load(args.target / "thresholds.json")
        write_card(args.target, _load(args.work / "onnx_check.json"), thresholds, args.work)
        return 0
    fp32 = args.work / "model_fp32.onnx"
    export_fp32(args.model, fp32)
    logger.info("Сжатие: %s", quantize(fp32, args.target / "model.onnx"))
    thresholds = write_assets(args.model, args.target, args.work)
    check = verify(args.model, args.target, fp32)
    write_json(args.work / "onnx_check.json", check)
    write_card(args.target, check, thresholds, args.work)
    ok_delta, ok_size = check["max_delta_int8"] < MAX_DELTA, check["size_mb"] <= TARGET_MB
    logger.info("ONNX: %s | Δ < %.2f: %s | ≤ %d МБ: %s", check, MAX_DELTA, ok_delta,
                TARGET_MB, ok_size)
    return 0 if ok_delta and ok_size else 1


if __name__ == "__main__":
    raise SystemExit(main())
