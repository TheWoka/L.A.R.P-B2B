# ИИ-аудитор безопасности документов

Проверяет документы закупок на персональные данные, коммерческую тайну, грифы, согласования
и сроки хранения и выдаёт отчёт со светофором и ссылками на статьи законов
(хакатон B2B-РТС «AI Procurement Copilot», задача 7).

## Быстрый старт

Нужен Docker Desktop.

```bash
cp .env.example .env          # Windows PowerShell: Copy-Item .env.example .env
docker-compose up --build
```

| Что | Адрес |
|---|---|
| Интерфейс | http://localhost:3000 |
| API и Swagger | http://localhost:8000/docs |
| Проверка состояния | http://localhost:8000/api/health |

Без файла `.env` compose не запустится — скопируйте `.env.example`. По умолчанию
`LLM_PROVIDER=mock`: всё работает без API-ключа.

## Разработка без Docker

```bash
# бэкенд (Python 3.11)
cd backend
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest && ruff check .

# фронтенд (Node 20.19+)
cd frontend
npm ci
npm run dev        # http://localhost:5173, /api проксируется на localhost:8000
```

## Структура

- `backend/` — FastAPI, Celery, сервисы конвейера (`app/services/*`), тесты.
- `frontend/` — React + TypeScript + Ant Design.
- `data/` — правила (`rules/rules.yaml`), тексты законов, демо-документы (только синтетика).
- `eval/` — генератор синтетического корпуса и подсчёт качества детекции.
- `docs/` — **контракты** (`contracts.md`), архитектура, каталог правил.

Перед любой задачей прочитайте `AGENTS.md` и `docs/contracts.md`.

## Статус: каркас (T-001)

Работают: `docker-compose`, `/api/health`, Swagger со всеми эндпоинтами, Celery-задача `ping`,
навигация фронтенда и пример отчёта. Остальные эндпоинты отвечают `501` с номером задачи,
функции сервисов — `NotImplementedError` с исполнителем в docstring.
Финальный README пишется в T-022.
