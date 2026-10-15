"""Поиск маркеров чек-листа. Задача T-010, исполнитель — Данил."""

from app.schemas import ExtractedDocument, Marker


def find_markers(doc: ExtractedDocument) -> list[Marker]:
    """Ищет лист согласования, гриф, срок хранения, согласие на обработку ПДн и др.

    Возвращает по одному Marker на КАЖДЫЙ MarkerType: present=True с evidence
    (цитата и позиция) или present=False без evidence.

    Контракт: docs/contracts.md, раздел 3. Задача T-010, исполнитель — Данил.
    """
    raise NotImplementedError("T-010 (Данил)")
