"""Проверки T-011b: суммы, воспроизводимость и реальные смещения четырёх форматов."""

import json
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest
from docx import Document

from app.evaluation.corpus import load_corpus
from app.evaluation.generator import TEMPLATES, make_document, read_for_markup, values
from app.evaluation.generator.build import GENERATORS, _markup, render_template
from app.evaluation.generator.templates import MARKERS, TRAPS, Template, paragraph
from app.schemas import DocPurpose, EntityType
from app.services.detection import validators


@pytest.mark.parametrize("name", GENERATORS)
def test_values_one_hundred(name: str) -> None:
    generator, _ = GENERATORS[name]
    rng = Random(42)
    samples = [generator(rng) for _ in range(100)]
    assert all(isinstance(value, str) and value.strip() for value in samples)
    assert len(set(samples)) > 1
    other = Random(42)
    assert samples == [generator(other) for _ in range(100)]
    for value in samples:
        if name == "snils":
            assert validators.is_valid_snils(value)
        elif name == "inn_person":
            assert len(value) == 12 and validators.is_valid_inn(value)
        elif name == "card":
            assert validators.is_valid_card(value)
        elif name == "bank_account":
            assert len(value) == 20 and value.startswith("40817")
            assert validators.is_valid_account_key(value, values.BIK)
        elif name == "phone":
            assert validators.is_valid_ru_phone(value)
        elif name == "passport":
            digits = re.sub(r"\D", "", value)
            assert len(digits) == 10 and validators.is_valid_passport_series(digits[:4])
        elif name == "email":
            assert value.endswith("@example.invalid")
        elif name == "address":
            assert all(part in value for part in ("г. ", "ул. ", "д. ", "кв. "))


def test_person_gender_and_variants() -> None:
    assert all(len(items) == 30 for items in (
        values.SURNAMES, values.MALE_NAMES, values.FEMALE_NAMES,
        values.MALE_PATRONYMICS, values.FEMALE_PATRONYMICS,
    ))
    rng = Random(71)
    forms: set[str] = set()
    genders: set[bool] = set()
    for _ in range(1000):
        value = values.person(rng)
        surname, name, patronymic = value.split()
        female = surname.lower().endswith("а")
        genders.add(female)
        if "." in value:
            forms.add("initials")
            assert len(name) == len(patronymic) == 2
        else:
            forms.add("upper" if value.isupper() else "full")
            assert name.title() in (values.FEMALE_NAMES if female else values.MALE_NAMES)
            assert patronymic.title() in (
                values.FEMALE_PATRONYMICS if female else values.MALE_PATRONYMICS
            )
    assert forms == {"initials", "upper", "full"}
    assert genders == {True, False}


def test_value_formats_and_dates() -> None:
    rng = Random(42)
    passports = [values.passport(rng) for _ in range(100)]
    assert any(value.startswith("серия ") for value in passports)
    assert {len(re.sub(r"\d", "", value)) for value in passports} >= {1, 2}
    snils = [values.snils(rng) for _ in range(100)]
    assert {"-" in value for value in snils} == {True, False}
    dates = [values.birth_date(rng) for _ in range(100)]
    assert {" г." in value for value in dates} == {True, False}
    for value in dates:
        if " г." in value:
            day, month_name, year, _ = value.split()
            month = values.MONTHS.index(month_name) + 1
        else:
            day, month, year = value.split(".")
        assert validators.is_valid_birth_date(int(day), int(month), int(year))


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.name)
@pytest.mark.parametrize("seed", [0, 42, 101])
def test_template_offsets_and_loader(template: Template, seed: int, tmp_path: Path) -> None:
    _, expected = render_template(template, Random(seed))
    path = make_document(template, Random(seed), tmp_path, "sample")
    doc = read_for_markup(path)
    payload = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert payload["purpose"] == template.purpose.value
    assert len(payload["entities"]) == len(expected)
    for entity, value in zip(payload["entities"], expected, strict=True):
        assert entity["type"] == value.type.value
        assert doc.text[entity["start"]:entity["end"]] == value.value
    loaded = load_corpus(tmp_path)
    assert len(loaded) == 1 and loaded[0].doc == doc
    assert len(loaded[0].gold) == len(expected)
    if template.format == "docx":
        assert len(Document(str(path)).tables) == 1
    if template.uppercase:
        assert doc.text == doc.text.upper()


@pytest.mark.parametrize("format_name", ["docx", "pdf", "xlsx", "txt"])
def test_all_four_formats_roundtrip(format_name: str, tmp_path: Path) -> None:
    template = replace(TEMPLATES[3], format=format_name)
    for seed in (0, 42, 101):
        _, expected = render_template(template, Random(seed))
        folder = tmp_path / str(seed)
        path = make_document(template, Random(seed), folder, "roundtrip")
        # В том числе PDF: не ослабляем точное совпадение из-за кириллицы или переносов.
        doc = read_for_markup(path)
        gold = load_corpus(folder)[0]
        assert gold.doc == doc
        for entity, item in zip(gold.gold, expected, strict=True):
            assert doc.text[entity.start:entity.end] == item.value


def test_json_reproducible_and_seed_changes(tmp_path: Path) -> None:
    results: list[list[bytes]] = []
    for run, seed in enumerate((42, 42, 43)):
        rng = Random(seed)
        folder = tmp_path / str(run)
        for index, template in enumerate(TEMPLATES):
            make_document(template, rng, folder, f"sample_{index}")
        results.append([path.read_bytes() for path in sorted(folder.glob("*.json"))])
    assert results[0] == results[1]
    assert results[0] != results[2]


def test_traps_are_not_gold(tmp_path: Path) -> None:
    for template in (TEMPLATES[0], TEMPLATES[1], TEMPLATES[4], TEMPLATES[5]):
        folder = tmp_path / template.name
        path = make_document(template, Random(42), folder, "traps")
        item = load_corpus(folder)[0]
        start = item.doc.text.index(TRAPS)
        assert not any(e.start < start + len(TRAPS) and start < e.end for e in item.gold)
        assert all(e.type != EntityType.FINANCIAL for e in item.gold)
        assert path.exists()
    assert not load_corpus(tmp_path / "specification")[0].gold


def test_markers_present_and_absent() -> None:
    observed = {marker: set() for marker in MARKERS}
    for seed in range(50):
        blocks, _ = render_template(TEMPLATES[0], Random(seed))
        texts = [cell for block in blocks for row in block.rows for cell in row]
        for marker in MARKERS:
            observed[marker].add(marker in texts)
    assert all(states == {True, False} for states in observed.values())


def test_default_volume() -> None:
    rng = Random(42)
    counts: Counter[EntityType] = Counter()
    for index in range(210):
        _, expected = render_template(TEMPLATES[index % 7], rng)
        counts.update(item.type for item in expected)
    assert counts.total() >= 1500
    assert counts[EntityType.TRADE_SECRET] >= 150
    assert set(counts) == {entity_type for _, entity_type in GENERATORS.values()}


def test_repeated_slots_use_sequential_offsets(tmp_path: Path) -> None:
    template = Template("repeated", DocPurpose.internal, "txt", (
        paragraph("{person_1}; повтор: {person_1}; другое лицо: {person_2}"),
    ))
    make_document(template, Random(42), tmp_path, "repeated")
    item = load_corpus(tmp_path)[0]
    assert len(item.gold) == 3
    first, repeated, _ = item.gold
    assert first.end < repeated.start
    assert item.doc.text[first.start:first.end] == item.doc.text[repeated.start:repeated.end]


def test_missing_value_error_is_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.evaluation.generator import build

    monkeypatch.setattr(build, "read_for_markup", lambda path: type("Doc", (), {"text": ""})())
    with pytest.raises(ValueError) as caught:
        make_document(TEMPLATES[0], Random(42), tmp_path, "missing")
    assert str(caught.value) == "contract: не найдено значение типа PERSON"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["", "..", "../escape", "folder/name", "folder\\name"])
def test_invalid_name(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="имя документа"):
        make_document(TEMPLATES[0], Random(42), tmp_path, name)
    assert not list(tmp_path.iterdir())


def test_existing_pair_is_not_overwritten(tmp_path: Path) -> None:
    path = make_document(TEMPLATES[6], Random(42), tmp_path, "existing")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="уже используется"):
        make_document(TEMPLATES[0], Random(43), tmp_path, "existing")
    assert path.read_bytes() == before
    assert len(load_corpus(tmp_path)) == 1


def test_empty_template_and_empty_markup(tmp_path: Path) -> None:
    template = Template("empty", DocPurpose.internal, "txt", ())
    path = make_document(template, Random(42), tmp_path, "empty")
    assert path.exists() and load_corpus(tmp_path)[0].gold == []
    assert _markup("", [], "empty") == []
