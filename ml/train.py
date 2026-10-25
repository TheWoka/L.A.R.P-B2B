"""Обучение классификатора фрагментов (rubert-tiny2, 3 класса). Задача T-036a.

Данные — data/train/fragments.jsonl (синтетика generate_data.py). 10% — валидация,
стратифицированно, seed фиксирован. Взвешенная cross-entropy (классы несбалансированы),
лучшая эпоха по macro-F1 на валидации → ml/out/best/ (save_pretrained, в .gitignore).
eval/* в обучении не участвует никак.

Запуск (из корня репозитория, venv с ml/requirements-ml.txt):
    python ml/train.py                       # CPU ≈ 10–20 мин; GPU подхватится сам
    python ml/train.py --epochs 3 --lr 5e-5  # параметры по умолчанию — в parse_args
"""

import argparse
import logging
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import classification_report, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    get_linear_schedule_with_warmup,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    BASE_MODEL,
    BEST_DIR,
    LABELS,
    MAX_LEN,
    OUT_DIR,
    SEED,
    dedup_key,
    default_train_files,
    read_jsonl,
    setup_logging,
    write_json,
)

logger = logging.getLogger("train")


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def load_rows(paths: list[Path]) -> list[dict]:
    """Объединяет наборы синтетики; дубли (dedup_key) убираются, конфликтующие метки — тоже."""
    by_key: dict[str, dict] = {}
    labels: dict[str, set[str]] = {}
    for path in paths:
        for r in read_jsonl(path):
            if r["label"] not in LABELS:
                continue
            key = dedup_key(r["text"])
            labels.setdefault(key, set()).add(r["label"])
            by_key.setdefault(key, r)
    return [r for k, r in by_key.items() if len(labels[k]) == 1]


def load_split(paths: list[Path], val_share: float, seed: int) -> tuple[list[dict], list[dict]]:
    """Наборы синтетики → (train, val), стратифицированно по метке."""
    rows = load_rows(paths)
    labels = [r["label"] for r in rows]
    train, val = train_test_split(rows, test_size=val_share, random_state=seed, stratify=labels)
    return train, val


def class_weights(rows: list[dict]) -> torch.Tensor:
    """w_c = N / (K · n_c): редкий класс весит больше."""
    counts = np.array([sum(r["label"] == lab for r in rows) for lab in LABELS], dtype=float)
    counts = np.maximum(counts, 1.0)
    return torch.tensor(counts.sum() / (len(LABELS) * counts), dtype=torch.float)


def make_loader(rows: list[dict], tokenizer, batch: int, shuffle: bool) -> DataLoader:  # noqa: ANN001
    label_id = {lab: i for i, lab in enumerate(LABELS)}

    def collate(items: list[dict]) -> dict[str, torch.Tensor]:
        enc = tokenizer([r["text"] for r in items], truncation=True, max_length=MAX_LEN,
                        padding=True, return_tensors="pt")
        enc["labels"] = torch.tensor([label_id[r["label"]] for r in items])
        return dict(enc)

    gen = torch.Generator().manual_seed(SEED)
    return DataLoader(rows, batch_size=batch, shuffle=shuffle, collate_fn=collate, generator=gen)


@torch.no_grad()
def predict(model, loader: DataLoader, device: torch.device) -> tuple[np.ndarray, np.ndarray]:  # noqa: ANN001
    """Вероятности классов и истинные метки по всему загрузчику."""
    model.eval()
    probs, gold = [], []
    for batch in loader:
        labels = batch.pop("labels")
        logits = model(**{k: v.to(device) for k, v in batch.items()}).logits
        probs.append(torch.softmax(logits, dim=-1).cpu().numpy())
        gold.append(labels.numpy())
    return np.concatenate(probs), np.concatenate(gold)


def val_metrics(probs: np.ndarray, gold: np.ndarray) -> dict:
    pred = probs.argmax(axis=1)
    report = classification_report(gold, pred, labels=list(range(len(LABELS))),
                                   target_names=list(LABELS), output_dict=True, zero_division=0)
    return {
        "macro_f1": round(float(f1_score(gold, pred, average="macro")), 4),
        "by_class": {lab: {k: round(report[lab][k], 4) for k in ("precision", "recall", "f1-score")}
                     for lab in LABELS},
        "confusion": confusion_matrix(gold, pred, labels=list(range(len(LABELS)))).tolist(),
    }


def train_epoch(model, loader, optimizer, scheduler, loss_fn, device) -> float:  # noqa: ANN001
    model.train()
    total = 0.0
    for batch in loader:
        labels = batch.pop("labels").to(device)
        logits = model(**{k: v.to(device) for k, v in batch.items()}).logits
        loss = loss_fn(logits, labels)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        scheduler.step()
        optimizer.zero_grad()
        total += loss.item()
    return total / max(len(loader), 1)


def build_model(base: str, device: torch.device):  # noqa: ANN201
    tokenizer = AutoTokenizer.from_pretrained(base)
    model = AutoModelForSequenceClassification.from_pretrained(
        base, num_labels=len(LABELS),
        id2label=dict(enumerate(LABELS)), label2id={lab: i for i, lab in enumerate(LABELS)},
    )
    return tokenizer, model.to(device)


def fit(args: argparse.Namespace) -> dict:
    """Полный цикл обучения; возвращает метрики всех эпох и лучшей."""
    set_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    train_rows, val_rows = load_split(args.data, args.val_share, args.seed)
    tokenizer, model = build_model(args.base, device)
    train_loader = make_loader(train_rows, tokenizer, args.batch, shuffle=True)
    val_loader = make_loader(val_rows, tokenizer, 128, shuffle=False)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    steps = len(train_loader) * args.epochs
    scheduler = get_linear_schedule_with_warmup(optimizer, int(0.1 * steps), steps)
    weights = class_weights(train_rows)
    loss_fn = torch.nn.CrossEntropyLoss(weight=weights.to(device))
    history, best = [], {"macro_f1": -1.0}
    logger.info("device=%s train=%d val=%d weights=%s", device, len(train_rows), len(val_rows),
                [round(w, 3) for w in weights.tolist()])
    for epoch in range(1, args.epochs + 1):
        started = time.perf_counter()
        loss = train_epoch(model, train_loader, optimizer, scheduler, loss_fn, device)
        metrics = {"epoch": epoch, "train_loss": round(loss, 4),
                   **val_metrics(*predict(model, val_loader, device)),
                   "seconds": round(time.perf_counter() - started, 1)}
        history.append(metrics)
        logger.info("эпоха %d: loss=%.4f macro-F1=%.4f (%.0f с)", epoch, loss,
                    metrics["macro_f1"], metrics["seconds"])
        if metrics["macro_f1"] > best["macro_f1"]:
            best = metrics
            args.out.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(args.out)
            tokenizer.save_pretrained(args.out)
    return {"base_model": args.base, "device": str(device), "train": len(train_rows),
            "val": len(val_rows), "class_weights": [round(w, 4) for w in weights.tolist()],
            "params": vars_for_json(args), "history": history, "best": best}


def vars_for_json(args: argparse.Namespace) -> dict:
    def plain(v: object) -> object:
        if isinstance(v, Path):
            return str(v)
        if isinstance(v, list):
            return [plain(x) for x in v]
        return v

    return {k: plain(v) for k, v in vars(args).items()}


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Обучение классификатора фрагментов (T-036a)")
    p.add_argument("--data", type=Path, nargs="+", default=default_train_files(),
                   help="один или несколько fragments.jsonl (по умолчанию v1 + v2)")
    p.add_argument("--base", default=BASE_MODEL, help="модель HF или локальная папка")
    p.add_argument("--out", type=Path, default=BEST_DIR)
    p.add_argument("--epochs", type=int, default=4)
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--lr", type=float, default=5e-5)
    p.add_argument("--val-share", type=float, default=0.1)
    p.add_argument("--seed", type=int, default=SEED)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = parse_args(argv)
    result = fit(args)
    write_json(OUT_DIR / "train_metrics.json", result)
    best = result["best"]
    logger.info("Лучшая эпоха %d: macro-F1=%.4f %s → %s", best["epoch"], best["macro_f1"],
                {k: v["f1-score"] for k, v in best["by_class"].items()}, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
