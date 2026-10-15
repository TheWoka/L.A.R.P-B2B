"""Единая настройка логирования.

ВАЖНО (AGENTS.md, правило 4): значения ПДн НЕ логируем.
Можно: id документа и задачи, этап, время, количество найденных сущностей.
Нельзя: сам текст документа, найденные ФИО/СНИЛС/телефоны, mapping из маскирования.
"""

import logging

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Настраивает корневой логгер. Повторный вызов безопасен."""
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)
