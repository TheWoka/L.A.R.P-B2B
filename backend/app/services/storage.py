"""Временное хранение исходных файлов до завершения обработки."""

from pathlib import Path

from fastapi import UploadFile

UPLOAD_DIR = Path("/data/uploads")
CHUNK_BYTES = 1024 * 1024


def save_upload(file: UploadFile, document_id: str, max_bytes: int) -> Path:
    """Сохраняет загрузку частями, удаляя неполный файл при ошибке."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{document_id}{Path(file.filename or '').suffix.lower()}"
    size = 0
    try:
        with path.open("wb") as target:
            while chunk := file.file.read(CHUNK_BYTES):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("Файл больше 50 МБ")
                target.write(chunk)
    except Exception:
        delete_file(path)
        raise
    return path


def save_text(text: str, document_id: str) -> Path:
    """Сохраняет текстовый запрос в UTF-8."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{document_id}.txt"
    try:
        path.write_text(text, encoding="utf-8")
    except Exception:
        delete_file(path)
        raise
    return path


def delete_file(path: str | Path) -> None:
    """Удаляет исходный файл; повторное удаление безопасно."""
    Path(path).unlink(missing_ok=True)
