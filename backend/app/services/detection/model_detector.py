"""Классификатор коммерческой тайны и особых категорий в конвейере. Задача T-036b, Данила.

Модель grif-cls (T-036a, data/models/grif-cls) — rubert-tiny2 в ONNX, работает на CPU без ключа
и без сети. Она только ПРЕДЛАГАЕТ фрагменты; что засчитать, решает код:

1. Фрагменты — split_fragments по masked_text (модель видит [PERSON_1], а не ФИО — как при
   обучении и выборе порогов), границы переводятся в исходный текст to_original_offset.
2. Класс с p ≥ порога из thresholds.json (пороги выбраны на eval/dev, здесь не меняются);
   прошли оба класса — берётся больший p.
3. SPECIAL_CATEGORY засчитывается, только если в этом или соседнем фрагменте есть человек
   (has_person_context): ст. 10 152-ФЗ говорит о сведениях о конкретном субъекте.
   «Поставка препаратов для лечения онкологических заболеваний» без людей — не ПДн.
4. Пересечения (иначе повторная маска упадёт): находка того же типа на этом месте → фрагмент
   отбрасывается; ПДн и другие сущности, которые остаются в отчёте, вырезаются из фрагмента
   (края обрезаются, если ПДн внутри — берётся самый длинный кусок без них, ≥ 3 слов).
   FINANCIAL под новой КТ убирается (как у LLM в T-013d): сведения важнее суммы.

Нет файлов модели или onnxruntime → одно предупреждение за процесс, шаг пропускается,
проверка идёт дальше (как mock у LLM). В лог — только счётчики (AGENTS.md, правило 4).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.schemas import Entity, EntityCategory, EntityType, ExtractedDocument, MaskResult
from app.services.detection.detect import assign_location
from app.services.detection.fragments import split_fragments
from app.services.llm.special_category import has_person_context
from app.services.masking.masker import MASKED_CATEGORIES, mask, mask_value, to_original_offset

logger = logging.getLogger(__name__)

MODEL_NAME = "grif-cls"
# Том Docker (docker-compose.yml) и каталог репозитория при локальном запуске.
MODEL_DIRS: tuple[Path, ...] = (
    Path("/data/models") / MODEL_NAME,
    Path(__file__).resolve().parents[4] / "data" / "models" / MODEL_NAME,
)
MODEL_FILE = "model.onnx"
MAX_LEN = 128                 # как при обучении (ml/common.py)
BATCH_SIZE = 32
THREADS = 2                   # intra_op: не отнимать все ядра у worker
MIN_EDGE_WORDS = 2            # после обрезки края — как минимальный фрагмент split_fragments
MIN_INNER_WORDS = 3           # ПДн внутри фрагмента — берём кусок не короче 3 слов
MODEL_TYPES: tuple[EntityType, ...] = (EntityType.TRADE_SECRET, EntityType.SPECIAL_CATEGORY)
CATEGORY = {
    EntityType.TRADE_SECRET: EntityCategory.trade_secret,
    EntityType.SPECIAL_CATEGORY: EntityCategory.pd_special,
}
_WORD_RE = re.compile(r"\w+")
_EDGE_CHARS = " \t\n,;:—–-"


@dataclass(frozen=True)
class LoadedModel:
    session: Any                       # onnxruntime.InferenceSession
    tokenizer: Any                     # tokenizers.Tokenizer
    inputs: frozenset[str]
    columns: dict[EntityType, int]     # столбец вероятности класса в выходе модели
    thresholds: dict[EntityType, float]
    version: str                       # "grif-cls-<первые 8 hex md5 model.onnx>"


@dataclass(frozen=True)
class Fragment:
    start: int                         # исходные координаты doc.text
    end: int
    masked: str                        # что видит модель


# ------------------------------------------------------------- загрузка ---


def find_model_dir() -> Path | None:
    """Первая папка из MODEL_DIRS, где есть model.onnx."""
    return next((d for d in MODEL_DIRS if (d / MODEL_FILE).is_file()), None)


@lru_cache(maxsize=1)
def load_model() -> LoadedModel | None:
    """Модель один раз на процесс. None (и одно предупреждение) — шаг классификатора пропущен."""
    model_dir = find_model_dir()
    if model_dir is None:
        logger.warning("Классификатор %s не найден (%s): шаг пропущен", MODEL_NAME,
                       ", ".join(str(d) for d in MODEL_DIRS))
        return None
    try:
        return _load(model_dir)
    except Exception as exc:  # битый файл, нет onnxruntime — проверка всё равно идёт дальше
        logger.warning("Классификатор %s не загрузился (%s): шаг пропущен",
                       MODEL_NAME, type(exc).__name__)
        return None


def _load(model_dir: Path) -> LoadedModel:
    import onnxruntime as ort
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
    tokenizer.enable_truncation(MAX_LEN)
    tokenizer.enable_padding(pad_id=tokenizer.token_to_id("[PAD]") or 0)
    options = ort.SessionOptions()
    options.intra_op_num_threads = THREADS
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    model_path = model_dir / MODEL_FILE
    session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
    labels = json.loads((model_dir / "labels.json").read_text(encoding="utf-8"))
    raw = json.loads((model_dir / "thresholds.json").read_text(encoding="utf-8"))
    md5 = hashlib.md5(model_path.read_bytes(), usedforsecurity=False).hexdigest()
    model = LoadedModel(
        session=session, tokenizer=tokenizer,
        inputs=frozenset(i.name for i in session.get_inputs()),
        columns={t: labels.index(t.value) for t in MODEL_TYPES},
        thresholds={t: float(raw[t.value]) for t in MODEL_TYPES},
        version=f"{MODEL_NAME}-{md5[:8]}",
    )
    logger.info("Классификатор загружен: %s, пороги %s", model.version,
                {t.value: v for t, v in model.thresholds.items()})
    return model


def model_version() -> str | None:
    """Версия для AuditInfo.detectors ("grif-cls-1a2b3c4d") или None, если модели нет."""
    model = load_model()
    return model.version if model is not None else None


def predict_proba(model: LoadedModel, texts: Sequence[str]) -> list[dict[EntityType, float]]:
    """Вероятности TRADE_SECRET и SPECIAL_CATEGORY для каждого фрагмента, батчами по 32."""
    import numpy as np

    result: list[dict[EntityType, float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        enc = model.tokenizer.encode_batch(list(texts[i:i + BATCH_SIZE]))
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        feeds = {
            "input_ids": ids,
            "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
            "token_type_ids": np.zeros_like(ids),
        }
        logits = model.session.run(None, {k: v for k, v in feeds.items() if k in model.inputs})[0]
        z = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = z / z.sum(axis=1, keepdims=True)
        result += [{t: float(row[c]) for t, c in model.columns.items()} for row in probs]
    return result


# ---------------------------------------------------------- фрагменты ---


def doc_fragments(masked: MaskResult) -> list[Fragment]:
    """Фрагменты masked_text с границами в исходном тексте (как doc_fragments в ml/evaluate.py)."""
    frags: list[Fragment] = []
    for ms, me in split_fragments(masked.masked_text):
        start = to_original_offset(ms, masked.spans)
        end = to_original_offset(me, masked.spans)
        if end > start:
            frags.append(Fragment(start, end, masked.masked_text[ms:me]))
    return frags


def _words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _strip(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start] in _EDGE_CHARS:
        start += 1
    while end > start and text[end - 1] in _EDGE_CHARS:
        end -= 1
    return start, end


def carve(text: str, start: int, end: int, obstacles: Sequence[Entity]) -> tuple[int, int] | None:
    """[start, end) без отрезков obstacles: края обрезаются до границ сущностей; сущность
    внутри — самый длинный свободный кусок. Короче MIN_EDGE_WORDS (один кусок) или
    MIN_INNER_WORDS (кусков несколько) слов → None."""
    blocks = sorted((max(o.start, start), min(o.end, end)) for o in obstacles
                    if o.start < end and start < o.end)
    if not blocks:
        return start, end
    pieces: list[tuple[int, int]] = []
    pos = start
    for s, e in blocks:
        if s > pos:
            pieces.append(_strip(text, pos, s))
        pos = max(pos, e)
    if pos < end:
        pieces.append(_strip(text, pos, end))
    min_words = MIN_EDGE_WORDS if len(pieces) == 1 else MIN_INNER_WORDS
    best = max(pieces, key=lambda p: p[1] - p[0], default=None)
    if best is None or _words(text[best[0]:best[1]]) < min_words:
        return None
    return best


# ------------------------------------------------------------- находки ---


def _label(probs: dict[EntityType, float], model: LoadedModel) -> tuple[EntityType, float] | None:
    passed = [(p, t) for t, p in probs.items() if p >= model.thresholds[t]]
    if not passed:
        return None
    prob, etype = max(passed)
    return etype, prob


def _place(
    text: str, frag: Fragment, etype: EntityType, kept: Sequence[Entity],
) -> tuple[int, int] | None:
    """Границы новой сущности без пересечений, которые сломали бы повторную маску."""
    if any(e.type == etype and e.start < frag.end and frag.start < e.end for e in kept):
        return None                                    # уже найдено словарём / моделью
    if etype == EntityType.TRADE_SECRET:               # FINANCIAL уберёт add_model_entities
        obstacles = [e for e in kept if e.type != EntityType.FINANCIAL]
    else:
        obstacles = list(kept)
    return carve(text, frag.start, frag.end, obstacles)


def detect_model(
    doc: ExtractedDocument, masked: MaskResult, entities: list[Entity],
) -> list[Entity]:
    """Новые сущности классификатора (detector="model") — только те, которых нет в entities.

    masked — MaskResult того же документа по тем же entities. Модели нет → [].
    Объединять с entities — через add_model_entities (убирает FINANCIAL под новой КТ).
    """
    model = load_model()
    if model is None:
        return []
    frags = doc_fragments(masked)
    if not frags:
        return []
    probs = predict_proba(model, [f.masked for f in frags])
    person = has_person_context([f.masked for f in frags], word_in_neighbors=True)
    found: list[Entity] = []
    unbound = 0
    for i, (frag, p) in enumerate(zip(frags, probs, strict=True)):
        picked = _label(p, model)
        if picked is None:
            continue
        etype, prob = picked
        if etype == EntityType.SPECIAL_CATEGORY and not person[i]:
            unbound += 1
            continue
        bounds = _place(doc.text, frag, etype, [*entities, *found])
        if bounds is not None:
            found.append(_entity(doc, etype, prob, *bounds))
    _log(doc, len(frags), found, unbound)
    return assign_location(found, doc)


def _entity(doc: ExtractedDocument, etype: EntityType, prob: float, start: int, end: int) -> Entity:
    value = doc.text[start:end]
    if CATEGORY[etype] in MASKED_CATEGORIES:
        value = mask_value(value)                      # особые категории — это ПДн (раздел 4.1)
    return Entity(type=etype, category=CATEGORY[etype], start=start, end=end, value_masked=value,
                  confidence=round(prob, 4), detector="model")


def _log(doc: ExtractedDocument, fragments: int, found: list[Entity], unbound: int) -> None:
    by_type = {t.value: sum(e.type == t for e in found) for t in MODEL_TYPES}
    logger.info("Классификатор: document_id=%s фрагментов=%d найдено=%s без человека=%d",
                doc.document_id, fragments, by_type, unbound)


def add_model_entities(entities: list[Entity], found: list[Entity]) -> list[Entity]:
    """entities + found по start; FINANCIAL, пересекающийся с новой КТ модели, убирается."""
    secrets = [e for e in found if e.type == EntityType.TRADE_SECRET]
    kept = [
        e for e in entities
        if not (e.type == EntityType.FINANCIAL
                and any(s.start < e.end and e.start < s.end for s in secrets))
    ]
    return sorted([*kept, *found], key=lambda e: (e.start, e.end))


def apply_model(
    doc: ExtractedDocument, entities: list[Entity], masked: MaskResult,
) -> tuple[list[Entity], MaskResult]:
    """Шаг конвейера и оценки: detect_model → при находках общий список и маска заново."""
    found = detect_model(doc, masked, entities)
    if not found:
        return entities, masked
    merged = add_model_entities(entities, found)
    return merged, mask(doc.text, merged)
