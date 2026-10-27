FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Сначала зависимости — этот слой кэшируется, пока не меняются requirements.
# requirements-dev (pytest, ruff) ставим тоже, чтобы работала команда
# `docker-compose exec api pytest` из AGENTS.md.
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

# Непривилегированный пользователь. Папку /data/uploads создаём заранее,
# чтобы именованный том унаследовал владельца appuser.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data/uploads \
    && chown -R appuser:appuser /data /app

# Тесты остаются в образе для `docker-compose run --rm --no-deps api pytest -q`.
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser tests ./tests
COPY --chown=appuser:appuser pyproject.toml ./

USER appuser
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
