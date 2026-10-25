"""Тесты генерации данных (T-036a): план, промпт, постобработка, вызовы в режиме mock.

Запуск из корня репозитория: python -m pytest ml/tests -q (нужны зависимости backend).
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import pytest

os.environ["LLM_PROVIDER"] = "mock"   # никаких реальных вызовов в тестах (AGENTS.md, правило 7)
for _name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
    os.environ.pop(_name, None)

ML = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML))

import generate_data as gd  # noqa: E402
from common import ensure_app_importable  # noqa: E402

ensure_app_importable()


def test_plan_is_deterministic_and_within_budget() -> None:
    calls = gd.plan_calls()
    assert calls == gd.plan_calls()
    assert len(calls) <= 400
    assert {c.kind for c in calls} == {k.name for k in gd.CALL_KINDS}
    assert len({c.call_id for c in calls}) == len(calls)
    est = gd.estimate_cost(calls, 0.15, 0.60)
    assert est["usd"] < 2.0


def test_plan_v2_within_budget_and_prefixed() -> None:
    try:
        gd.use_plan("v2")
        calls = gd.plan_calls()
        assert len(calls) <= 400 and all(c.call_id.startswith("v2-") for c in calls)
        assert {"none_person", "none_requisites", "sc_form", "long_mix"} <= {c.kind for c in calls}
        _, template = gd.load_prompt()
        assert "$" not in gd.build_user(calls[-1], template)
        assert gd.estimate_cost(calls, 2.0, 8.0)["usd"] < 8.0
    finally:
        gd.use_plan("v1")


def test_doc_types_match_kind() -> None:
    for call in gd.plan_calls():
        if call.kind in ("trade_secret", "contrast_ts"):
            assert call.doc_type in gd.TS_DOCS
        if call.kind in ("special", "contrast_sc"):
            assert call.doc_type in gd.SC_DOCS


def test_user_prompt_is_fully_substituted() -> None:
    _, template = gd.load_prompt()
    user = gd.build_user(gd.plan_calls()[0], template)
    assert "$" not in user
    assert "20 фрагментов" in user


def test_prompt_contains_no_eval_lines() -> None:
    """Честность метрики: ни одной строки eval/seed и eval/dev в промпте генерации."""
    prompt = ""
    for name in gd.PLANS:
        gd.use_plan(name)
        system, template = gd.load_prompt()
        prompt += system + template.template
    gd.use_plan("v1")
    root = ML.parent / "eval"
    for folder in ("seed", "dev"):
        for path in (root / folder).glob("*.txt"):
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if len(line) >= 25:
                    assert line not in prompt, f"{path.name}: строка попала в промпт"


def _batch(call_id: str, items: list[tuple[str, str]], kind: str = "trade_secret") -> dict:
    meta = {"call_id": call_id, "kind": kind, "doc_type": "memo", "register": "official",
            "industry": "it", "themes": []}
    return {"meta": meta, "items": [{"text": t, "label": lab} for t, lab in items]}


def test_postprocess_filters_and_dedups() -> None:
    raw = [_batch("a", [
        ("Наценка на серверы для этого заказчика — 14%.", "TRADE_SECRET"),
        ("наценка на серверы для этого заказчика — 15 %", "TRADE_SECRET"),   # дубль (цифры → 0)
        ("Коротко.", "NONE"),                                                 # длина < 15
        ("Срок поставки серверов — 10 рабочих дней.", "MAYBE"),               # плохая метка
        ("НМЦК по лоту составляет 1 200 000 руб.", "NONE"),
        ("НМЦК по лоту составляет 1 300 000 руб.", "TRADE_SECRET"),           # конфликт меток
        ("- Договор подписал Сидоров П.П. в марте.", "NONE"),
        ("Закупка инсулина без указания конкретного пациента.", "NONE"),       # пересказ правила
        ("Особая цена для [CLIENT_4] — 90% от прайса.", "TRADE_SECRET"),      # чужой плейсхолдер
    ])]
    rows, drops = gd.postprocess(raw, lambda t: t.replace("Сидоров П.П.", "[PERSON_1]"))
    texts = {r["text"]: r["label"] for r in rows}
    assert texts == {
        "Наценка на серверы для этого заказчика — 14%.": "TRADE_SECRET",
        "Договор подписал [PERSON_1] в марте.": "NONE",
    }
    assert drops["duplicate"] == 2 and drops["length"] == 1 and drops["bad_label"] == 1
    assert drops["label_conflict"] == 1 and drops["masked_pd"] == 1
    assert drops["meta_phrase"] == 1 and drops["unknown_placeholder"] == 1


def test_unknown_placeholder() -> None:
    assert gd.has_unknown_placeholder("Национальность: [PERSON_17_NATIONALITY].")
    assert gd.has_unknown_placeholder("Цена для [CLIENT_4]")
    assert not gd.has_unknown_placeholder("[PERSON_1], тел. [PHONE_1], карта [CARD_2]")


def test_postprocess_empty() -> None:
    assert gd.postprocess([], lambda t: t) == ([], {"label_conflict": 0})


def test_app_masker_hides_names_but_keeps_special() -> None:
    masker = gd.make_person_masker()
    out = masker("Иванов Иван Петрович перенёс операцию на сердце в марте.")
    assert "Иванов" not in out and "[PERSON_1]" in out
    assert "операцию на сердце" in out


def test_review_sample_and_stats(tmp_path: Path) -> None:
    rows = [{"text": f"Фрагмент номер {i} | с чертой", "label": "NONE",
             "meta": _batch(f"c{i}", [])["meta"]} for i in range(5)]
    path = tmp_path / "s.md"
    gd.write_review_sample(rows, path)
    content = path.read_text(encoding="utf-8")
    assert content.count("| NONE |") == 5 and "\\|" in content
    stats = gd.build_stats(rows, {}, gd.Usage(), (0.15, 0.6))
    assert stats["fragments"] == 5 and stats["by_label"] == {"NONE": 5}


def test_usage_handler_counts_tokens() -> None:
    usage = gd.Usage()
    handler = gd.UsageHandler(usage)
    msg = "LLM вызов: %s %s %d %.0f %s %s %s %s %d"
    for hit in (False, True):
        handler.emit(logging.LogRecord("x", logging.INFO, "", 0, msg,
                                       ("p", "m", 10, 1.0, 100, 50, 150, hit, 1), None))
    assert (usage.calls, usage.cache_hits, usage.prompt_tokens, usage.completion_tokens) == (
        1, 1, 100, 50)


@pytest.fixture
def mock_llm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    from app.services.llm.client import reset_mock_responses, set_mock_response

    monkeypatch.setattr(gd, "CHECKPOINT_FILE", tmp_path / "gen_raw.jsonl")
    set_mock_response(gd.GenBatch, lambda: {"items": [
        {"text": "Себестоимость одного стола для нас — 8 400 руб.", "label": "TRADE_SECRET"},
        {"text": "[PERSON_1] с прошлого года на учёте у кардиолога.", "label": "SPECIAL_CATEGORY"},
    ]})
    yield tmp_path
    reset_mock_responses()


def test_run_calls_with_mock_and_resume(mock_llm: Path) -> None:
    args = argparse.Namespace(max_calls=10, max_usd=2.0, price_in=0.15, price_out=0.6)
    calls = gd.plan_calls()[:3]
    done = gd.run_calls(calls, args, gd.Usage())
    assert set(done) == {c.call_id for c in calls}
    lines = (mock_llm / "gen_raw.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    gd.run_calls(calls, args, gd.Usage())                       # повтор — ничего не дописывает
    assert len((mock_llm / "gen_raw.jsonl").read_text(encoding="utf-8").splitlines()) == 3
    rows, _ = gd.postprocess(list(done.values()), lambda t: t)
    assert len(rows) == 2                                        # одинаковые ответы → дубли


def test_main_dry_run_and_over_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    assert gd.main(["--dry-run"]) == 0
    assert gd.main(["--dry-run", "--max-calls", "10"]) == 3
