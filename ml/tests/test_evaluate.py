"""Тесты сборки сущностей классификатора (T-036a) на фиктивном предсказателе — без весов."""

import os
import sys
from pathlib import Path

import numpy as np
import pytest

os.environ["LLM_PROVIDER"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evaluate as ev  # noqa: E402
from app.evaluation.corpus import CorpusDocument, text_document  # noqa: E402
from app.evaluation.markup import parse_markup  # noqa: E402
from common import LABELS, refuse_blind  # noqa: E402


class KeywordPredictor:
    """«Модель»: TRADE_SECRET, если во фрагменте есть «накрутк», SPECIAL — если «операц»."""

    name = "fake"

    def __init__(self) -> None:
        self.seen: list[str] = []

    def predict_proba(self, texts: list[str]) -> np.ndarray:
        self.seen += texts
        out = np.tile(np.array([0.9, 0.05, 0.05]), (len(texts), 1))
        for i, t in enumerate(texts):
            if "накрутк" in t.lower():
                out[i] = [0.1, 0.85, 0.05]
            elif "операц" in t.lower():
                out[i] = [0.1, 0.05, 0.85]
        return out


RAW = (
    "Письмо от [[PERSON:Сидорова Петра Ильича]] по поставке мебели.\n"
    "Сообщаем, что [[TRADE_SECRET:накрутку на столы держим около семнадцати процентов]].\n"
    "[[PERSON:Сидоров П.И.]] [[SPECIAL_CATEGORY:в марте перенёс операцию на позвоночнике]].\n"
    "Срок поставки — 10 рабочих дней с даты заявки.\n"
)


@pytest.fixture
def cache() -> ev.DocCache:
    clean, gold = parse_markup(RAW, "t.txt")
    item = CorpusDocument("t.txt", text_document(clean, "t.txt"), gold)
    pred = KeywordPredictor()
    (c,) = ev.build_cache([item], pred)
    c.seen = pred.seen  # type: ignore[attr-defined]
    return c


def test_fragments_map_to_original_and_model_sees_masked(cache: ev.DocCache) -> None:
    text = cache.item.doc.text
    for frag in cache.fragments:
        assert text[frag.start:frag.end].strip() == text[frag.start:frag.end]
    seen = " ".join(cache.seen)  # type: ignore[attr-defined]
    assert "Сидоров" not in seen and "[PERSON_1]" in seen


def test_model_entities_match_gold(cache: ev.DocCache) -> None:
    thresholds = {"TRADE_SECRET": 0.5, "SPECIAL_CATEGORY": 0.5}
    found = ev.model_entities(cache, thresholds)
    types = sorted(e.type.value for e in found)
    assert types == ["SPECIAL_CATEGORY", "TRADE_SECRET"]
    for e in found:
        assert e.detector == "model" and len(e.value_masked) == e.end - e.start
        assert "Сидоров" not in e.value_masked
    result = ev.run([cache], thresholds)
    from app.schemas import EntityType
    assert result.counts[EntityType.TRADE_SECRET].tp == 1
    assert result.counts[EntityType.SPECIAL_CATEGORY].tp == 1


def test_threshold_above_probability_disables_class(cache: ev.DocCache) -> None:
    assert ev.model_entities(cache, {"TRADE_SECRET": 0.9, "SPECIAL_CATEGORY": 0.9}) == []
    assert ev.model_entities(cache, {}) == []


def test_skip_when_same_type_already_found(cache: ev.DocCache) -> None:
    ts = [f for f, p in zip(cache.fragments, cache.probs, strict=True)
          if p[LABELS.index("TRADE_SECRET")] > 0.5][0]
    from app.schemas import Entity, EntityCategory, EntityType
    cache.base.append(Entity(type=EntityType.TRADE_SECRET, category=EntityCategory.trade_secret,
                             start=ts.start + 2, end=ts.start + 8, value_masked="x" * 6,
                             confidence=1.0, detector="dict"))
    found = ev.model_entities(cache, {"TRADE_SECRET": 0.5})
    assert found == []


def test_pick_threshold_respects_precision(cache: ev.DocCache) -> None:
    picked = ev.pick_threshold([cache], "TRADE_SECRET")
    assert picked["constraint_met"] is True
    assert picked["threshold"] in ev.GRID and len(picked["curve"]) == len(ev.GRID)


def test_refuse_blind() -> None:
    with pytest.raises(SystemExit):
        refuse_blind(Path("eval/blind"))
    with pytest.raises(SystemExit):
        refuse_blind(Path("eval/results/blind-llm"))
    refuse_blind(Path("eval/dev"))
