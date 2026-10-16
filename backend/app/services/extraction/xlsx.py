"""Парсер .xlsx на openpyxl. Задача T-003.

Внутренний интерфейс модуля extraction — не контракт: исполнитель может его менять,
контрактом является только extract() в extract.py.
"""

from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from app.schemas import TextSegment


def extract_xlsx(path: Path) -> list[TextSegment]:
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
                for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                    cells = [(column, str(value)) for column, value in enumerate(row, start=1)
                             if value is not None and str(value).strip()]
                    if not cells:
                        continue
                    first = f"{get_column_letter(cells[0][0])}{row_number}"
                    last = f"{get_column_letter(cells[-1][0])}{row_number}"
                    text = " | ".join(value for _, value in cells)
                    segments.append(TextSegment(
                        id=len(segments), text=text, offset=offset,
                        section=f"{sheet.title}!{first}:{last}",
                    ))
                    offset += len(text) + 1
        finally:
            workbook.close()
    return segments
