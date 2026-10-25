"""Общее для скриптов ml/ (T-036a): пути, метки классов, доступ к пакету app, защита blind."""

import importlib.util
import json
import logging
import re
import sys
from pathlib import Path

ML_DIR = Path(__file__).resolve().parent
REPO_ROOT = ML_DIR.parent
OUT_DIR = ML_DIR / "out"                          # веса до экспорта, в .gitignore
TRAIN_DIR = REPO_ROOT / "data" / "train"          # синтетика, коммитится
MODEL_DIR = REPO_ROOT / "data" / "models" / "grif-cls"
BEST_DIR = OUT_DIR / "best"                       # лучшая эпоха (save_pretrained)
THRESHOLDS_FILE = OUT_DIR / "thresholds.json"     # пороги, выбранные на eval/dev
EVAL_SUMMARY_FILE = OUT_DIR / "eval" / "summary.json"

BASE_MODEL = "cointegrated/rubert-tiny2"
LABELS: tuple[str, ...] = ("NONE", "TRADE_SECRET", "SPECIAL_CATEGORY")
POSITIVE_LABELS: tuple[str, ...] = ("TRADE_SECRET", "SPECIAL_CATEGORY")
MAX_LEN = 128
SEED = 42


def ensure_app_importable() -> None:
    """Добавляет в sys.path пакет app: backend/ рядом с ml/ или /app в контейнере api."""
    if importlib.util.find_spec("app") is not None:
        return
    for candidate in (REPO_ROOT / "backend", Path("/app"), Path.cwd()):
        if (candidate / "app" / "__init__.py").is_file():
            sys.path.insert(0, str(candidate))
            return
    raise SystemExit("Не найден пакет app: запускайте из корня репозитория или из контейнера api")


def refuse_blind(path: Path) -> None:
    """eval/blind и результаты blind в T-036a не открываем вообще (честность метрики)."""
    parts = {p.lower() for p in path.resolve().parts}
    if any(p == "blind" or p.startswith("blind") for p in parts):
        raise SystemExit(f"{path}: blind в T-036a не используется — его считаем один раз 27.10")


_SPACES = re.compile(r"\s+")
_NORM_DROP = re.compile(r"[^\w\[\] ]+")


def dedup_key(text: str) -> str:
    """Нормализация для дедупликации: регистр, ё→е, цифры→0, без пунктуации."""
    low = re.sub(r"\d", "0", text.lower().replace("ё", "е"))
    return _SPACES.sub(" ", _NORM_DROP.sub(" ", low)).strip()


def default_train_files() -> list[Path]:
    """Все наборы синтетики: v1 (data/train) и, если есть, v2 (data/train/v2)."""
    return [p for p in (TRAIN_DIR / "fragments.jsonl", TRAIN_DIR / "v2" / "fragments.jsonl")
            if p.is_file()]


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def setup_logging() -> None:
    """INFO в консоль; на Windows (cp1251) — UTF-8, чтобы не падать на «≥», «█», «✅»."""
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("app", "pymorphy2", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
