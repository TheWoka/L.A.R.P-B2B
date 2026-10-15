"""Парсер .xlsx на openpyxl. Задача T-003, исполнитель — Владос.

Внутренний интерфейс модуля extraction — не контракт: исполнитель может его менять,
контрактом является только extract() в extract.py.
"""

from pathlib import Path

from app.schemas import TextSegment


def extract_xlsx(path: Path) -> list[TextSegment]:
    """Читает .xlsx и возвращает сегменты текста по порядку.

    offset считается от начала текста этого файла при склейке сегментов через "\\n";
    page = None, section — адрес ячейки или диапазона ("Лист1!B2").
    Задача T-003, исполнитель — Владос.
    """
    raise NotImplementedError("T-003 (Владос)")
