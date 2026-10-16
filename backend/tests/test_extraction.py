"""Синтетические документы создаются во временной папке; бинарников в репозитории нет."""

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pymupdf
import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from openpyxl import Workbook

from app.schemas import ExtractedDocument, TextSegment
from app.services.extraction.docx import extract_docx
from app.services.extraction.extract import extract
from app.services.extraction.pdf import extract_pdf
from app.services.extraction.xlsx import extract_xlsx


@pytest.fixture
def docx_path(tmp_path: Path) -> Path:
    path = tmp_path / "sample.docx"
    document = Document()
    document.add_heading("1. Общие положения", level=1)
    document.add_paragraph("Выдуманный предмет закупки.")
    document.add_paragraph("  \t ")
    table = document.add_table(rows=2, cols=3)
    for row, values in zip(table.rows, [("Товар", "Количество", "Цена"),
                                       ("Лунный куб", "2", "10")], strict=True):
        for cell, value in zip(row.cells, values, strict=True):
            cell.text = value
    document.add_heading("2. Цена контракта", level=1)
    document.add_paragraph("Условная стоимость.")
    document.save(str(path))
    return path


@pytest.fixture
def xlsx_path(tmp_path: Path) -> Path:
    path = tmp_path / "sample.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Лист1"
    sheet.append([None, None, None])
    sheet.append(["Лунный куб", 0, 12.5])
    sheet.append([" ", None, "\t"])
    sheet.append([None, "Конец", None, "Строка"])
    sheet = workbook.create_sheet("Лист2")
    sheet.append([datetime(2025, 1, 2), False])
    sheet.append(["=1+1"])  # Без кэша формула в data_only=True даёт None.
    workbook.save(path)
    workbook.close()
    return path


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "1. General terms")
        page.insert_text((72, 120), "Fictional moon cubes.\nSynthetic description.")
        page = document.new_page()
        page.insert_text((72, 72), "Continued terms.")
        page.insert_text((72, 120), "2.1. Price")
        page.insert_text((72, 170), "Synthetic price.")
        document.save(path)
    return path


@pytest.fixture
def long_pdf_path(tmp_path: Path) -> Path:
    path = tmp_path / "long.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_htmlbox((72, 72, 500, 110), "1. Общие положения")
        page.insert_htmlbox((72, 120, 500, 160), "Выдуманный лунный куб.")
        page.insert_text((72, 200), "2. " + "a" * 117, fontsize=5)
        page.insert_text((72, 240), "Still in the first section.")
        for _ in range(50):
            document.new_page()
        document.save(path)
    return path


def test_pdf_cyrillic_long_heading_and_blank_pages(long_pdf_path: Path) -> None:
    document = extract(long_pdf_path, long_pdf_path.name, "synthetic-id")
    assert document.pages == 51
    assert "Выдуманный лунный куб." in document.text
    assert document.segments[-1].section == "1. Общие положения"
    assert all(segment.page == 1 for segment in document.segments)
    assert_offsets(document)


def assert_offsets(document: ExtractedDocument) -> None:
    assert document.text == "\n".join(segment.text for segment in document.segments)
    expected_offset = 0
    for index, segment in enumerate(document.segments):
        assert segment.id == index
        assert segment.offset == expected_offset
        assert document.text[segment.offset:segment.offset + len(segment.text)] == segment.text
        expected_offset += len(segment.text) + 1


def test_docx_order_and_sections(docx_path: Path) -> None:
    document = extract(docx_path, "sample.docx", "synthetic-doc")
    assert [segment.text for segment in document.segments] == [
        "1. Общие положения", "Выдуманный предмет закупки.",
        "Товар | Количество | Цена", "Лунный куб | 2 | 10",
        "2. Цена контракта", "Условная стоимость.",
    ]
    assert document.segments[2].section == "1. Общие положения"
    assert document.segments[-1].section == "2. Цена контракта"
    assert document.pages is None
    assert all(segment.page is None for segment in document.segments)
    assert_offsets(document)


def test_xlsx_sections_and_values(xlsx_path: Path) -> None:
    document = extract(xlsx_path, "sample.xlsx", "synthetic-sheet")
    assert [segment.section for segment in document.segments] == [
        "Лист1!A2:C2", "Лист1!B4:D4", "Лист2!A1:B1",
    ]
    assert [segment.text for segment in document.segments] == [
        "Лунный куб | 0 | 12.5", "Конец | Строка", "2025-01-02 00:00:00 | False",
    ]
    assert document.pages is None
    assert all(segment.page is None for segment in document.segments)
    assert_offsets(document)


def test_pdf_pages_and_sections(pdf_path: Path) -> None:
    document = extract(pdf_path, "sample.pdf", "synthetic-pdf")
    assert document.pages == 2
    assert [segment.page for segment in document.segments] == [1, 1, 2, 2, 2]
    assert document.segments[2].section == "1. General terms"
    assert document.segments[-1].section == "2.1. Price"
    assert "Fictional moon cubes." in document.text
    assert_offsets(document)


@pytest.mark.parametrize("fixture, filename", [
    ("pdf_path", "upload.PdF"), ("docx_path", "upload.DoCx"), ("xlsx_path", "upload.XlSx"),
])
def test_filename_dispatch_and_safe_logs(
    fixture: str, filename: str, request: pytest.FixtureRequest,
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    source: Path = request.getfixturevalue(fixture)
    path = tmp_path / "upload-without-extension"
    path.write_bytes(source.read_bytes())
    with caplog.at_level(logging.INFO, logger="app.services.extraction.extract"):
        document = extract(path, filename, "synthetic-id")
    assert document.text
    assert document.filename == filename
    assert document.document_id == "synthetic-id"
    assert filename in caplog.text
    assert f"сегментов={len(document.segments)}" in caplog.text
    assert f"страниц={document.pages}" in caplog.text
    assert all(segment.text not in caplog.text for segment in document.segments)
    assert_offsets(document)


def test_unsupported_extension(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        extract(tmp_path / "missing", "sample.txt", "synthetic-id")
    assert str(error.value) == (
        "Формат файла не поддерживается. Загрузите .pdf, .docx или .xlsx"
    )


@pytest.fixture(params=["pdf", "docx", "xlsx"])
def corrupt_path(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    extension: str = request.param
    path = tmp_path / f"broken.{extension}"
    path.write_bytes(b"\x00\xff\x13\x89not-a-document")
    return path


def test_corrupt_file(corrupt_path: Path) -> None:
    with pytest.raises(ValueError, match="Не удалось прочитать файл") as error:
        extract(corrupt_path, corrupt_path.name, "synthetic-id")
    assert error.value.__cause__ is not None


@pytest.fixture(params=["pdf", "docx", "xlsx"])
def empty_path(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    extension: str = request.param
    path = tmp_path / f"empty.{extension}"
    if extension == "pdf":
        with pymupdf.open() as document:
            document.new_page()
            document.new_page()
            document.save(path)
    elif extension == "docx":
        document = Document()
        document.add_paragraph(" \t ")
        document.add_table(rows=1, cols=2)
        document.save(str(path))
    else:
        workbook = Workbook()
        workbook.active.append([None, " \t "])
        workbook.save(path)
        workbook.close()
    return path


def test_empty_documents(empty_path: Path) -> None:
    extracted = extract(empty_path, empty_path.name, "synthetic-id")
    assert extracted.text == ""
    assert extracted.segments == []
    assert extracted.pages == (2 if empty_path.suffix == ".pdf" else None)
    assert_offsets(extracted)


@pytest.fixture
def merged_path(tmp_path: Path) -> Path:
    path = tmp_path / "merged.docx"
    document = Document()
    table = document.add_table(rows=3, cols=3)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Общий заголовок"
    table.cell(0, 2).text = "Правый столбец"
    table.cell(1, 0).merge(table.cell(2, 0)).text = "Общая категория"
    table.cell(1, 1).text = "Одинаково"
    table.cell(1, 2).text = "Одинаково"
    table.cell(2, 2).text = "Конец"
    document.save(str(path))
    return path


def test_docx_merged_cells(merged_path: Path) -> None:
    extracted = extract(merged_path, merged_path.name, "synthetic-id")
    assert [segment.text for segment in extracted.segments] == [
        "Общий заголовок | Правый столбец",
        "Общая категория | Одинаково | Одинаково", "Конец",
    ]
    assert_offsets(extracted)


@pytest.fixture(params=[
    ("Без номера", "Heading 2", "Без номера"),
    ("Русский стиль", "Заголовок 2", "Русский стиль"),
    ("3.2. Условия", "Normal", "3.2. Условия"),
    ("3. " + "а" * 117, "Normal", None),
    ("Обычный абзац", "Normal", None),
])
def heading_case(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[Path, str | None]:
    heading, style, expected = request.param
    path = tmp_path / "headings.docx"
    document = Document()
    if style not in document.styles:
        document.styles.add_style(style, WD_STYLE_TYPE.PARAGRAPH)
    document.add_paragraph(heading, style)
    document.add_paragraph("Текст раздела")
    document.save(str(path))
    return path, expected


def test_docx_heading_detection(heading_case: tuple[Path, str | None]) -> None:
    path, expected = heading_case
    assert extract_docx(path)[-1].section == expected


@pytest.mark.parametrize("fixture, parser", [
    ("docx_path", extract_docx), ("xlsx_path", extract_xlsx), ("pdf_path", extract_pdf),
])
def test_parser_offsets(
    fixture: str, parser: Callable[[Path], list[TextSegment]], request: pytest.FixtureRequest,
) -> None:
    path: Path = request.getfixturevalue(fixture)
    segments = parser(path)
    assert segments
    assert_offsets(ExtractedDocument(
        document_id="synthetic-id", filename=path.name,
        text="\n".join(segment.text for segment in segments), segments=segments,
    ))


def test_extract_reindexes_segments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_parser(path: Path) -> list[TextSegment]:
        return [TextSegment(id=8, offset=99, text="Первый\nблок"),
                TextSegment(id=5, offset=20, text=" \t "),
                TextSegment(id=3, offset=0, text="Повтор"),
                TextSegment(id=4, offset=0, text="Повтор")]

    monkeypatch.setattr("app.services.extraction.extract.extract_docx", fake_parser)
    document = extract(tmp_path / "unused", "synthetic.docx", "synthetic-id")
    assert len(document.segments) == 3
    assert_offsets(document)
