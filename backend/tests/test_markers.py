"""Маркеры на синтетических текстах: контекст, короткие цитаты и координаты."""

import logging

import pytest

from app.schemas import ExtractedDocument, Marker, MarkerType, TextSegment
from app.services.markers.find_markers import find_markers


def _document(*parts: tuple[str, int | None, str | None]) -> ExtractedDocument:
    segments: list[TextSegment] = []
    offset = 0
    for index, (text, page, section) in enumerate(parts):
        segments.append(TextSegment(id=index, text=text, offset=offset, page=page, section=section))
        offset += len(text) + 1
    return ExtractedDocument(
        document_id="synthetic-markers", filename="synthetic.txt",
        text="\n".join(segment.text for segment in segments), segments=segments,
    )


def _marker(doc: ExtractedDocument, kind: MarkerType) -> Marker:
    markers = find_markers(doc)
    assert len(markers) == 6
    assert [marker.type for marker in markers] == list(MarkerType)
    return next(marker for marker in markers if marker.type == kind)


def _assert_evidence(doc: ExtractedDocument, kind: MarkerType, quote: str) -> None:
    marker = _marker(doc, kind)
    assert marker.present
    evidence = marker.evidence
    assert evidence is not None
    assert evidence.quote == quote
    assert doc.text[evidence.start:evidence.end] == quote
    assert evidence.end - evidence.start == len(quote)
    assert (evidence.page, evidence.section) == (7, "Синтетический раздел")


@pytest.mark.parametrize(("kind", "phrase"), [
    (MarkerType.approval_sheet, "лист согласования"),
    (MarkerType.approval_sheet, "Согласовано"),
    (MarkerType.approval_sheet, "УТВЕРЖДАЮ"),
    (MarkerType.approval_sheet, "визы"),
    (MarkerType.approval_sheet, "Лист ознакомления"),
    (MarkerType.classification_mark, "КОММЕРЧЕСКАЯ ТАЙНА"),
    (MarkerType.classification_mark, "Конфиденциально"),
    (MarkerType.classification_mark, "Для служебного пользования"),
    (MarkerType.classification_mark, "ДСП"),
    (MarkerType.retention_period, "срок хранения"),
    (MarkerType.retention_period, "хранится в течение"),
    (MarkerType.retention_period, "подлежит уничтожению"),
    (MarkerType.retention_period, "уничтожается по истечении"),
    (MarkerType.retention_period, "срок хранения документа"),
    (MarkerType.consent_clause, "согласие на обработку персональных данных"),
    (MarkerType.consent_clause, "Даю согласие на обработку"),
    (MarkerType.consent_clause, "согласие субъекта персональных данных"),
    (MarkerType.processing_assignment, "по поручению оператора"),
    (MarkerType.processing_assignment, "поручение обработки"),
    (MarkerType.processing_assignment, "поручает обработку персональных данных"),
    (MarkerType.kii_mention, "критической информационной инфраструктуры"),
    (MarkerType.kii_mention, "объект КИИ"),
    (MarkerType.kii_mention, "значимый объект"),
    (MarkerType.kii_mention, "187-ФЗ"),
    (MarkerType.kii_mention, "КИИ"),
])
def test_each_keyword_has_exact_quote_and_segment_metadata(kind: MarkerType, phrase: str) -> None:
    doc = _document(("Выдуманное введение", 1, "Введение"),
                    (phrase, 7, "Синтетический раздел"))
    _assert_evidence(doc, kind, phrase)


@pytest.mark.parametrize(("kind", "text"), [
    (MarkerType.approval_sheet, "Условия согласованы сторонами выдуманного договора."),
    (MarkerType.classification_mark,
     "Стороны обязуются не разглашать сведения, составляющие коммерческую тайну"),
    (MarkerType.retention_period, "Учебный договор не содержит сроков архивирования."),
    (MarkerType.consent_clause, "Учебная обработка документов описана без согласия."),
    (MarkerType.processing_assignment, "Исполнитель самостоятельно оказывает учебные услуги."),
    (MarkerType.kii_mention, "В описании упомянуты только обычные учебные системы."),
])
def test_each_marker_negative(kind: MarkerType, text: str) -> None:
    marker = _marker(_document((text, 7, "Синтетический раздел")), kind)
    assert not marker.present
    assert marker.evidence is None


@pytest.mark.parametrize(("kind", "text"), [
    (MarkerType.classification_mark, "Столешница из ЛДСП и плиты ДСП толщиной 16 мм."),
    (MarkerType.approval_sheet, "Поставщик оформляет визы сотрудникам за свой счёт."),
    (MarkerType.approval_sheet, "Согласовано сторонами, что поставка учебная."),
    (MarkerType.retention_period, "Гарантийный срок хранения товара — 12 месяцев."),
])
def test_review_false_positives_are_rejected(kind: MarkerType, text: str) -> None:
    marker = _marker(_document((text, 7, "Синтетический раздел")), kind)
    assert not marker.present
    assert marker.evidence is None


@pytest.mark.parametrize("text", ["", " ", "\n\n", "Обычный синтетический договор."])
def test_no_markers_still_returns_all_six_in_enum_order(text: str) -> None:
    doc = ExtractedDocument(document_id="empty", filename="empty.txt", text=text, segments=[])
    markers = find_markers(doc)
    assert len(markers) == 6
    assert [marker.type for marker in markers] == list(MarkerType)
    assert all(not marker.present and marker.evidence is None for marker in markers)


def test_all_markers_are_returned_once_in_enum_order() -> None:
    doc = _document((
        "Лист согласования\nКОММЕРЧЕСКАЯ ТАЙНА\nСрок хранения документа\n"
        "Даю согласие на обработку\nПо поручению оператора\nОбъект КИИ",
        7, "Синтетический раздел",
    ))
    markers = find_markers(doc)
    assert len(markers) == 6
    assert [marker.type for marker in markers] == list(MarkerType)
    assert all(marker.present and marker.evidence is not None for marker in markers)
    for marker in markers:
        evidence = marker.evidence
        assert evidence is not None
        assert doc.text[evidence.start:evidence.end] == evidence.quote
        assert (evidence.page, evidence.section) == (7, "Синтетический раздел")


@pytest.mark.parametrize("word", ["Согласовано", "Утверждаю", "Утвёрждаю", "Визы"])
def test_approval_at_line_start_quotes_no_synthetic_person(word: str) -> None:
    text = f"Выдуманные реквизиты\r\n\t  {word}: Тестов Тест Тестович\nПрочий текст"
    doc = _document((text, 7, "Синтетический раздел"))
    _assert_evidence(doc, MarkerType.approval_sheet, word)


@pytest.mark.parametrize("word", ["Согласовано", "Утверждаю", "Визы"])
@pytest.mark.parametrize("suffix", [
    "", " \t", ": Тестов Тест", ", Тестов Тест", "/ Тестов Тест",
    "- Тестов Тест", "— Тестов Тест", "\tТестов Тест", " \t\nстрочное продолжение",
])
def test_approval_label_accepts_only_allowed_continuation(word: str, suffix: str) -> None:
    doc = _document((f"Выдуманный заголовок\n\t{word}{suffix}",
                     7, "Синтетический раздел"))
    _assert_evidence(doc, MarkerType.approval_sheet, word)


@pytest.mark.parametrize("word", ["Согласовано", "Утверждаю", "Визы"])
@pytest.mark.parametrize("suffix", [" сторонами учебного договора.", "\tучебный текст.", "."])
def test_approval_label_rejects_lowercase_continuation_and_period(word: str, suffix: str) -> None:
    marker = _marker(_document((f"\t{word}{suffix}", 7, None)), MarkerType.approval_sheet)
    assert not marker.present
    assert marker.evidence is None


def test_uppercase_approval_on_separate_line() -> None:
    doc = _document(("Учебное введение\nСОГЛАСОВАНО\nстрочное продолжение",
                     7, "Синтетический раздел"))
    _assert_evidence(doc, MarkerType.approval_sheet, "СОГЛАСОВАНО")


@pytest.mark.parametrize("text", [
    "В учебном описании согласовано место поставки.",
    "Я утверждаю условия выдуманного договора.",
    "Переутверждаю учебную запись.", "Безвизовый учебный режим.",
])
def test_approval_rejects_prose_and_word_fragments(text: str) -> None:
    assert not _marker(_document((text, 7, None)), MarkerType.approval_sheet).present


def test_first_valid_approval_wins_regardless_of_dictionary_order() -> None:
    doc = _document((
        "В учебном тексте согласовано условие.\nУтверждаю: Тестов Тест\nЛист согласования",
        7, "Синтетический раздел",
    ))
    _assert_evidence(doc, MarkerType.approval_sheet, "Утверждаю")


@pytest.mark.parametrize("phrase", [
    "коммерческая тайна", "конфиденциально", "для служебного пользования", "дсп",
    "ДСП", "КОНФИДЕНЦИАЛЬНО",
])
def test_classification_is_an_isolated_short_line(phrase: str) -> None:
    doc = _document((f"Учебный заголовок\n«{phrase}»\nВыдуманное продолжение",
                     7, "Синтетический раздел"))
    _assert_evidence(doc, MarkerType.classification_mark, phrase)


@pytest.mark.parametrize("section", ["Верхний колонтитул", "Нижний колонтитул"])
@pytest.mark.parametrize("phrase", ["конфиденциально", "КОНФИДЕНЦИАЛЬНО", "ДСП"])
def test_classification_in_long_margin_segment(section: str, phrase: str) -> None:
    text = "Учебный шаблон " * 10 + f"{phrase} — Тестов Тест"
    doc = _document(("Введение", 1, None), (text, 7, section))
    marker = _marker(doc, MarkerType.classification_mark)
    assert marker.present and marker.evidence is not None
    assert marker.evidence.quote == phrase
    assert doc.text[marker.evidence.start:marker.evidence.end] == phrase
    assert (marker.evidence.page, marker.evidence.section) == (7, section)


@pytest.mark.parametrize("phrase", ["КОММЕРЧЕСКАЯ ТАЙНА", "ДЛЯ СЛУЖЕБНОГО ПОЛЬЗОВАНИЯ"])
def test_classification_in_uppercase_inside_long_line(phrase: str) -> None:
    doc = _document((("Учебная строка " * 10) + f"{phrase}: Тестов Тест",
                     7, "Синтетический раздел"))
    _assert_evidence(doc, MarkerType.classification_mark, phrase)


@pytest.mark.parametrize("text", [
    "Здесь слово конфиденциально используется как пример.",
    "Говорим про понятие «коммерческая тайна» в учебном договоре.",
    "Выдуманный текст для служебного пользования содержит описание.",
    "Стороны обязуются не разглашать сведения, составляющие коммерческую тайну",
    "Поставить плиту ДСП для учебного стенда.",
    "Слово КОНФИДЕНЦИАЛЬНО приведено в учебной инструкции.",
])
def test_classification_rejects_phrase_inside_short_prose(text: str) -> None:
    assert not _marker(_document((text, 7, None)), MarkerType.classification_mark).present


def test_first_valid_classification_skips_prose() -> None:
    doc = _document(("Слово конфиденциально приведено как пример.", 1, "Введение"),
                    ("Коммерческая тайна", 7, "Синтетический раздел"),
                    ("КОНФИДЕНЦИАЛЬНО", 8, "Позже"))
    _assert_evidence(doc, MarkerType.classification_mark, "Коммерческая тайна")


@pytest.mark.parametrize("length", [80, 81])
@pytest.mark.parametrize("phrase", ["конфиденциально", "КОНФИДЕНЦИАЛЬНО", "ДСП"])
def test_isolated_classification_length_limit(length: int, phrase: str) -> None:
    label = "." * (length - len(phrase)) + phrase
    doc = _document((label, 7, None))
    marker = _marker(doc, MarkerType.classification_mark)
    assert marker.present is (length == 80)


@pytest.mark.parametrize("goods", [
    "товаров", "ПРОДУКЦИИ", "изделий", "грузов", "материалов", "образцов",
])
@pytest.mark.parametrize("preceding_words", [0, 1, 2, 3])
def test_retention_excludes_goods_only_within_next_three_words(
    goods: str, preceding_words: int,
) -> None:
    phrase = "СРОК\tХРАНЕНИЯ"
    text = f"Учебное условие: {phrase} {'учебных ' * preceding_words}{goods} — 12 месяцев."
    doc = _document((text, 7, "Синтетический раздел"))
    marker = _marker(doc, MarkerType.retention_period)
    if preceding_words == 3:
        _assert_evidence(doc, MarkerType.retention_period, phrase)
    else:
        assert not marker.present
        assert marker.evidence is None


@pytest.mark.parametrize(("text", "quote"), [
    ("Срок хранения документа — 5 лет", "Срок хранения документа"),
    ("срок хранения персональных данных — 5 лет", "срок хранения"),
])
def test_retention_of_documents_and_personal_data_remains_present(text: str, quote: str) -> None:
    _assert_evidence(_document((text, 7, "Синтетический раздел")),
                     MarkerType.retention_period, quote)


@pytest.mark.parametrize("phrase", [
    "хранится в течение", "подлежит уничтожению", "уничтожается по истечении",
])
def test_other_retention_phrases_keep_existing_behavior(phrase: str) -> None:
    doc = _document((f"Учебный образец {phrase} срока хранения товара.",
                     7, "Синтетический раздел"))
    _assert_evidence(doc, MarkerType.retention_period, phrase)


def test_first_valid_retention_skips_goods_storage() -> None:
    doc = _document(("Срок хранения товара — 12 месяцев.", 1, "Введение"),
                    ("Срок хранения документа — 5 лет.", 7, "Синтетический раздел"))
    _assert_evidence(doc, MarkerType.retention_period, "Срок хранения документа")


@pytest.mark.parametrize("reference", ["ч. 3 ст. 6", "ч.3 ст.6", "части 3 статьи 6"])
@pytest.mark.parametrize("law_first", [False, True])
def test_processing_article_requires_nearby_law(reference: str, law_first: bool) -> None:
    text = f"152-ФЗ: {reference}" if law_first else f"{reference} закона 152-ФЗ"
    doc = _document((text, 7, "Синтетический раздел"))
    _assert_evidence(doc, MarkerType.processing_assignment, reference)


@pytest.mark.parametrize("law_first", [False, True])
@pytest.mark.parametrize("gap", [80, 81])
def test_processing_law_distance_boundary(law_first: bool, gap: int) -> None:
    article, law = "ч. 3 ст. 6", "152-ФЗ"
    text = law + " " * gap + article if law_first else article + " " * gap + law
    marker = _marker(_document((text, 7, None)), MarkerType.processing_assignment)
    assert marker.present is (gap == 80)


@pytest.mark.parametrize("text", [
    "ч. 3 ст. 6 учебного положения", "части 3 статьи 6", "152-ФЗ",
    "ч. 3 ст. 6 и 187-ФЗ", "ч. 3 ст. 60 закона 152-ФЗ",
    "ч. 3 ст. 6 закона 1152-ФЗ", "ч. 3 ст. 6 закона 152-ФЗ2",
    "ч. 3 ст. 6.1 закона 152-ФЗ", "части 3 статьи 6.1 закона 152-ФЗ",
    "ч. 3 ст. 6,1 закона 152-ФЗ",
])
def test_processing_article_negative(text: str) -> None:
    marker = _marker(_document((text, 7, None)), MarkerType.processing_assignment)
    assert not marker.present and marker.evidence is None


def test_first_processing_article_wins_before_later_keyword() -> None:
    doc = _document(("ч. 3 ст. 6 закона 152-ФЗ; далее по поручению оператора",
                     7, "Синтетический раздел"))
    _assert_evidence(doc, MarkerType.processing_assignment, "ч. 3 ст. 6")


@pytest.mark.parametrize("text", ["КИИмя", "неКИИ", "КИИ123", "_КИИ_", "1187-ФЗ"])
def test_kii_rejects_word_fragments(text: str) -> None:
    assert not _marker(_document((text, 7, None)), MarkerType.kii_mention).present


def test_kii_acronym_is_case_insensitive_and_whole_word() -> None:
    doc = _document(("Синтетическое описание (кии).", 7, "Синтетический раздел"))
    _assert_evidence(doc, MarkerType.kii_mention, "кии")


def test_case_yo_and_whitespace_preserve_offsets_with_unicode_prefix() -> None:
    text = "İ 🦊 Выдуманный акт: УНИЧТОЖАЁТСЯ\tПО  ИСТЕЧЁНИИ пяти лет."
    quote = "УНИЧТОЖАЁТСЯ\tПО  ИСТЕЧЁНИИ"
    doc = _document((text, 7, "Синтетический раздел"))
    _assert_evidence(doc, MarkerType.retention_period, quote)


@pytest.mark.parametrize("kind, first, second", [
    (MarkerType.approval_sheet, "Лист ознакомления", "Лист согласования"),
    (MarkerType.classification_mark, "дсп", "Конфиденциально"),
    (MarkerType.retention_period, "Подлежит уничтожению", "Срок хранения документа"),
    (MarkerType.consent_clause, "Даю согласие на обработку",
     "Согласие субъекта персональных данных"),
    (MarkerType.processing_assignment, "Поручение обработки", "По поручению оператора"),
    (MarkerType.kii_mention, "187-ФЗ", "Значимый объект"),
])
def test_first_occurrence_of_each_marker(kind: MarkerType, first: str, second: str) -> None:
    doc = _document((first, 7, "Синтетический раздел"), (second, 8, "Позже"))
    _assert_evidence(doc, kind, first)
    marker = _marker(doc, kind)
    assert marker.evidence is not None and marker.evidence.start == 0


def test_quote_uses_segment_containing_start_when_phrase_crosses_segments() -> None:
    doc = _document(("согласие субъекта", 7, "Синтетический раздел"),
                    ("персональных данных", 8, "Следующий раздел"))
    _assert_evidence(doc, MarkerType.consent_clause, "согласие субъекта\nперсональных данных")


def test_unsorted_segments_do_not_change_first_match_or_mutate_document() -> None:
    doc = _document(("Выдуманное введение", 1, None),
                    ("Срок хранения документа", 7, "Синтетический раздел"))
    doc.segments.reverse()
    original = doc.model_dump()
    _assert_evidence(doc, MarkerType.retention_period, "Срок хранения документа")
    assert doc.model_dump() == original


def test_match_without_segments_has_no_page_or_section() -> None:
    doc = ExtractedDocument(document_id="no-segments", filename="test.txt",
                            text="Лист согласования", segments=[])
    marker = _marker(doc, MarkerType.approval_sheet)
    assert marker.present and marker.evidence is not None
    assert marker.evidence.quote == doc.text
    assert marker.evidence.page is None and marker.evidence.section is None


def test_match_before_first_segment_has_no_incorrect_metadata() -> None:
    doc = _document(("Выдуманное введение", 1, None), ("Срок хранения", 7, "Позже"))
    doc.segments = doc.segments[1:]
    marker = _marker(doc, MarkerType.approval_sheet)
    assert not marker.present
    doc.text = "Лист согласования\n" + doc.text
    for segment in doc.segments:
        segment.offset += len("Лист согласования\n")
    marker = _marker(doc, MarkerType.approval_sheet)
    assert marker.evidence is not None
    assert marker.evidence.page is None and marker.evidence.section is None


def test_logs_only_found_marker_count(caplog: pytest.LogCaptureFixture) -> None:
    text = "Лист согласования: Тестов Тест Тестович\n187-ФЗ"
    doc = _document((text, 7, "Не логировать название раздела"))
    with caplog.at_level(logging.DEBUG, logger="app.services.markers.find_markers"):
        find_markers(doc)
    assert [record.getMessage() for record in caplog.records] == ["Найдено маркеров: 2"]
    assert "Тестов" not in caplog.text
    assert "Лист согласования" not in caplog.text
    assert "187-ФЗ" not in caplog.text
    assert "Не логировать" not in caplog.text
