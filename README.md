# Гриф — ИИ-аудитор безопасности документов

[![CI](https://github.com/TheWoka/L.A.R.P-B2B/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TheWoka/L.A.R.P-B2B/actions/workflows/ci.yml)

Проверяет документы закупок на персональные данные (152-ФЗ и GDPR), сведения о критической
информационной инфраструктуре — КИИ (187-ФЗ) и коммерческую тайну (98-ФЗ); выдаёт отчёт
со светофором, нарушениями, цитатами, статьями законов и рекомендациями.

**Что умеет (этапы 1 и 2):** принимает PDF, DOCX, XLSX, XML-извещения ЕИС (44-ФЗ и 223-ФЗ),
вставленный текст, сканы PDF без текста и изображения PNG, JPG, TIFF (локальное распознавание
Tesseract, уверенность показана в отчёте); проверяет один документ или комплект до 20 файлов
со сводным отчётом и выгрузкой в PDF / Markdown / JSON; проверяет согласования, грифы
и сроки хранения; заменяет найденные персональные данные плейсхолдерами до обращения
к нейросети; делает обезличенную копию, в том числе сканов; режим «Без нейросети / Локально /
Облако» выбирается при загрузке, по умолчанию — без нейросети и без ключа.

[Как читать отчёт «Гриф»](docs/user-guide.md) — короткая памятка для юриста и специалиста по закупкам.

[Архитектура и путь документа](docs/architecture.md) — схемы, модули и границы безопасности.

🎬 **Видео о проекте** — команда, техническое решение и работа приложения. [Скачать mp4](https://github.com/TheWoka/L.A.R.P-B2B/raw/main/docs/video/grif_video.mp4)

**Для комиссии:** запуск — раздел «Быстрый старт» (API-ключ не нужен), затем таблица
[«Демо: какой документ в какую проверку»](#демо-какой-документ-в-какую-проверку).
Чем подтверждён каждый пункт задания — в таблице «Соответствие требованиям» ниже.

## Соответствие требованиям

Строка таблицы — пункт задания своими словами, способ выполнения и доказательство, которое
можно проверить: тест, команда или строка метрик с датой замера и набором.

- **Тест** записан как `файл::имя`. Весь набор запускается на каждый push
  ([.github/workflows/ci.yml](.github/workflows/ci.yml)) и командой
  `docker-compose run --rm --no-deps api pytest -q`. Один тест — тем же вызовом с путём
  внутри образа, без `backend/`: `… api pytest -q tests/test_batches.py::test_create_upload_get`.
  Тестам, читающим демо-файлы, нужна папка демо:
  добавьте `-v "${PWD}/data/demo:/data/demo:ro"` перед `api`, иначе они будут пропущены.
- **check_demo** — сверка демо-документов с ключом ответов
  [data/demo/answers.yaml](data/demo/answers.yaml) (итог светофора, число сущностей по типам,
  список нарушений) на запущенной системе; `--only 11` проверяет один файл:
  `docker-compose run --rm --no-deps -v "${PWD}/data/demo:/data/demo:ro" api python /data/demo/check_demo.py --api http://api:8000`.
- **Метрики** — опубликованные файлы `eval/results/<набор>/metrics.json`; дата — поле
  `created_at`. Полнота — доля найденных размеченных сущностей; как считается и как
  повторить замер — раздел [«Качество»](#качество).
- Тесты интерфейса лежат в `frontend/tests` (node:test) и указаны как `файл` — «название теста»; запуск — `cd frontend && npm test` (Node 22+), в CI — на каждый PR.

### Этап 1

| Требование | Как выполнено | Чем доказано |
|---|---|---|
| Загрузка PDF, DOCX, XLSX и ввод текста | `POST /api/documents` проверяет расширение и сигнатуру файла, `POST /api/queries` принимает текст; текст извлекается с привязкой к страницам и разделам | `backend/tests/test_extraction.py::test_pdf_pages_and_sections`, `::test_docx_order_and_sections`, `::test_xlsx_sections_and_values`<br>`backend/tests/test_api_documents.py::test_upload_docx`, `::test_query`<br>check_demo, демо 01–04 (DOCX, PDF, XLSX) |
| Найдено не менее 90% персональных данных | Регулярные выражения с контрольными суммами, Natasha NER, словари, классификатор grif-cls; режим по умолчанию, без генеративной нейросети и ключа | `eval/results/blind/metrics.json` — слепой набор `eval/blind`, 12 документов, 26.10.2026: полнота ПДн **92,8%**<br>`eval/results/blind2/metrics.json` — второй слепой набор `eval/blind2`, 12 документов, 12.11.2026: **91,0%** |
| Найдено не менее 85% коммерческой тайны | Словари и классификатор grif-cls, без генеративной нейросети. На первом слепом наборе цель достигнута, на втором — нет (см. [«Известные ограничения»](#известные-ограничения)) | `eval/results/blind/metrics.json` — `eval/blind`, 26.10.2026: полнота КТ **91,7%**<br>`eval/results/blind2/metrics.json` — `eval/blind2`, 12.11.2026: 64,1%<br>`eval/results/blind2-llm/metrics.json` — тот же набор с облачной нейросетью, 12.11.2026: 82,1% |
| Маскирование персональных данных до обращения к нейросети | Найденные ПДн заменяются плейсхолдерами `[PERSON_1]`, `[SNILS_1]`; правила и нейросеть получают только текст с масками | `backend/tests/test_pipeline.py::test_run_audit_stages_and_masking`<br>`backend/tests/test_properties.py::test_mask_removes_pd_values_from_text_for_llm` (случайные тексты, Hypothesis)<br>`backend/tests/test_detect.py::test_detect_then_mask_hides_all_pd` |
| Чек-лист из трёх категорий | Согласования, гриф конфиденциальности, срок хранения: у каждого пункта статус и пояснение на русском | `backend/tests/test_report_builder.py::test_checklist_status_table`<br>`backend/tests/test_e2e.py::test_contract_docx` |
| Нарушения со ссылкой на норму и рекомендацией | Нарушение наследует от правила статьи законов и рекомендацию; ссылка открывает текст статьи из `data/legal` | `backend/tests/test_rules_engine.py::test_entity_evidence_uses_masked_values_and_preserves_location`<br>`backend/tests/test_rules_catalog.py::test_catalog_law_refs_open_local_articles` |
| Светофор | Красный — есть серьёзное нарушение, жёлтый — только замечания, зелёный — нарушений нет | `backend/tests/test_report_builder.py::test_overall_depends_only_on_violations`<br>check_demo: итог каждого из 12 демо-документов |
| Файл на 50+ страниц без ошибки 504 | Запрос на загрузку сразу возвращает `202` и `task_id`, проверка идёт в очереди Celery | `backend/tests/test_large_documents.py::test_large_document_pipeline` — PDF 60 страниц, DOCX 70 разделов, XLSX; отчёт готов быстрее 120 с |
| Запуск одной командой | `docker-compose up --build`; ключ и внешние сервисы не нужны | Раздел [«Быстрый старт»](#быстрый-старт): `curl.exe -sS http://localhost:8000/api/health` → `{"status":"ok","db":true,"redis":true}`<br>`backend/tests/test_health.py::test_health_all_up` |
| Интерфейс понятен юристу | Текст интерфейса на русском, без технических терминов; светофор, чек-лист, цитаты и рекомендации на одной странице | Проверяется вручную по разделу [«Как пользоваться»](#как-пользоваться) и [памятке](docs/user-guide.md) со снимками экрана; автоматического теста понятности нет |
| Правила редактируются без кода | Страница «Правила» и `POST` / `PUT` / `DELETE /api/rules`; изменение действует на следующие проверки | `backend/tests/test_api_rules.py::test_create_and_duplicate`, `::test_update_preserves_position_and_persists`<br>`backend/tests/test_rules_in_pipeline.py::test_rule_toggle_changes_new_reports_and_ruleset_version` |

### Этап 2, задача A — комплект документов

| Требование | Как выполнено | Чем доказано |
|---|---|---|
| Несколько документов проверяются одним комплектом | `POST /api/batches` создаёт комплект, файлы загружаются по одному с `batch_id`; до 20 файлов | `backend/tests/test_batches.py::test_create_upload_get`, `::test_document_limit`<br>`frontend/tests/batch.test.mjs` — «accepts scans and propagates the batch id on every upload» |
| Общий уровень риска комплекта | Общий светофор — худший среди готовых документов | `backend/tests/test_batches.py::test_summary_ready_overall`, `::test_summary_mixed_statuses` |
| Нарушения по уровням | Счётчики серьёзных нарушений и замечаний; список правил «Что исправить в первую очередь»: сначала серьёзные, затем по числу документов | `backend/tests/test_batches.py::test_summary_mixed_statuses`, `::test_rule_frequency_severity_and_stale_report` |
| Перечень документов с итогами | У каждого документа — статус, светофор, число нарушений, отметка о распознавании, ссылка на отчёт | `backend/tests/test_batches.py::test_summary_mixed_statuses`<br>`backend/tests/test_contract_v3.py::test_batch_summary_matches_mock` |
| Выгрузка сводки | `GET /api/batches/{id}/export?format=pdf`, `md` или `json`; значений ПДн в сводке нет | `backend/tests/test_batches.py::test_export_privacy_and_current_status` (три формата) |
| Страница сводного отчёта | `/batches/{id}` — «Сводный отчёт по комплекту» | [frontend/src/pages/BatchSummaryPage.tsx](frontend/src/pages/BatchSummaryPage.tsx); вручную — раздел «Как пользоваться», шаг 4; данные страницы — `backend/tests/test_batches.py::test_create_upload_get` |
| Результаты не теряются при закрытии страницы | Комплект и отчёты хранятся в PostgreSQL, сводка собирается при каждом чтении; страница открывается по сохранённой ссылке и из истории браузера. Срок хранения — 7 дней | `backend/tests/test_batches.py::test_get_latest_task_and_isolation`<br>`frontend/tests/history.test.mjs` — «batch history survives a read and never stores document filenames» |

### Этап 2, задача B — сканы и изображения

| Требование | Как выполнено | Чем доказано |
|---|---|---|
| Распознавание сканов и изображений | PDF без текстового слоя, PNG, JPG, многостраничный TIFF — локальный Tesseract (`rus+eng`); распознаются только страницы без текста | `backend/tests/test_extraction_ocr.py::test_scan_and_mixed_pdf`, `::test_png_extract_detect_and_private_logs`, `::test_multipage_tiff`, `::test_jpeg_exif_rotation`<br>`backend/tests/test_api_documents.py::test_upload_image_signatures` |
| Признак распознавания и надёжность в отчёте | Поле отчёта `ocr`: движок, уверенность по страницам, средняя уверенность, страницы и слова с низкой уверенностью; в сводке комплекта — отметка «Скан (OCR)» | `backend/tests/test_worker_pipeline.py::test_ocr_pipeline_progress_and_report`<br>`backend/tests/test_extraction_ocr.py::test_confidence_and_full_text_offsets`<br>`backend/tests/test_contract_v3.py::test_report_accepts_ocr_and_llm_mode` |
| Предупреждение о возможных ошибках | Плашка «Текст получен автоматическим распознаванием» в отчёте, предупреждение на странице комплекта и в выгрузке сводки | `backend/tests/test_batches.py::test_export_privacy_and_current_status` (предупреждение есть в выгрузке)<br>[frontend/src/components/ReportView.tsx](frontend/src/components/ReportView.tsx) — блок `ocr-notice`; [памятка, раздел 10](docs/user-guide.md) |
| Проверка на примере скана: от распознавания до поиска ПДн | Распознанный текст идёт по тому же конвейеру: поиск → маскирование → правила → отчёт. Демо 11 (PDF-скан) и 12 (JPG) | check_demo `--only 11`, `--only 12`<br>`backend/tests/test_extraction_ocr.py::test_mixed_page_detects_pd_in_image`<br>`eval/results/scan-blind/metrics.json` — сканы слепого набора, 12 документов, 12.11.2026: ПДн 91,9%, КТ 87,5%<br>`eval/results/scan-dev/metrics.json` — сканы набора для настройки, 15 документов, 11.11.2026: ПДн 95,2%, КТ 89,1% |

### Сверх задания

| Что сделано | Как выполнено | Чем доказано |
|---|---|---|
| XML-извещения ЕИС (44-ФЗ, 223-ФЗ) | Безопасный разбор (defusedxml), поля извещения проверяются как текст | `backend/tests/test_extraction_xml.py::test_fixture_fio_is_one_segment_and_detected`<br>`backend/tests/test_e2e.py::test_publication_xml`<br>check_demo, демо 09 и 10 |
| Обезличенная копия, включая сканы | Найденные ПДн удаляются из DOCX, XLSX, PDF, XML; в сканах закрашиваются пиксели; копия выдаётся только после самопроверки | `backend/tests/test_redaction.py::test_docx_redaction`, `::test_pdf_redaction_removes_text`, `::test_self_check_failure_hides_copy`<br>`backend/tests/test_redaction_xml.py::test_notice_copy_is_clean_valid_and_keeps_prefixes`<br>`backend/tests/test_redaction_ocr.py::test_demo_scans_copy_has_no_pd`, `::test_redact_image_paints_pixels_not_overlay` |
| «Что видела нейросеть» | Журнал обращений к нейросети с замаскированным текстом — в отчёте и через `GET /api/reports/{id}/llm-log` | `backend/tests/test_llm_conditions.py::test_worker_saves_llm_log_and_api_returns_it` |
| След проверки | В отчёте — хеш документа, версия набора правил и словарей, режим и модель нейросети, число обращений | `backend/tests/test_pipeline.py::test_audit_hashes`<br>`backend/tests/test_llm_modes.py::test_report_audit_has_document_mode` |
| Режимы нейросети | «Без нейросети / Локально / Облако» выбирается для каждой проверки; недоступный режим показан с причиной; ключ наружу не отдаётся | `backend/tests/test_llm_modes.py::test_modes_endpoint_never_returns_key`, `::test_mock_mode_makes_no_requests`, `::test_upload_cloud_without_key_is_400` |
| Слепые наборы и A/B-сравнение моделей | Два слепых набора, по одному прогону; дообученный классификатор v3 сравнён с выпущенным v2 по правилу, записанному до прогона, — в релизе остался v2 | `eval/results/blind/metrics.json` (26.10.2026), `eval/results/blind2/metrics.json` (12.11.2026)<br>[eval/results/blind2-ab/ab.md](eval/results/blind2-ab/ab.md) — `eval/blind2`, 12.11.2026<br>`backend/tests/test_paired.py::test_mcnemar_exact`, `::test_build_report_picks_b_only_when_rule_passes` |

## Быстрый старт

Нужен запущенный Docker Desktop с Linux-контейнерами, свободные порты 3000 и 8000
и доступ в интернет для первой сборки. Команды ниже — из корня репозитория в PowerShell.

Первая сборка скачивает зависимости, Tesseract и языковые пакеты; время зависит от сети.
Проверка 12.11.2026 на чистой машине (Docker очищен, свежий клон в пути с кириллицей и пробелом):
сборка и запуск заняли **23 мин 36 с** на медленном канале; повторные запуски — секунды.
Дождитесь, пока все пять сервисов станут `healthy` (`docker-compose ps`).

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
$env:COMPOSE_PROJECT_NAME = 'ai-auditor'
docker-compose build api
docker-compose up --build -d
docker-compose ps
curl.exe -sS http://localhost:8000/api/health
```

| Что | Адрес |
|---|---|
| Интерфейс | <http://localhost:3000> |
| API и Swagger | <http://localhost:8000/docs> |
| Проверка состояния | <http://localhost:8000/api/health> |
| Качество проверки | <http://localhost:3000/quality> |

При готовности API вернёт `{"status":"ok","db":true,"redis":true}`. Если соединение
ещё не установлено, повторите запрос через несколько секунд. `degraded` означает, что
API запущен, но БД или Redis недоступны. Если задача не завершается, проверьте
`docker-compose logs --tail=100 worker`.

Имя проекта задано явно, чтобы запуск работал и из папки с кириллицей. Сохраняйте это имя
в следующих командах Compose; в новом окне PowerShell задайте переменную повторно.
По умолчанию `LLM_DEFAULT_MODE=mock`: API-ключ не нужен. Первая загрузка зависимостей
зависит от скорости интернета; следующие запуски используют кэш Docker.
Если сборка завершилась `ReadTimeout` при скачивании пакетов, проверьте доступ к PyPI
и повторите сборку. Переходите к проверке API после успешного завершения запуска.

### Если сборка фронтенда упала с `tsc: not found`

Пересоберите фронтенд без кэша, затем запустите сервисы:

```powershell
docker-compose build --no-cache frontend
docker-compose up -d
```

## Как пользоваться

1. Откройте <http://localhost:3000>.
2. Перетащите PDF, DOCX, XLSX, XML, PNG, JPG/JPEG или TIFF (TIF) в область загрузки
   либо откройте вкладку «Вставить текст». Каждый файл — до 50 МБ.
   Можно выбрать до 20 файлов: при двух и более создаётся комплект. Укажите
   необязательное «Название комплекта» (до 120 символов), без личных сведений.
   Файлы отправляются на сервер по одному, проверяются в очереди Celery.
   Для XML обезличенная копия — тоже XML с той же разметкой; атрибуты и комментарии XML не проверяются.
3. Выберите назначение: «Для публикации», «Договор», «Внутренний документ»
   или «Международный контракт»; у отдельного файла его можно поменять в списке.
   Выберите режим «Без нейросети», «Локально» или «Облако» и начните проверку.
   В облачном режиме провайдер получает текст после маскирования найденных ПДн,
   включая распознанный текст сканов. КТ и финансы остаются открытыми; пропущенные
   детекторами ПДн тоже могут остаться. Файл или изображение в модель не отправляются.
4. Дождитесь окончания проверки на странице с ходом обработки. Документ обрабатывается
   в очереди; для одного файла откроется его отчёт, для комплекта — страница
   «Сводный отчёт по комплекту» (`/batches/{id}`). Общий светофор — худший среди
   готовых документов. Проверьте число ошибок, блок «Что исправить в первую очередь»
   и перейдите по ссылке «Открыть отчёт». Страницу можно обновить или открыть по
   сохранённой ссылке; комплект также доступен из истории этого браузера.
5. Прочитайте светофор: красный — серьёзное нарушение, жёлтый — замечания,
   зелёный — нарушения не найдены включёнными проверками. Изучите три пункта чек-листа,
   нарушения, рекомендации и цитаты; нажмите на ссылку статьи закона, чтобы открыть её текст.
   Кнопка «Скачать обезличенную копию» доступна, если копия создана; рядом можно скачать
   отчёт в JSON, PDF или Markdown. Из копии удаляются только ПДн — коммерческую тайну нужно убрать отдельно.
   Для комплекта кнопки «Скачать PDF», «Markdown», «JSON» выгружают сводку;
   отдельные отчёты и копии скачиваются со страниц документов.

## Что умеет

- Проверяет один документ, текстовый запрос или комплект до 20 файлов: общий светофор,
  приоритет исправлений и отчёт по каждому документу, выгрузка сводки в PDF / MD / JSON.
- Читает PDF без текста, PNG, JPG/JPEG и многостраничный TIFF локально через Tesseract
  (`rus+eng`), показывает уверенность распознавания и сомнительные слова.
- Позволяет выбрать режим нейросети при загрузке; детекторы, классификатор и чек-лист
  работают и в режиме «Без нейросети».

## Примеры API

Примеры 1–15 проверены 24.10.2026 в PowerShell 7.6, `LLM_PROVIDER=mock`
(дата отдельной проверки XML указана в 10а). Примеры этапа 2 — в шагах 16–20.
Выполняйте из корня
репозитория по порядку в одном окне; используется именно `curl.exe`, а не псевдоним
`curl` из Windows PowerShell. JSON записывается в UTF-8 без BOM: кавычки в аргументах
PowerShell не требуют ручного экранирования. Временные файлы лежат вне репозитория.

Подготовка (все сведения о человеке выдуманы):

```powershell
$api = 'http://localhost:8000/api'
$demoDir = Join-Path $env:TEMP 'grif-t022a-api'
New-Item -ItemType Directory -Force $demoDir | Out-Null
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText("$demoDir/query.json", '{"text":"Учебный документ. Контактное лицо: Петров Пётр Петрович, телефон +7 999 123-45-67.","purpose":"publication"}', [Text.UTF8Encoding]::new($false))
```

Идентификаторы ниже — из реального прогона; при повторении они будут другими.
Используйте переменные `$taskId` и `$reportId`, полученные из своих ответов.
Примеры ответов сокращены удалением полей или элементов; сохранённые значения не изменены.

### 1. Состояние — GET /api/health

```powershell
curl.exe -sS -w "`nHTTP %{http_code}`n" "$api/health"
```

HTTP 200:

```json
{"status":"ok","db":true,"redis":true}
```

### 2. Проверить текст — POST /api/queries

```powershell
curl.exe -sS -w "`nHTTP %{http_code}`n" -X POST "$api/queries" -H "Content-Type: application/json" --data-binary "@$demoDir/query.json" -o "$demoDir/created.json"
Get-Content -Raw -Encoding UTF8 "$demoDir/created.json"
$created = Get-Content -Raw -Encoding UTF8 "$demoDir/created.json" | ConvertFrom-Json
$taskId = $created.task_id
```

HTTP 202, `TaskCreated` (отчёта в этом ответе ещё нет):

```json
{"task_id":"b2f9aa38fc914dfabe904ab53d845a0c","document_id":"f65e352790c84ef78a0a16e41a136d7b"}
```

### 3. Дождаться отчёта — GET /api/tasks/{task_id}

```powershell
curl.exe -sS -w "`nHTTP %{http_code}`n" "$api/tasks/$taskId" -o "$demoDir/task.json"
Get-Content -Raw -Encoding UTF8 "$demoDir/task.json"
```

HTTP 200 после завершения:

```json
{"task_id":"b2f9aa38fc914dfabe904ab53d845a0c","status":"done","stage":"reporting","progress":100,"report_id":"1b9264f720ec4d6ca7c757c6d1d6c332","error":null}
```

При `queued` или `processing` повторите две команды через пару секунд.
При `failed` прочитайте `error`. Продолжайте только после `done`:

```powershell
$task = Get-Content -Raw -Encoding UTF8 "$demoDir/task.json" | ConvertFrom-Json
if ($task.status -ne 'done') { throw 'Дождитесь done и повторите чтение task.json' }
$reportId = $task.report_id
```

### 4. Отчёт — GET /api/reports/{report_id}

```powershell
curl.exe -sS -w "`nHTTP %{http_code}`n" "$api/reports/$reportId"
```

HTTP 200; оставлены `overall`, одно нарушение (с одной цитатой) и все три пункта `checklist`:

```json
{
  "overall": "red",
  "violations": [
    {
      "rule_id": "PD_IN_PUBLICATION",
      "severity": "red",
      "law_refs": [
        {
          "law": "152-ФЗ",
          "article": "ст. 7"
        },
        {
          "law": "152-ФЗ",
          "article": "ст. 10.1"
        }
      ],
      "evidence": [
        {
          "quote": "П█████ П███ П███████",
          "start": 35,
          "end": 55
        }
      ],
      "recommendation": "Удалите или обезличьте ПДн перед публикацией либо получите согласие субъекта на распространение.",
      "source": "rule"
    }
  ],
  "checklist": [
    {
      "category": "approvals",
      "status": "not_required",
      "comment": "Для этого назначения лист согласования не требуется"
    },
    {
      "category": "classification",
      "status": "not_required",
      "comment": "Для этого документа гриф не требуется"
    },
    {
      "category": "retention",
      "status": "not_required",
      "comment": "Для найденных данных указание срока хранения не требуется"
    }
  ]
}
```

### 5. Текст с масками — GET /api/reports/{report_id}/text

```powershell
curl.exe -sS -w "`nHTTP %{http_code}`n" "$api/reports/$reportId/text"
```

HTTP 200, `ReportText`:

```json
{"text":"Учебный документ. Контактное лицо: П█████ П███ П███████, телефон +█ ███ ███-██-██.","segments":[{"id":0,"text":"Учебный документ. Контактное лицо: П█████ П███ П███████, телефон +█ ███ ███-██-██.","offset":0,"page":null,"section":null}]}
```

В `/text` длина текста сохранена, смещения `start/end/offset` совпадают с исходными.
Это маски для подсветки в отчёте; нейросеть получает плейсхолдеры вида `[PERSON_1]`.

### 6. Журнал нейросети — GET /api/reports/{report_id}/llm-log

```powershell
curl.exe -sS -w "`nHTTP %{http_code}`n" "$api/reports/$reportId/llm-log"
```

HTTP 200 в mock:

```json
[]
```

### 7. Обезличенная копия — GET /api/reports/{report_id}/redacted

```powershell
curl.exe -sS -w "`nHTTP %{http_code}`n" "$api/reports/$reportId/redacted" -o "$demoDir/redacted.txt"
Get-Content -Raw -Encoding UTF8 "$demoDir/redacted.txt"
```

HTTP 200, файл TXT для текстового запроса:

```text
Учебный документ. Контактное лицо: [ФИО], телефон [ТЕЛЕФОН].
```

Для загруженных документов возвращается исходный формат. Если копия не создана,
ответ — HTTP 404 `{"detail":"Обезличенная копия недоступна"}`; проверьте
`redacted_available` в отчёте. Коммерческая тайна и финансы в копии сохраняются.

### 8. Скачать отчёт — GET /api/reports/{report_id}/export?format=json

```powershell
curl.exe -sS -w "`nHTTP %{http_code}`n" "$api/reports/$reportId/export?format=json" -o "$demoDir/report-export.json"
```

HTTP 200, JSON-файл; фрагмент содержимого:

```json
{"report_id":"1b9264f720ec4d6ca7c757c6d1d6c332","purpose":"publication","overall":"red","llm_used":false,"redacted_available":true}
```

### 9. Правила — GET /api/rules

```powershell
curl.exe -sS -w "`nHTTP %{http_code}`n" "$api/rules"
```

HTTP 200; первое правило, часть полей:

```json
[{"id":"PD_IN_PUBLICATION","title":"Персональные данные в документе для публикации","severity":"red","law_refs":[{"law":"152-ФЗ","article":"ст. 7","title":null,"url":null},{"law":"152-ФЗ","article":"ст. 10.1","title":null,"url":null}],"enabled":true}]
```

### 10. Проверить документ — POST /api/documents

```powershell
curl.exe -sS -w "`nHTTP %{http_code}`n" -X POST "$api/documents" -F "file=@data/demo/01_tz_publication.docx" -F "purpose=publication"
```

HTTP 202:

```json
{"task_id":"801b29064587403d85efb7ffc078b798","document_id":"6390290762e041cbb0e64c2c0c3cac90"}
```

Для этой новой задачи повторите шаги 3–8 с её `task_id`. Форматы загрузки:
`.pdf`, `.docx`, `.xlsx`, `.xml`, `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, до 50 МБ;
неподдерживаемый файл — HTTP 400.

### 10а. Проверить XML-извещение ЕИС — POST /api/documents

Учебное извещение по 44-ФЗ содержит только выдуманные данные. Команда проверена
30.10.2026 в PowerShell 7.6.5, `LLM_PROVIDER=mock`.
Выполняйте из корня репозитория в том же окне PowerShell:

```powershell
curl.exe -sS -w "`nHTTP %{http_code}`n" -X POST "$api/documents" -F "file=@backend/tests/fixtures/synthetic_notice_44fz.xml" -F "purpose=publication" -o "$demoDir/xml-created.json"
Get-Content -Raw -Encoding UTF8 "$demoDir/xml-created.json"
$created = Get-Content -Raw -Encoding UTF8 "$demoDir/xml-created.json" | ConvertFrom-Json
$taskId = $created.task_id
```

HTTP 202:

```json
{"task_id":"c7524f4ebebc44db9945c982ff9d0b1a","document_id":"32b9291dfe184d2fa14ee416461cfcef"}
```

Для получения отчёта повторите шаги 3–8 с новым `$taskId`.
Обезличенная копия этого документа скачивается в XML.

### 11. Метрики — GET /api/metrics

```powershell
curl.exe -sS -w "`nHTTP %{http_code}`n" "$api/metrics"
```

HTTP 200; в `by_type` оставлен один тип:

```json
{
  "corpus": "seed",
  "documents": 31,
  "created_at": "2026-10-24T11:49:20.864433Z",
  "by_type": [
    {
      "type": "PERSON",
      "precision": 1.0,
      "recall": 0.9804,
      "f1": 0.9901,
      "support": 51
    }
  ],
  "pd_recall": 0.9925,
  "trade_secret_recall": 1.0,
  "matching": "тот же type и пересечение ≥ 50% длины эталонной сущности; один к одному, жадно по убыванию пересечения"
}
```

Если файл метрик ещё не создан, HTTP 404 `{"detail":"Метрики ещё не посчитаны"}`.
Команда расчёта — в разделе «Качество» ниже.

### 12. Добавить правило — POST /api/rules

Для проверки управления правилами создайте временное **выключенное** учебное правило.
После PUT удалите его шагом 14; исходный YAML-каталог не изменяется.

```powershell
$rule = '{"id":"DEMO_T022A","title":"Учебное правило","description":"Выдуманный пример управления правилами","severity":"yellow","law_refs":[{"law":"Учебная политика","article":"п. 1"}],"applies_to":["internal"],"condition":{"type":"purpose_is","purposes":["internal"]},"recommendation":"Учебная рекомендация","enabled":false}'
[IO.File]::WriteAllText("$demoDir/rule.json", $rule, [Text.UTF8Encoding]::new($false))
curl.exe -sS -w "`nHTTP %{http_code}`n" -X POST "$api/rules" -H "Content-Type: application/json" --data-binary "@$demoDir/rule.json"
```

HTTP 201, фрагмент:

```json
{"id":"DEMO_T022A","title":"Учебное правило","enabled":false}
```

Если предыдущий прогон оборвался и `DEMO_T022A` уже существует, POST вернёт HTTP 409;
продолжите с PUT и затем DELETE этого учебного правила.

### 13. Изменить правило — PUT /api/rules/{id}

PUT принимает полный `Rule`; `id` в пути и теле должны совпадать.

```powershell
[IO.File]::WriteAllText("$demoDir/rule.json", $rule.Replace('Учебное правило','Учебное правило (обновлено)'), [Text.UTF8Encoding]::new($false))
curl.exe -sS -w "`nHTTP %{http_code}`n" -X PUT "$api/rules/DEMO_T022A" -H "Content-Type: application/json" --data-binary "@$demoDir/rule.json"
```

HTTP 200, фрагмент:

```json
{"id":"DEMO_T022A","title":"Учебное правило (обновлено)","enabled":false}
```

### 14. Удалить учебное правило — DELETE /api/rules/{id}

```powershell
curl.exe -sS -w "`nHTTP %{http_code}`n" -X DELETE "$api/rules/DEMO_T022A"
```

HTTP 204, тело пустое.

### 15. Скачать отчёт в PDF — GET /api/reports/{report_id}/export?format=pdf

```powershell
curl.exe -sS -w "`nHTTP %{http_code}`n" "$api/reports/$reportId/export?format=pdf" -o "$demoDir/report.pdf"
```

HTTP 200, файл PDF (проверен заголовок `%PDF-`), `Content-Type: application/pdf`.

Отчёты и обезличенные копии хранятся `REPORT_TTL_DAYS` дней (по умолчанию 7);
исходные файлы удаляются после обработки. После удаления отчёта создайте новую проверку.

### 16. Этап 2: доступные режимы — GET /api/llm/modes

Шаги 16–20 выполняются независимо от старых примеров, по порядку в одном окне
PowerShell из корня репозитория. Данные синтетические, режим проверки — `mock`.
Все новые команды требуют запущенного API и worker из текущего `main`.
Все команды шагов 16–20 выполнены **12.11.2026**, PowerShell 7.6.9, `llm_mode=mock`,
исходники `main` на коммите `b41cb30`. Использован проверочный образ из локального
кэша с текущими исходниками, Tesseract 5.5.0 и pytesseract 0.3.13; все 20 закреплённых
runtime-зависимостей совпали с `backend/requirements.txt`, worker — один процесс.
Это проверка API. Чистая сборка и запуск одной командой подтверждены отдельно (T-079, 12.11.2026,
коммит `1730917`): все сервисы `healthy`, демо 12/12 обработаны (9 совпали с ключом, 07/08/09 —
описанные расхождения классификатора), `pytest` 2139 passed / 3 xfailed.

| Команда | Результат проверки 12.11.2026 |
|---|---|
| `GET /api/llm/modes` | 200, `mock` доступен, `local` / `cloud` недоступны с причинами |
| `POST /api/batches` | 201, получен `batch_id` |
| `POST /api/documents`, DOCX с `batch_id`, `llm_mode=mock` | 202, получены идентификаторы задачи и документа |
| `POST /api/documents`, PNG с `batch_id`, `llm_mode=mock` | 202, после проверки `ocr=true` |
| `GET /api/batches/{id}` | 200, `complete=true`, `documents_done=2`, `documents_failed=0`, `overall=red` |
| `GET /api/batches/{id}/export?format=pdf` | 200, файл с заголовком `%PDF-` |
| `GET /api/batches/{id}/export?format=md` | 200, Markdown-сводка |
| `GET /api/batches/{id}/export?format=json` | 200, JSON-сводка |

```powershell
$api = 'http://localhost:8000/api'
$demoDir = Join-Path $env:TEMP 'grif-t078-api'
New-Item -ItemType Directory -Force $demoDir | Out-Null
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
curl.exe --fail-with-body -sS -w "`nHTTP %{http_code}`n" "$api/llm/modes" -o "$demoDir/modes.json"
Get-Content -Raw -Encoding UTF8 "$demoDir/modes.json"
```

HTTP 200: `default` и три элемента `modes` (`mock`, `local`, `cloud`), у каждого
`available`, `model`, `reason`. Ключ API в ответ не входит. `mock` доступен всегда;
если `available=false`, причина объясняет серый пункт интерфейса. Проверка облака
подтверждает наличие настроек, а не доступность провайдера или остаток средств.

### 17. Создать комплект — POST /api/batches

```powershell
[IO.File]::WriteAllText("$demoDir/batch.json", '{"name":"Учебный комплект T-078","purpose":"publication"}', [Text.UTF8Encoding]::new($false))
curl.exe --fail-with-body -sS -w "`nHTTP %{http_code}`n" -X POST "$api/batches" -H "Content-Type: application/json" --data-binary "@$demoDir/batch.json" -o "$demoDir/batch-created.json"
$batch = Get-Content -Raw -Encoding UTF8 "$demoDir/batch-created.json" | ConvertFrom-Json
if (!$batch.batch_id) { throw 'Комплект не создан: проверьте ответ API' }
$batchId = $batch.batch_id
```

HTTP 201, `{"batch_id":"…"}`. Название необязательно, до 120 символов;
не включайте в него ПДн. Пока документы не загружены, сводка пустая.

### 18. Добавить документы и PNG — POST /api/documents с batch_id и llm_mode

Сначала документ с текстом, затем скан. PNG готовится из синтетического демо 12
средствами Windows вне репозитория; переименование JPG в PNG не подходит.

```powershell
curl.exe --fail-with-body -sS -w "`nHTTP %{http_code}`n" -X POST "$api/documents" -F "file=@data/demo/01_tz_publication.docx" -F "purpose=publication" -F "batch_id=$batchId" -F "llm_mode=mock" -o "$demoDir/doc-created.json"
Get-Content -Raw -Encoding UTF8 "$demoDir/doc-created.json"
Add-Type -AssemblyName System.Drawing
$scan = [Drawing.Image]::FromFile((Join-Path $PWD 'data/demo/12_scan_anketa.jpg'))
try { $scan.Save("$demoDir/scan.png", [Drawing.Imaging.ImageFormat]::Png) } finally { $scan.Dispose() }
curl.exe --fail-with-body -sS -w "`nHTTP %{http_code}`n" -X POST "$api/documents" -F "file=@$demoDir/scan.png" -F "purpose=internal" -F "batch_id=$batchId" -F "llm_mode=mock" -o "$demoDir/png-created.json"
Get-Content -Raw -Encoding UTF8 "$demoDir/png-created.json"
```

Оба запроса — HTTP 202 с `task_id` и `document_id`; распознавание и проверка идут
в Celery. Назначение PNG переопределено на `internal`. Загрузка по одному файлу,
до 20 в комплекте; 21-й — HTTP 400, неизвестный `batch_id` — 404,
недоступный `llm_mode` — 400 с причиной. Без `llm_mode` используется режим сервера
по умолчанию. Без `batch_id` создаётся обычная отдельная проверка.

### 19. Прочитать сводный отчёт — GET /api/batches/{id}

```powershell
curl.exe --fail-with-body -sS -w "`nHTTP %{http_code}`n" "$api/batches/$batchId" -o "$demoDir/summary.json"
$summary = Get-Content -Raw -Encoding UTF8 "$demoDir/summary.json" | ConvertFrom-Json
$summary | Select-Object complete, overall, documents_total, documents_done, documents_failed
```

HTTP 200. Пока `complete=false`, повторяйте эти три команды через несколько секунд.
После завершения обоих документов ожидаются `documents_total=2`, `documents_done=2`,
`documents_failed=0`, `overall=red`; у скана `documents[].ocr=true`.
`rules` группирует нарушения по правилам с числом документов; значения ПДн и цитаты
в сводку не входят. Страница интерфейса: `http://localhost:3000/batches/<batchId>`.
`complete=true` означает отсутствие выполняющихся задач, включая завершение с ошибкой;
обязательно смотрите `documents_failed` и `documents[].error`.

### 20. Выгрузить сводку — GET /api/batches/{id}/export?format=pdf

Дождитесь готовности всех документов, чтобы выгрузить полный результат.

```powershell
if (!$summary.complete -or $summary.documents_total -ne 2 -or $summary.documents_failed -ne 0) { throw 'Проверьте готовность и ошибки комплекта в шаге 19' }
curl.exe --fail-with-body -sS -w "`nHTTP %{http_code}`n" "$api/batches/$batchId/export?format=pdf" -o "$demoDir/batch.pdf"
curl.exe --fail-with-body -sS -w "`nHTTP %{http_code}`n" "$api/batches/$batchId/export?format=md" -o "$demoDir/batch.md"
curl.exe --fail-with-body -sS -w "`nHTTP %{http_code}`n" "$api/batches/$batchId/export?format=json" -o "$demoDir/batch-export.json"
```

HTTP 200 для каждого формата. PDF начинается с `%PDF-`, JSON повторяет структуру
`BatchSummary`. Это сводка комплекта, отдельные отчёты и обезличенные копии
в неё не вложены. Все временные файлы находятся в `$demoDir`, вне репозитория.

## Какие модели используются

| Слой | Модель | Где работает | Нужен ли ключ |
|---|---|---|---|
| Поиск имён | Natasha NER 1.6.0 | в контейнере worker, CPU | нет |
| Коммерческая тайна и особые категории ПДн | **grif-cls** — наш классификатор на базе `cointegrated/rubert-tiny2` (29 млн параметров, ONNX 34 МБ), обучен дистилляцией на ≈12 тыс. синтетических фрагментов ([карточка модели](data/models/grif-cls/card.md)) | в контейнере worker, CPU | нет — работает и без генеративной нейросети, если файлы модели установлены |
| Генеративная нейросеть (необязательно) | **gpt-4o-mini** (OpenAI) через OpenAI-совместимый API — с ней получены цифры «+ облачная нейросеть» в разделе «Качество»; подходит любая модель с OpenAI-совместимым API | у провайдера | да, свой ключ в `.env` |
| Генеративная нейросеть локально (необязательно) | GPU 10–12 ГБ — **Gemma 4 12B** (`gemma4:12b-it-qat`, рекомендуем); GPU 8 ГБ — Qwen3.5-9B; CPU — Qwen2.5-7B Q4_K_M ([выбор модели](docs/local-llm.md#какую-модель-выбрать-t-029b)) | Ollama 0.31.2 внутри Docker Compose, профиль `local-llm` | нет |

Обе цифры ТЗ (ПДн ≥ 90%, КТ ≥ 85%) выполняются без генеративной нейросети — словарями,
шаблонами и grif-cls. В любую генеративную модель уходит только текст с замаскированными ПДн.

## Режимы нейросети

Во всех режимах работают извлечение текста, регулярные выражения с проверкой контрольных
сумм, Natasha NER, словари, маскирование, обычные правила, три категории чек-листа,
отчёт и создание обезличенной копии. Локальный классификатор `grif-cls` ищет КТ и особые
категории ПДн на CPU без ключа и сети; если файлов модели нет, этот шаг пропускается
с предупреждением. Ниже «нейросеть» означает генеративную модель (LLM).

| Режим | Что работает и что добавляется | Что уходит наружу при проверке |
|---|---|---|
| **Без нейросети — по умолчанию**, `mock` | Базовая проверка выше, работает без ключа у всех. Поиск через LLM и условия правил `llm` не выполняются | Текст документа не отправляется внешней модели |
| **Облако**, `cloud` | Базовая проверка + поиск особых категорий ПДн, повторная маска, поиск коммерческой тайны и смысловые условия правил `llm` | Провайдер получает текст с замаскированными найденными ПДн; КТ и финансовые сведения в нём сохраняются |
| **Локально**, `local`, профиль Compose `local-llm` | Те же дополнительные проверки LLM; качество и скорость зависят от модели. Профили CPU и GPU — в [выборе модели](docs/local-llm.md#какую-модель-выбрать-t-029b) | Маскированный текст передаётся Ollama внутри сети Compose, облачному провайдеру не отправляется; для первичной загрузки образов и весов нужен интернет |

Выбор доступен при загрузке файла, комплекта и вводе текста. Серый пункт означает,
что режим недоступен: наведите указатель или переведите фокус на пункт, чтобы увидеть
причину. Для локального режима проверяются ответ Ollama и наличие скачанной модели;
для облачного — заполнены ли адрес, ключ и модель. Доступность обновляется с кэшем
до 10 секунд; после настройки обновите страницу. API показывает то же через
`GET /api/llm/modes`, ключи не возвращает.

Настройки из [.env.example](.env.example), меняются только в локальном `.env`:

| Переменная | Значение и назначение |
|---|---|
| `LLM_DEFAULT_MODE` | `mock` / `local` / `cloud`, по умолчанию в примере `mock`; интерфейс также запоминает доступный выбор в этом браузере |
| `LLM_LOCAL_BASE_URL` | `http://ollama:11434/v1` — Ollama внутри Compose |
| `LLM_LOCAL_MODEL` | Имя заранее скачанной модели; пусто — локальный режим недоступен |
| `LLM_CLOUD_BASE_URL` | Адрес OpenAI-совместимого API с `/v1` |
| `LLM_CLOUD_API_KEY` | Ключ провайдера, только в `.env` |
| `LLM_CLOUD_MODEL` | Имя облачной модели; для режима нужны все три `LLM_CLOUD_*` |
| `LLM_MAX_CALLS_PER_DOC` | Общий лимит реальных обращений, по умолчанию 30 |
| `LLM_TIMEOUT_S` | Таймаут запроса: по умолчанию 30 с, на CPU обычно 120 с |
| `LLM_REASONING_EFFORT` | Необязательно; `none` для локальных моделей с рассуждениями, в режиме `cloud` не передаётся |

Старые `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` продолжают работать.
Если `LLM_DEFAULT_MODE` отсутствует или пуст, `mock` берётся из `LLM_PROVIDER=mock`;
при `openai_compatible` адрес с хостом `ollama` или портом 11434 означает `local`,
остальные — `cloud`. Старые параметры заполняют незаданные параметры этого режима;
новые имеют приоритет. Чтобы пользоваться старым `.env`, не оставляйте поверх него
`LLM_DEFAULT_MODE=mock`, если нужен другой режим по умолчанию.

Профили локальной модели: GPU 10–12 ГБ — Gemma 4 12B, GPU 8 ГБ — Qwen3.5-9B
(для обеих `LLM_REASONING_EFFORT=none`, `LLM_TIMEOUT_S=60`), CPU — Qwen2.5-7B Q4_K_M
(`LLM_TIMEOUT_S=120`). Значения — в [.env.example](.env.example), видеокарта NVIDIA
подключается отдельным файлом `docker-compose.gpu.yml`; инструкция и замеры —
[docs/local-llm.md](docs/local-llm.md). На наборе `eval/dev` вся система с Gemma 4 12B
даёт ПДн 96,4% / КТ 96,4% (без генеративной нейросети — 95,2% / 89,1%).

Для OpenAI-совместимого API заполните **локальный** `.env` переменными из
[.env.example](.env.example):

```dotenv
LLM_DEFAULT_MODE=mock
LLM_CLOUD_BASE_URL=https://<адрес-провайдера>/v1
LLM_CLOUD_API_KEY=<ваш-ключ>
LLM_CLOUD_MODEL=<имя-модели-у-провайдера>
LLM_MAX_CALLS_PER_DOC=30
LLM_TIMEOUT_S=30
```

После изменения настроек выполните `docker-compose up -d --force-recreate api worker`.
После этого выберите «Облако» на странице загрузки. Для «Локально» задайте
`LLM_LOCAL_MODEL`, запустите профиль `local-llm` и скачайте модель по
[инструкции Ollama](docs/local-llm.md).
В нейросеть передаётся только текст с замаскированными ПДн (`[PERSON_1]`, `[PHONE_1]`);
его можно проверить в разделе отчёта «Что видела нейросеть» и через `/llm-log`.
Ключи не добавляйте в репозиторий. В mock `llm_used=false`; при включённой LLM это поле
отражает реальные вызовы, поэтому ответы только из кэша также дают `false`.
Таймаут или ответ без подтверждённой цитаты даёт `unknown`, а не доказательство
отсутствия нарушения; обычные проверки продолжают работать.

## Демо: какой документ в какую проверку

Все документы лежат в [data/demo](data/demo/README.md), данные в них выдуманные.
Загрузите файл на главной странице и выберите **назначение из второго столбца** — от него
зависит, какие правила применяются. Ожидания — для режима по умолчанию (без ключа).

| № | Файл | Назначение в интерфейсе | Итог | Что должен найти «Гриф» |
|---|---|---|---|---|
| 01 | `01_tz_publication.docx` — техзадание | «Для публикации» | 🔴 | ФИО и личный телефон сотрудника, себестоимость в документе для ЕИС → ПДн и коммерческая тайна в публикации |
| 02 | `02_contract_clean.docx` — договор | «Договор» | 🟢 | ФИО и паспорт есть, но условия обработки ПДн соблюдены — нарушений нет |
| 03 | `03_application_pd.pdf` — заявка участника | «Договор» | 🔴 | Паспорт, СНИЛС, ИНН, дата рождения без поручения обработки и согласия; нет согласования и срока хранения |
| 04 | `04_estimate.xlsx` — смета | «Внутренний документ» | 🔴 | Себестоимость и наценка без грифа «Коммерческая тайна»; нет согласования |
| 05 | `05_anketa_special.docx` — анкета | «Внутренний документ» | 🔴 | Сведения о здоровье (особые категории ПДн) без согласия; нет согласования, грифа и срока хранения |
| 06 | `06_medical_tz.docx` — техзадание на медизделия | «Для публикации» | 🟢 | Медицинские термины без привязки к человеку — это не ПДн; нарушений нет |
| 07 | `07_international_dpa.docx` — международный договор | «Международный контракт» | 🔴 | Передача ПДн в США без договора по Art. 28 GDPR (+ 1 лишняя находка КТ классификатора) |
| 08 | `08_kii_publication.docx` — техзадание | «Для публикации» | 🔴 | Сведения о значимом объекте КИИ в публикации, 187-ФЗ (+ 3 лишние находки КТ классификатора) |
| 09 | `09_eis_notice_44fz.xml` — извещение ЕИС, 44-ФЗ | «Для публикации» | 🔴 | Паспорт и СНИЛС члена комиссии в извещении, контакты ответственного лица (+ 1 лишняя находка КТ) |
| 10 | `10_eis_notice_223fz.xml` — извещение ЕИС, 223-ФЗ | «Для публикации» | 🔴 | Внутренний расчёт себестоимости заказчика и контакты в извещении |
| 11 | `11_scan_application.pdf` — заявка, скан, распознаётся OCR (`ocr: true`) | «Для публикации» (`publication`) | 🔴 | Два ФИО, личные телефон и почта, паспорт, СНИЛС, дата рождения → `PD_IN_PUBLICATION`, `CHECKLIST_NO_RETENTION`; ИНН организации (10 цифр) — не ПДн |
| 12 | `12_scan_anketa.jpg` — анкета для пропуска, скан, распознаётся OCR (`ocr: true`) | «Внутренний документ» (`internal`) | 🔴 | ФИО, телефон, адрес, группа инвалидности → `SPECIAL_PD_NO_CONSENT`, `CHECKLIST_NO_APPROVAL`, `CHECKLIST_NO_MARK`, `CHECKLIST_NO_RETENTION` |

Дополнительно — смысловое правило с генеративной нейросетью (нужен ключ в `.env`, раздел
«Режимы нейросети»): вкладка «Вставить текст», назначение «Договор», текст

```text
Договор поручения обработки персональных данных № 1/2026
АО «Банк Пример» (Оператор) поручает обработку персональных данных ООО «Бета-Пример» (Обработчик) в целях исполнения договора поставки.
1. Обработчик обрабатывает персональные данные по поручению оператора только в целях, указанных в договоре.
5.1. Обработчик принимает необходимые меры для защиты персональных данных.
5.2. Обработчик обеспечивает конфиденциальность и безопасность персональных данных надлежащим образом.
Контактное лицо Оператора: Петров Пётр Петрович, тел. +7 (900) 000-00-01, petrov@example.ru.
```

Ожидается замечание «Меры защиты ПДн описаны формально» с источником «нейросеть» и цитатой
из п. 5.1. Без ключа это условие не проверяется — отчёт честно показывает режим без LLM.

Лишние находки в 07, 08 и 09 — известные ошибки классификатора, описаны в
[data/demo/README.md](data/demo/README.md#расхождения-t-046); после финального замера детекцию
под демо не подстраивали. Можно выбрать несколько файлов сразу и задать назначение каждому.
Автоматическая сверка всех демо с ключом ответов:
`docker-compose run --rm --no-deps -v "${PWD}/data/demo:/data/demo:ro" api python /data/demo/check_demo.py --api http://api:8000`.

## Качество

Текстовые документы проверяем на четырёх синтетических наборах с разными ролями;
отдельно оцениваем сканы набора для настройки:

| Набор | Как используется |
|---|---|
| Эталонный `eval/seed` | Составлен вместе с детектором: проверяем, что изменения ничего не сломали. Высокий результат здесь не доказывает качество на новых документах |
| Для настройки [eval/dev](eval/dev/README.md) | Написан другим автором независимо от seed и реализации детектора; на нём разбираем ошибки и настраиваем детектор, классификатор и промпты |
| Слепой [eval/blind](eval/blind/README.md) | Написан другим автором; детектор под него не настраивается. Финальный замер — 26.10, один прогон |
| Второй слепой [eval/blind2](eval/blind2/README.md) | Написан 11.11 без доступа к коду детектора, словарям и обучающим данным; документы длиннее, 34 из 39 сведений коммерческой тайны — без «сигнальных» слов. Замер — 12.11, один прогон |

Полнота (recall) — доля найденных размеченных сущностей. Режим по умолчанию — без
генеративной нейросети (`--llm` не задан), с локальным классификатором `grif-cls`,
без API-ключа: так систему запустит жюри.

| Набор | Recall ПДн | Recall КТ |
|---|---|---|
| `eval/seed` | 99,3% | 100% |
| `eval/dev` | 95,2% | 89,1% |
| `eval/blind` (финальный замер 26.10, один прогон) | 92,8% | 91,7% |
| `eval/blind2` — blind-2 (12.11, один прогон) | 91,0% | 64,1% |
| blind-2 + облачная нейросеть (gpt-4o-mini, 12.11, один прогон) | 92,0% | 82,1% |
| Сканы (синтетические, dev) — `eval/scan-dev`, 15 документов, замер 11.11.2026 | 95,2% | 89,1% |
| Сканы слепого набора — `eval/scan-blind`, 12 документов, замер 12.11.2026, один прогон | 91,9% | 87,5% |

Данные сканов — из опубликованных метрик [scan-dev](eval/results/scan-dev/metrics.json)
и [scan-blind](eval/results/scan-blind/metrics.json).
Это документы `dev` и `blind`, превращённые в изображения 200 dpi с шумом и поворотом ±1,5°;
набор `scan-dev` использован для настройки и не является слепым, `scan-blind`
([описание](eval/scan-blind/README.md)) получен из слепого набора тем же генератором. Сущности, полностью потерянные
распознаванием, учитываются как пропуски. Уверенность Tesseract не равна полноте поиска
ПДн. Описание — [eval/scan-dev](eval/scan-dev/README.md); в интерфейсе откройте
«Качество» → «Сканы (распознавание)» → «Набор для настройки», API —
`GET /api/metrics?corpus=scan-dev`. При отсутствии метрик вкладка сообщает, что замер
ещё не выполнен, а API возвращает 404.

Сравнение полноты только на слепом наборе `eval/blind`:

| Режим | ПДн | КТ |
|---|---|---|
| Только словари и шаблоны (`--no-model`) | 90,1% | 12,5% |
| + классификатор grif-cls (по умолчанию, без ключа) | 92,8% | 91,7% |
| + облачная нейросеть (`--llm`, gpt-4o-mini через OpenAI-совместимый API) | 93,7% | 95,8% |

**Ограничения.** Точность коммерческой тайны на слепом наборе в режиме по умолчанию —
0,73: примерно каждая четвёртая находка лишняя. Полнота особых категорий ПДн — 56%
без генеративной нейросети, 67% с ней. Классификатор обучен на синтетике.

**blind-2 (12.11).** На втором слепом наборе полнота коммерческой тайны — 64,1% (25 из 39),
ниже цели 85%: набор почти целиком состоит из сведений без ключевых слов. Точность
коммерческой тайны — 0,69, особых категорий ПДн — полнота 65%, точность 0,54.
С облачной нейросетью (`--llm`, gpt-4o-mini, один прогон 12.11, те же детектор, пороги и
промпты) полнота коммерческой тайны на сведениях без ключевых слов выросла до 82,1%
(32 из 39) — всё ещё ниже цели 85%, а точность снизилась до 0,64.
На этом же наборе сравнили выпущенный классификатор (v2) с дообученным (v3), по одному
прогону каждого, по правилу, записанному до прогона. v3 нашёл больше коммерческой тайны
(30 из 39), но потерял два эталона особых категорий и дал больше лишних находок, поэтому
в релизе остался v2. Команда сравнения: `eval/ab_models.py`.

Как повторить замер: PowerShell из корня проекта, команды по одной, в том же окне
с `COMPOSE_PROJECT_NAME` из быстрого старта. Каталог `/eval/results` в Compose доступен
только для чтения, поэтому результаты записываем через отдельное подключение `/out`:

```powershell
docker-compose build api
```

```powershell
docker-compose run --rm --no-deps -v "${PWD}/eval:/eval" -v "${PWD}/eval/results:/out" api python /eval/run_eval.py --corpus /eval/seed --out /out
```

```powershell
docker-compose run --rm --no-deps -v "${PWD}/eval:/eval" -v "${PWD}/eval/results:/out" api python /eval/run_eval.py --corpus /eval/dev --out /out/dev
```

Сопоставление: тот же тип и пересечение не менее 50% длины эталонной сущности,
один к одному. ПДн включают обычные и особые категории; финансовые данные в эти
два показателя не входят. Подробности расчёта — в [eval/README.md](eval/README.md).

Откройте <http://localhost:3000/quality>: там полнота, точность и F1 по типам данных,
пороги полноты ПДн ≥ 90% и коммерческой тайны ≥ 85%. До расчёта страница показывает
**пример данных**, а `/api/metrics` отвечает 404. После расчёта API читает
`eval/results/metrics.json`; ошибки детекции — в `eval/results/errors.json`.
Результаты dev записываются отдельно в `eval/results/dev/` и не заменяют seed.
Контрольные результаты опубликованы в репозитории; новые прогоны сохраняйте отдельно.
Флаг `--strict` возвращает код 1 при недостижении порогов;
обычный запуск с кодом 0 означает успешный расчёт, а не достижение целевых значений.

## Нагрузка

Комплект из 20 синтетических файлов (демо 01–12 и повторно 01–08, включая PDF-скан и JPG),
режим без генеративной нейросети; распознавание, Natasha и grif-cls включены.
Замер 12.11.2026, один прогон на значение: AMD Ryzen 7 9700X, Docker доступны
16 логических процессоров и 15,2 ГиБ памяти.

| Процессов worker (`WORKER_CONCURRENCY`) | Время комплекта, с | Первый отчёт, с | Память worker, наблюдаемый пик, МиБ | Ошибки |
|---|---:|---:|---:|---:|
| 1 | 12,48 | 1,12 | 622,4 | 0 |
| **2 (по умолчанию)** | **9,90** | **1,63** | **888,3** | **0** |
| 4 | 9,87 | 1,63 | 1399,8 | 0 |

Во всех трёх прогонах готовы 20 отчётов из 20. Два процесса быстрее одного примерно на 21%;
четыре почти не ускоряют обработку, а пик памяти растёт примерно на 58%. Методика, полная
таблица и команда повтора (`eval/load_test.py`) —
[docs/local-llm.md](docs/local-llm.md#нагрузка-комплект-из-20-файлов-t-082).

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

## Стек

| Слой | Технологии |
|---|---|
| Интерфейс | React 18, TypeScript, Vite, Ant Design; nginx |
| API | Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2 |
| Очередь | Celery + Redis — вся проверка в фоне, поэтому большие файлы не дают 504 |
| Хранилище | PostgreSQL 16 (образ pgvector, расширение `vector` включается при запуске; векторный поиск по законам — в развитии) |
| Извлечение текста | PyMuPDF, python-docx, openpyxl, defusedxml (XML ЕИС); Tesseract и pytesseract для локального OCR |
| Поиск данных | регулярные выражения с контрольными суммами, Natasha (NER), словари, классификатор grif-cls (rubert-tiny2, ONNX Runtime, CPU) |
| Генеративная нейросеть (необязательно) | любой OpenAI-совместимый API или локальная Ollama (Gemma 4 / Qwen) |
| Запуск | Docker Compose |

## Структура

- `backend/` — FastAPI, Celery, сервисы конвейера (`app/services/*`), тесты.
- `frontend/` — React + TypeScript + Ant Design.
- `data/` — правила (`rules/rules.yaml`), тексты законов, демо-документы (только синтетика).
- `eval/` — генератор синтетического корпуса и подсчёт качества детекции.
- `ml/` — обучение классификатора grif-cls: генерация синтетики, обучение, экспорт в ONNX ([ml/README.md](ml/README.md)).
- `docs/` — **контракты** (`contracts.md`), архитектура, каталог правил.

Правила разработки команды — `AGENTS.md`; контракты данных и API — `docs/contracts.md`.

## Известные ограничения

### Чтение документов

1. OCR рассчитан на печатный текст. Рукописный текст, печати и подписи поверх текста
   не поддерживаются; при разрешении ниже 150 dpi надёжное распознавание не гарантируется.
   Ошибки распознавания снижают полноту поиска, особенно номеров с контрольными суммами:
   неверно прочитанная цифра может привести к пропуску. Найденные сомнительные слова
   подчёркнуты пунктиром; даже при высокой уверенности нужен просмотр исходника.
2. Старые форматы `.doc` и `.xls` не поддерживаются; PDF с паролем даёт понятную ошибку.
3. Файл — до 50 МБ, текстовый запрос — до 200 000 символов, комплект — до 20 файлов.
   Браузер и API отправляют файлы по одному; ZIP-архивы не принимаются.
   По умолчанию на документ — до 100 операций OCR (`OCR_MAX_PAGES`): полная страница
   или область изображения считается за одну операцию.
4. В XML атрибуты, комментарии и текст смешанного содержимого не проверяются.
   В атрибутах и смешанном тексте ПДн заменяются в копии, только если такое же значение
   найдено в полях; комментарии в копию не попадают.
   Текст одной XML-выгрузки — не больше 1 млн символов; больше — понятная ошибка
   с просьбой разделить выгрузку на несколько файлов.
5. На смешанной странице PDF с частичным текстовым слоем (часть текста — в слое, часть —
   на картинке) один и тот же фрагмент может быть прочитан дважды: из слоя и распознаванием.
   Тогда находки дублируются, а счётчики найденных данных завышаются.

### Качество поиска

6. На слепом наборе в режиме по умолчанию точность коммерческой тайны — 0,73
   (примерно каждая четвёртая находка лишняя), полнота особых категорий ПДн — 56%,
   финансовых сведений — 40%.
7. На втором слепом наборе blind-2 (замер 12.11.2026, один прогон) коммерческая тайна,
   записанная без ключевых слов, без генеративной нейросети находится в 64% случаев
   (25 из 39) при цели 85%; с облачной нейросетью — 82% (32 из 39), тоже ниже цели.
   Такие сведения нужно проверять человеку. Подробности — раздел [«Качество»](#качество).
8. Классификатор обучен на синтетике; на демо 07 и 08 он отмечает лишнюю коммерческую
   тайну. Подробнее — в [описании демо](data/demo/README.md#расхождения-t-046).
9. Правило «ПДн в документе для публикации» срабатывает и на ответственное должностное
   лицо заказчика в извещении. Решение, оставлять ли эти сведения, принимает человек.

### Безопасность

10. Маскирование зависит от полноты поиска: пропущенные ПДн попадут в текст для
   нейросети. Коммерческая тайна и финансовые сведения уходят в нейросеть открыто.
11. Обезличенная копия удаляет только найденные ПДн; коммерческую тайну нужно убрать вручную.
   Копии сканов реализованы: пиксели под найденными ПДн заменяются, скан-страницы PDF
   пересобираются без исходной картинки. Копия выдаётся после самопроверки;
   при ошибке или более 20 OCR-страницах она недоступна (`redacted_available=false`).
   Перед публикацией проверьте копию визуально: пропущенные данные могут сохраниться.
12. Входа и ролей нет: систему запускают локально или в закрытом контуре.
    Выставлять её в интернет без прокси с аутентификацией нельзя.

### Нейросеть (необязательный слой)

13. «Не удалось проверить» при сбое, таймауте или неточной цитате не значит
    «нарушения нет».
14. Локальная Gemma 4 12B хорошо ищет коммерческую тайну, но нестабильна на смысловых
    условиях правил: один ответ «да» из трёх на тестовом договоре.
15. Если пользователь создаст правило вида «ИЛИ (обычное условие, вопрос нейросети)»,
    источником будет показана нейросеть, даже когда сработала обычная ветка.

### Хранение

16. История проверок — только в этом браузере; отчёты и копии сервер удаляет через
    7 дней по умолчанию (`REPORT_TTL_DAYS`). Запись в истории остаётся, но отчёт уже не открыть.
17. Правила загружаются в базу при первом запуске; правки `rules.yaml` после этого
    применяются только через редактор правил или `docker-compose down -v`.
    Последняя команда удаляет тома проекта, включая базу и сохранённые отчёты.
18. Нагрузочный замер — один прогон на значение на одном компьютере, комплект из
    20 небольших файлов (раздел [«Нагрузка»](#нагрузка)); для других размеров документов
    и оборудования нужен отдельный замер. Документ на 70 страниц проверяется примерно
    за 6 с без генеративной нейросети.

## Дальнейшее развитие

- Вход через корпоративный SSO (Active Directory / Keycloak) и роли. Сейчас аккаунтов
  нет намеренно: система не хранит данных пользователей, история проверок — только
  в браузере.
- Общая история на сервере для команды юристов.
- Дообучение OCR на бланках закупок.
- Подключение регламентов заказчика как текстов правил.
- ZIP-архив комплекта с отчётами и обезличенными копиями.
- Серверная очередь комплекта с приоритетами (сейчас в Celery ставятся отдельные документы).
- Загрузка извещения по реестровому номеру из ЕИС (через официальный доступ к данным ЕИС в контуре заказчика).
- Дообучение классификатора на размеченных документах заказчика внутри его контура.

