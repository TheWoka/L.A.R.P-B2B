"""Парсер .xlsx на openpyxl. Задача T-003.

Внутренний интерфейс модуля extraction — не контракт: исполнитель может его менять,
контрактом является только extract() в extract.py.
"""

import re
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.read_only import EmptyCell, ReadOnlyCell
from openpyxl.utils import get_column_letter

from app.schemas import TextSegment
from app.services.extraction.errors import (
    FORMULAS_WITHOUT_VALUES,
    ExtractionError,
    readable_file,
)

UNITS = frozenset({"руб.", "руб", "₽", "%", "шт.", "тыс. руб.", "млн руб."})


def _format_cell(cell: ReadOnlyCell | EmptyCell) -> str:
    """Учитывает процентный формат, сохраняя прежний текст остальных значений."""
    value = cell.value
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    number_format = cell.number_format or "General"
    # Литералы, цвета, локали и заполнители не являются разрядами числа.
    pattern = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]|_.|\*.', "", number_format.split(";")[0])
    digits = re.search(r"[0#?][0#?, \u00a0\u202f]*(?:\.([0#?]+))?", pattern)
    if not digits or "%" not in pattern:
        return str(value)
    decimals = digits.group(1) or ""
    with localcontext() as context:
        context.prec = max(28, len(str(value)) + len(decimals) + 5)
        number = Decimal(str(value)) * 100
        number = number.quantize(Decimal(1).scaleb(-len(decimals)), rounding=ROUND_HALF_UP)
        text = format(number, f".{len(decimals)}f")
    if decimals and decimals.endswith("#"):
        minimum = len(decimals.rstrip("#"))
        integer, fraction = text.split(".")
        fraction = fraction.rstrip("0").ljust(minimum, "0")
        text = integer + ("." + fraction if fraction else "")
    return text.replace(".", ",") + "%"


def _row_text(row: tuple[ReadOnlyCell | EmptyCell, ...]) -> str:
    """Присоединяет отдельную единицу только к соседней числовой ячейке."""
    parts: list[str] = []
    for index, cell in enumerate(row):
        text = _format_cell(cell)
        if not text.strip():
            continue
        unit = text.strip().lower()
        previous = row[index - 1].value if index else None
        if unit in UNITS and isinstance(previous, (int, float)) and not isinstance(previous, bool):
            duplicate = unit == "%" and parts[-1].endswith("%")
            if not duplicate:
                parts[-1] += " " + text.strip()
        else:
            parts.append(text)
    return " | ".join(parts)


def extract_xlsx(path: Path) -> list[TextSegment]:
    """Переводит ошибки чтения XLSX в безопасное сообщение."""
    with readable_file():
        segments = _read_xlsx(path)
        if not segments and _has_formulas(path):
            raise ExtractionError(FORMULAS_WITHOUT_VALUES, case="formulas_without_values")
        return segments


def _has_formulas(path: Path) -> bool:
    """Проверяет формулы только когда сохранённых значений для проверки нет."""
    with path.open("rb") as source:
        workbook = load_workbook(source, read_only=True, data_only=False)
        try:
            return any(
                cell.data_type == "f"
                for sheet in workbook for row in sheet.iter_rows() for cell in row
            )
        finally:
            workbook.close()


def _read_xlsx(path: Path) -> list[TextSegment]:
    """Читает .xlsx и возвращает сегменты текста по порядку.

    offset считается от начала текста этого файла при склейке сегментов через "\\n";
    page = None, section — адрес ячейки или диапазона ("Лист1!B2").
    Задача T-003.
    """
    segments: list[TextSegment] = []
    offset = 0
    # Поток позволяет читать временный файл без расширения исходного filename.
    with path.open("rb") as source:
        workbook = load_workbook(source, read_only=True, data_only=True)
        try:
            for sheet in workbook:
                for row_number, row in enumerate(sheet.iter_rows(), start=1):
                    cells = [column for column, cell in enumerate(row, start=1)
                             if cell.value is not None and str(cell.value).strip()]
                    if not cells:
                        continue
                    first = f"{get_column_letter(cells[0])}{row_number}"
                    last = f"{get_column_letter(cells[-1])}{row_number}"
                    text = _row_text(row)
                    segments.append(TextSegment(
                        id=len(segments), text=text, offset=offset,
                        section=f"{sheet.title}!{first}:{last}",
                    ))
                    offset += len(text) + 1
        finally:
            workbook.close()
    return segments
