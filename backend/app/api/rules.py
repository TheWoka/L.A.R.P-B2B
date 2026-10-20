"""Управление правилами проверки через БД; начальный каталог читается из YAML."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas import Rule
from app.services.rules import repository

DatabaseSession = Annotated[Session, Depends(get_db)]


def _seed_rules(session: DatabaseSession) -> None:
    """Перед каждым запросом заполняет таблицу, только если она ещё пуста."""
    repository.ensure_seeded(session)


router = APIRouter(tags=["rules"], dependencies=[Depends(_seed_rules)])


@router.get("/rules", response_model=list[Rule], summary="Список правил")
def list_rules(session: DatabaseSession) -> list[Rule]:
    return repository.list_rules(session)


@router.post("/rules", status_code=201, response_model=Rule, summary="Добавить правило")
def create_rule(rule: Rule, session: DatabaseSession) -> Rule:
    try:
        return repository.create_rule(session, rule)
    except repository.RuleAlreadyExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@router.put("/rules/{rule_id}", response_model=Rule, summary="Изменить правило")
def update_rule(rule_id: str, rule: Rule, session: DatabaseSession) -> Rule:
    try:
        return repository.update_rule(session, rule_id, rule)
    except repository.RuleNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@router.delete(
    "/rules/{rule_id}", status_code=204, response_class=Response, summary="Удалить правило"
)
def delete_rule(rule_id: str, session: DatabaseSession) -> Response:
    try:
        repository.delete_rule(session, rule_id)
    except repository.RuleNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    return Response(status_code=204)
