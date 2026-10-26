"""Безопасные русские сообщения об ошибках входных данных (T-052)."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

PURPOSE_ERROR = (
    "Выберите назначение документа: внутренний, для публикации, договор с контрагентом "
    "или международный контракт."
)
EMPTY_TEXT_ERROR = "Введите текст для проверки."
LONG_TEXT_ERROR = (
    "Текст слишком длинный: не больше 200 000 символов. Загрузите его файлом."
)
RULE_FIELD_NAMES = {
    "id": "идентификатор",
    "title": "название",
    "description": "описание",
    "severity": "уровень риска",
    "law_refs": "ссылки на законы",
    "applies_to": "назначения документов",
    "condition": "условие",
    "recommendation": "рекомендация",
}
VALIDATION_ERROR_RESPONSE = {
    "description": "Ошибка ввода: понятное сообщение на русском языке",
    "content": {"application/json": {"schema": {
        "type": "object",
        "properties": {"detail": {"type": "string"}},
        "required": ["detail"],
    }}},
}


def _validation_detail(request: Request, exc: RequestValidationError) -> str:
    """Использует только loc/type первой ошибки; input/msg/ctx могут содержать ПДн."""
    errors = exc.errors()
    error = errors[0] if errors else {}
    location = error.get("loc", ())
    field = location[1] if len(location) > 1 and isinstance(location[1], str) else "запрос"
    error_type = error.get("type")
    if request.url.path.rstrip("/") == "/api/rules" or request.url.path.startswith("/api/rules/"):
        if field in RULE_FIELD_NAMES:
            name = RULE_FIELD_NAMES[field]
            if error_type == "missing":
                return f"Правило: не заполнено поле «{name}»."
            return f"Правило: неверное значение поля «{name}»."
    if field == "purpose":
        return PURPOSE_ERROR
    if field == "text":
        if error_type in {"missing", "string_too_short", "value_error"}:
            return EMPTY_TEXT_ERROR
        if error_type == "string_too_long":
            return LONG_TEXT_ERROR
    if field == "file" and error_type == "missing":
        return "Выберите файл для проверки."
    if field == "corpus":
        return "Неверное имя набора данных."
    return f"Неверный запрос: проверьте поле «{field}»."


async def request_validation_error_handler(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    """Не сериализует исключение, тело запроса или введённые значения."""
    return JSONResponse(status_code=422, content={"detail": _validation_detail(request, exc)})
