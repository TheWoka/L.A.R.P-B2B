"""Управление правилами проверки. Заглушки по контракту, реализация — T-010."""

from fastapi import APIRouter, HTTPException, Response

from app.schemas import Rule

router = APIRouter(tags=["rules"])

NOT_IMPLEMENTED = "Будет реализовано в T-010"


@router.get("/rules", response_model=list[Rule], summary="Список правил")
def list_rules() -> list[Rule]:
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED)


@router.post("/rules", status_code=201, response_model=Rule, summary="Добавить правило")
def create_rule(rule: Rule) -> Rule:
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED)


@router.put("/rules/{rule_id}", response_model=Rule, summary="Изменить правило")
def update_rule(rule_id: str, rule: Rule) -> Rule:
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED)


@router.delete(
    "/rules/{rule_id}", status_code=204, response_class=Response, summary="Удалить правило"
)
def delete_rule(rule_id: str) -> Response:
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED)
