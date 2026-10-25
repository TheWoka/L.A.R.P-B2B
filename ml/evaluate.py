"""Честная офлайн-оценка «словари + классификатор» на eval/dev и eval/seed. Задача T-036a.

Тот же evaluate_corpus и то же правило совпадения, что run_eval (type + пересечение ≥ 50%
длины ЭТАЛОНА). Детектор = detect() (словари, как сейчас) + классификатор:
    detect → mask (как в конвейере, [PERSON_1]) → split_fragments(masked_text) →
    вероятности классов → фрагмент с p ≥ порога класса становится Entity(detector="model")
    в исходных координатах, если не пересекается с уже найденной сущностью того же типа.
Фрагмент с ПДн внутри для SPECIAL_CATEGORY допустим (сущности могут пересекаться).

Порог каждого класса выбирается ТОЛЬКО на eval/dev (сетка 0.30…0.90, максимум F1 при
precision ≥ 0.75); eval/seed — второй контроль; eval/blind не открывается (refuse_blind).

Запуск (из корня репозитория, venv с ml/requirements-ml.txt и backend/requirements.txt):
    python ml/evaluate.py                    # пороги на dev, таблица dev/seed, скорость
    python ml/evaluate.py --onnx             # + вариант сжатой ONNX (после export_onnx.py)
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    BEST_DIR,
    EVAL_SUMMARY_FILE,
    LABELS,
    MODEL_DIR,
    OUT_DIR,
    POSITIVE_LABELS,
    REPO_ROOT,
    THRESHOLDS_FILE,
    ensure_app_importable,
    refuse_blind,
    setup_logging,
    write_json,
)

ensure_app_importable()

from app.evaluation.corpus import CorpusDocument, load_corpus  # noqa: E402
from app.evaluation.markup import CATEGORY_BY_TYPE  # noqa: E402
from app.evaluation.matching import Counts, f1, precision, recall  # noqa: E402
from app.evaluation.report import build_metrics, evaluate_corpus, write_results  # noqa: E402
from app.schemas import Entity, EntityCategory, EntityType  # noqa: E402
from app.services.detection.detect import detect  # noqa: E402
from app.services.detection.fragments import split_fragments  # noqa: E402
from app.services.masking.masker import (  # noqa: E402
    MASKED_CATEGORIES,
    mask,
    mask_value,
    to_original_offset,
)
from predictors import OnnxPredictor, Predictor, TorchPredictor  # noqa: E402

logger = logging.getLogger("evaluate")

GRID = [round(x, 2) for x in np.arange(0.30, 0.901, 0.05)]
MIN_PRECISION = 0.75
DISABLED = 2.0                       # порог выше любой вероятности — класс выключен
REPORT_TYPES = ("TRADE_SECRET", "SPECIAL_CATEGORY", "PERSON")


@dataclass
class Fragment:
    start: int                       # координаты в исходном doc.text
    end: int
    masked: str                      # что видит модель


@dataclass
class DocCache:
    item: CorpusDocument
    base: list[Entity]               # detect() — словари, regex, NER
    fragments: list[Fragment]
    probs: np.ndarray                # [n_fragments, 3] в порядке LABELS


def doc_fragments(text: str, base: list[Entity]) -> list[Fragment]:
    """Фрагменты замаскированного текста с границами, переведёнными в исходный текст."""
    result = mask(text, base)
    frags = []
    for ms, me in split_fragments(result.masked_text):
        start = to_original_offset(ms, result.spans)
        end = to_original_offset(me, result.spans)
        if end > start:
            frags.append(Fragment(start, end, result.masked_text[ms:me]))
    return frags


def build_cache(docs: list[CorpusDocument], predictor: Predictor) -> list[DocCache]:
    caches = []
    for item in docs:
        base = detect(item.doc)
        frags = doc_fragments(item.doc.text, base)
        probs = predictor.predict_proba([f.masked for f in frags])
        caches.append(DocCache(item, base, frags, probs))
    return caches


def masked_value(text: str, start: int, end: int, base: list[Entity]) -> str:
    """value_masked фрагмента: ПДн внутри него закрыты с сохранением длины."""
    chars = list(text[start:end])
    for e in base:
        if e.category in MASKED_CATEGORIES and e.start < end and e.end > start:
            s, t = max(e.start, start), min(e.end, end)
            chars[s - start:t - start] = mask_value(text[s:t])
    return "".join(chars)


def model_entities(cache: DocCache, thresholds: dict[str, float]) -> list[Entity]:
    """Фрагменты с p ≥ порога → Entity(detector="model"); при двух классах — больший p."""
    text, found = cache.item.doc.text, []
    for frag, p in zip(cache.fragments, cache.probs, strict=True):
        passed = [(p[LABELS.index(c)], c) for c in POSITIVE_LABELS
                  if p[LABELS.index(c)] >= thresholds.get(c, DISABLED)]
        if not passed:
            continue
        prob, label = max(passed)
        etype = EntityType(label)
        if any(e.type == etype and e.start < frag.end and e.end > frag.start for e in cache.base):
            continue
        category = CATEGORY_BY_TYPE[etype]
        value = masked_value(text, frag.start, frag.end, cache.base)
        if category == EntityCategory.pd_special:
            value = mask_value(text[frag.start:frag.end])
        found.append(Entity(type=etype, category=category, start=frag.start, end=frag.end,
                            value_masked=value, confidence=round(float(prob), 4),
                            detector="model"))
    return found


def run(caches: list[DocCache], thresholds: dict[str, float] | None):  # noqa: ANN201
    """evaluate_corpus с детектором «base (+ модель)»; thresholds=None → только словари."""
    by_name = {c.item.name: c for c in caches}

    def detector(doc) -> list[Entity]:  # noqa: ANN001
        cache = by_name[doc.filename]
        extra = model_entities(cache, thresholds) if thresholds else []
        return [*cache.base, *extra]

    return evaluate_corpus([c.item for c in caches], detector)


def pick_threshold(caches: list[DocCache], label: str) -> dict:
    """Сетка порогов для одного класса на dev: максимум F1 при precision ≥ MIN_PRECISION."""
    curve = []
    for t in GRID:
        c = run(caches, {label: t}).counts.get(EntityType(label), Counts())
        curve.append({"t": t, "precision": round(precision(c), 4), "recall": round(recall(c), 4),
                      "f1": round(f1(c), 4), "tp": c.tp, "fp": c.fp, "fn": c.fn})
    ok = [r for r in curve if r["precision"] >= MIN_PRECISION]
    pool = ok or curve
    best = max(pool, key=lambda r: (r["f1"], r["t"]))   # при равенстве — порог строже
    return {"threshold": best["t"], "constraint_met": bool(ok), "curve": curve}


def summarize(result, corpus: str, variant: str) -> dict:  # noqa: ANN001
    metrics = build_metrics(result, corpus=f"{corpus} · {variant}")
    by_type = {m.type: m for m in metrics.by_type}
    row = {"corpus": corpus, "variant": variant, "pd_recall": metrics.pd_recall,
           "trade_secret_recall": metrics.trade_secret_recall}
    for t in REPORT_TYPES:
        m = by_type.get(t)
        row[t] = ({"precision": m.precision, "recall": m.recall, "f1": m.f1,
                   "support": m.support} if m else None)
    return {"row": row, "metrics": metrics}


def speed(predictor: Predictor, texts: list[str], n: int = 1000) -> dict:
    """Время на n фрагментов на CPU (тексты dev+seed по кругу, после прогрева)."""
    batch = (texts * (n // max(len(texts), 1) + 1))[:n]
    predictor.predict_proba(batch[:64])
    started = time.perf_counter()
    predictor.predict_proba(batch)
    sec = time.perf_counter() - started
    return {"backend": predictor.name, "fragments": n, "seconds": round(sec, 3),
            "seconds_per_100": round(sec / n * 100, 3)}


def fmt(m: dict | None) -> str:
    return "—" if m is None else f"{m['precision']:.2f} / {m['recall']:.2f} ({m['support']})"


def markdown_table(rows: list[dict]) -> str:
    head = ("| Корпус | Вариант | КТ P / R (эталонов) | Особые P / R | PERSON P / R "
            "| Recall ПДн | Recall КТ |\n|---|---|---|---|---|---|---|")
    lines = [head]
    for r in rows:
        lines.append(
            f"| {r['corpus']} | {r['variant']} | {fmt(r['TRADE_SECRET'])} "
            f"| {fmt(r['SPECIAL_CATEGORY'])} | {fmt(r['PERSON'])} "
            f"| {r['pd_recall']:.3f} | {r['trade_secret_recall']:.3f} |"
        )
    return "\n".join(lines)


def select_thresholds(dev: list[DocCache], args: argparse.Namespace) -> dict:
    """Пороги: из файла (--thresholds) или подбором на dev; результат → ml/out/thresholds.json."""
    if args.thresholds:
        return json.loads(args.thresholds.read_text(encoding="utf-8"))
    picked = {label: pick_threshold(dev, label) for label in POSITIVE_LABELS}
    data = {
        **{label: p["threshold"] for label, p in picked.items()},
        "selected_on": "eval/dev", "grid": GRID,
        "criterion": f"максимум F1 при precision ≥ {MIN_PRECISION}",
        "constraint_met": {label: p["constraint_met"] for label, p in picked.items()},
        "curves": {label: p["curve"] for label, p in picked.items()},
    }
    write_json(THRESHOLDS_FILE, data)
    return data


def evaluate_variants(corpora: dict[str, list[DocCache]], thresholds: dict[str, float],
                      variant: str, out: Path, with_base: bool) -> list[dict]:
    rows = []
    for name, caches in corpora.items():
        plan = ([("словари", None)] if with_base else []) + [(variant, thresholds)]
        for label, thr in plan:
            result = run(caches, thr)
            s = summarize(result, name, label)
            write_results(s["metrics"], result.errors, out / f"{name}-{label.replace(' ', '_')}")
            if thr:
                s["row"]["model_entities"] = sum(len(model_entities(c, thr)) for c in caches)
            rows.append(s["row"])
    return rows


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Офлайн-оценка словари + классификатор (T-036a)")
    p.add_argument("--dev", type=Path, default=REPO_ROOT / "eval" / "dev")
    p.add_argument("--seed", type=Path, default=REPO_ROOT / "eval" / "seed")
    p.add_argument("--model", type=Path, default=BEST_DIR, help="папка save_pretrained")
    p.add_argument("--onnx", action="store_true", help="добавить вариант сжатой ONNX")
    p.add_argument("--onnx-dir", type=Path, default=MODEL_DIR)
    p.add_argument("--thresholds", type=Path, help="взять пороги из файла, а не подбирать")
    p.add_argument("--out", type=Path, default=OUT_DIR / "eval")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging()
    for path in (args.dev, args.seed):
        refuse_blind(path)
    docs = {"dev": load_corpus(args.dev), "seed": load_corpus(args.seed)}
    torch_pred = TorchPredictor(args.model)
    corpora = {name: build_cache(d, torch_pred) for name, d in docs.items()}
    thr_data = select_thresholds(corpora["dev"], args)
    thresholds = {label: float(thr_data[label]) for label in POSITIVE_LABELS}
    logger.info("Пороги (выбраны на dev): %s; условие precision выполнено: %s",
                thresholds, thr_data.get("constraint_met"))
    rows = evaluate_variants(corpora, thresholds, "словари + модель", args.out, with_base=True)
    texts = [f.masked for caches in corpora.values() for c in caches for f in c.fragments]
    speeds = [speed(torch_pred, texts)]
    if args.onnx:
        onnx_pred = OnnxPredictor(args.onnx_dir)
        onnx_corpora = {name: build_cache(d, onnx_pred) for name, d in docs.items()}
        rows += evaluate_variants(onnx_corpora, thresholds, "словари + модель ONNX",
                                  args.out, with_base=False)
        speeds.append(speed(onnx_pred, texts))
    rows.sort(key=lambda r: (r["corpus"], r["variant"]))
    table = markdown_table(rows)
    summary = {"thresholds": thresholds, "constraint_met": thr_data.get("constraint_met"),
               "rows": rows, "speed": speeds, "fragments": {
                   name: sum(len(c.fragments) for c in caches) for name, caches in corpora.items()}}
    write_json(EVAL_SUMMARY_FILE, summary)
    EVAL_SUMMARY_FILE.with_suffix(".md").write_text(table + "\n", encoding="utf-8")
    logger.info("\n%s\n\nСкорость: %s\nИтог: %s", table, speeds, EVAL_SUMMARY_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
