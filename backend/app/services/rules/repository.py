"""Правила в БД; YAML используется только для начального заполнения пустой таблицы."""

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import RuleRow
from app.schemas import Rule
from app.services.rules.engine import validate_condition
from app.services.rules.loader import load_rules

RULES_PATHS = (
    Path("/data/rules/rules.yaml"),
    Path(__file__).resolve().parents[4] / "data/rules/rules.yaml",
)


class RuleAlreadyExistsError(ValueError):
    """Попытка добавить правило с уже занятым id."""


class RuleNotFoundError(LookupError):
    """Правило с указанным id отсутствует."""


def rules_yaml_path() -> Path:
    """Предпочитает каталог Docker, затем каталог локального репозитория."""
    for path in RULES_PATHS:
        if path.is_file():
            return path
    raise FileNotFoundError("Стартовый каталог правил не найден")


def _commit(session: Session) -> None:
    """Не оставляет сессию в сломанной транзакции после ошибки БД."""
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise


def _validated_rule(data: object) -> Rule:
    """Проверяет схему и всё дерево условий, включая выключенные правила."""
    payload = data.model_dump(mode="json") if isinstance(data, Rule) else data
    rule = Rule.model_validate(payload)
    try:
        validate_condition(rule.condition)
    except ValueError as error:
        raise ValueError(f"Правило {rule.id}: {error}") from None
    return rule


def _row_rule(row: RuleRow) -> Rule:
    """id и enabled берутся из колонок; остальные поля — из JSON правила."""
    return _validated_rule({**row.data, "id": row.id, "enabled": row.enabled})


def ensure_seeded(session: Session, yaml_path: Path | None = None) -> None:
    """Атомарно заполняет пустую таблицу, сохраняя порядок стартового каталога."""
    if session.scalar(select(RuleRow.id).limit(1)) is not None:
        return
    rules = load_rules(yaml_path if yaml_path is not None else rules_yaml_path())
    session.add_all([
        RuleRow(id=rule.id, enabled=rule.enabled, position=position,
                data=rule.model_dump(mode="json"))
        for position, rule in enumerate(rules, start=1)
    ])
    try:
        _commit(session)
    except IntegrityError:
        # Другой запрос мог успеть заполнить пустую таблицу той же транзакцией.
        if session.scalar(select(RuleRow.id).limit(1)) is None:
            raise


def list_rules(session: Session) -> list[Rule]:
    """Возвращает все правила, включая выключенные, в порядке добавления."""
    ensure_seeded(session)
    rows = session.scalars(select(RuleRow).order_by(RuleRow.position, RuleRow.id))
    return [_row_rule(row) for row in rows]


def get_rule(session: Session, rule_id: str) -> Rule:
    """Читает и проверяет одно правило."""
    row = session.get(RuleRow, rule_id)
    if row is None:
        raise RuleNotFoundError("Правило не найдено")
    return _row_rule(row)


def create_rule(session: Session, rule: Rule) -> Rule:
    """Добавляет правило в конец списка, не изменяя YAML."""
    rule = _validated_rule(rule)
    if session.get(RuleRow, rule.id) is not None:
        raise RuleAlreadyExistsError("Правило с таким id уже существует")
    position = (session.scalar(select(func.max(RuleRow.position))) or 0) + 1
    session.add(RuleRow(id=rule.id, enabled=rule.enabled, position=position,
                        data=rule.model_dump(mode="json")))
    try:
        _commit(session)
    except IntegrityError:
        if session.get(RuleRow, rule.id) is None:
            raise
        raise RuleAlreadyExistsError("Правило с таким id уже существует") from None
    return rule


def update_rule(session: Session, rule_id: str, rule: Rule) -> Rule:
    """Полностью заменяет правило, сохраняя id и позицию."""
    rule = _validated_rule(rule)
    if rule.id != rule_id:
        raise ValueError("id в пути и теле должны совпадать")
    row = session.get(RuleRow, rule_id)
    if row is None:
        raise RuleNotFoundError("Правило не найдено")
    row.enabled, row.data = rule.enabled, rule.model_dump(mode="json")
    _commit(session)
    return rule


def delete_rule(session: Session, rule_id: str) -> None:
    """Удаляет существующее правило из БД."""
    row = session.get(RuleRow, rule_id)
    if row is None:
        raise RuleNotFoundError("Правило не найдено")
    _row_rule(row)
    session.delete(row)
    _commit(session)
