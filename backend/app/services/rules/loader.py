"""Загрузка правил из YAML. Задача T-004, исполнитель — Данил.

Внутренний интерфейс модуля rules — не контракт.
"""

from pathlib import Path

from app.schemas import Rule


def load_rules(path: Path) -> list[Rule]:
    """Читает data/rules/rules.yaml и валидирует каждое правило моделью Rule.

    Ошибка в одном правиле — понятное исключение с id правила.
    Задача T-004, исполнитель — Данил.
    """
    raise NotImplementedError("T-004 (Данил)")
