"""Безопасные ошибки парсеров: только подготовленный текст для пользователя."""

from collections.abc import Iterator
from contextlib import contextmanager

CORRUPT_FILE = (
    "Файл повреждён или сохранён не полностью. Откройте его и сохраните заново."
)
PASSWORD_PROTECTED = "Файл защищён паролем. Снимите пароль и загрузите снова."
EMPTY_DOCUMENT = "В документе нет текста для проверки."
FORMULAS_WITHOUT_VALUES = (
    "В таблице формулы без сохранённых значений. "
    "Откройте файл в Excel и сохраните его."
)


class ExtractionError(ValueError):
    """Ошибка с безопасным сообщением, которую worker передаёт пользователю."""

    def __init__(self, message: str, *, case: str = "corrupt_file") -> None:
        super().__init__(message)
        self.case = case


@contextmanager
def readable_file() -> Iterator[None]:
    """Скрывает детали исключений библиотек, сохраняя подготовленные ошибки."""
    try:
        yield
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError(CORRUPT_FILE) from None
