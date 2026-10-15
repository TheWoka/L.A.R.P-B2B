# Архитектура

## Конвейер обработки документа

```
 Браузер (React, :3000)
    │  POST /api/documents (файл + назначение)      ← ответ за < 1 с: {task_id, document_id}
    ▼
 API (FastAPI, :8000) ── сохраняет файл в том uploads, пишет Task в PostgreSQL,
    │                    ставит задачу process_document в очередь Redis
    ▼
 Worker (Celery) ─── process_document(task_id):
    1. extracting  services/extraction  extract()      → ExtractedDocument (текст + страницы/разделы)
    2. detecting   services/detection   detect()       → list[Entity]  (regex + NER + словари)
                   services/markers     find_markers() → list[Marker]  (согласования, гриф, срок хранения…)
    3. masking     services/masking     mask()         → MaskResult    ([PERSON_1], [SNILS_1]…)
    4. checking    services/rules       evaluate()     → list[Violation]
                   └─ условия типа llm: services/llm (только masked_text) + services/rag (статьи законов)
    5. reporting   services/report      build_report() → Report (светофор, чек-лист, нарушения)
    │  после каждой стадии обновляет Task.stage / progress; исходный файл удаляется
    ▼
 PostgreSQL: documents, tasks, reports (JSONB), rules (JSONB), векторы статей законов (pgvector)
    ▲
 Браузер опрашивает GET /api/tasks/{task_id} → при done открывает GET /api/reports/{report_id}
```

Ключевая идея: **в LLM уходит только текст после стадии masking**. `MaskResult.mapping`
(плейсхолдер → исходное значение) живёт только в памяти воркера, не логируется и не сохраняется.

## Сервисы docker-compose

| Сервис | Образ / сборка | Порт | Назначение |
|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | — | БД: документы, задачи, отчёты, правила, векторы законов. Том `pgdata` |
| `redis` | `redis:7-alpine` | — | Брокер очереди Celery и кэш ответов LLM |
| `api` | `backend/Dockerfile` | 8000 | FastAPI: REST API, Swagger на `/docs` |
| `worker` | `backend/Dockerfile` | — | Celery: вся тяжёлая обработка документов |
| `frontend` | `frontend/Dockerfile` (node → nginx) | 3000 | Интерфейс; nginx проксирует `/api/` на `api:8000` |

Общие тома: `uploads` (`/data/uploads`) у api и worker; `./data/rules` и `./data/legal`
монтируются в оба сервиса только для чтения.

## Где что лежит

| Что | Где |
|---|---|
| Контракты (схемы, API, env) | `docs/contracts.md` → `backend/app/schemas/`, `frontend/src/api/types.ts` |
| Правила проверки | `data/rules/rules.yaml`, описание — `docs/rules.md` |
| Тексты законов для RAG | `data/legal/*.md` |
| Оценка качества | `eval/` |
