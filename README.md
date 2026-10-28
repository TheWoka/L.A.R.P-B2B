# Гриф — ИИ-аудитор безопасности документов

Проверяет документы закупок на персональные данные (152-ФЗ и GDPR), сведения о критической
информационной инфраструктуре — КИИ (187-ФЗ) и коммерческую тайну (98-ФЗ).
Проверяет согласования, грифы конфиденциальности и сроки хранения; выдаёт отчёт со светофором,
нарушениями, цитатами, статьями законов и рекомендациями.
Перед обращением к нейросети персональные данные заменяются плейсхолдерами.

[Как читать отчёт «Гриф»](docs/user-guide.md) — короткая памятка для юриста и специалиста по закупкам.

[Архитектура и путь документа](docs/architecture.md) — схемы, модули и границы безопасности.

## Быстрый старт

Нужен запущенный Docker Desktop с Linux-контейнерами, свободные порты 3000 и 8000
и доступ в интернет для первой сборки. Команды ниже — из корня репозитория в PowerShell.

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
По умолчанию `LLM_PROVIDER=mock`: API-ключ не нужен. Первая загрузка зависимостей
зависит от скорости интернета; следующие запуски используют кэш Docker.
Если сборка завершилась `ReadTimeout` при скачивании пакетов, проверьте доступ к PyPI
и повторите сборку. Переходите к проверке API после успешного завершения запуска.

## Как пользоваться

1. Откройте <http://localhost:3000>.
2. Перетащите PDF, DOCX или XLSX (до 50 МБ) в область загрузки либо выберите
   «Проверить текст» и вставьте текст документа.
3. Выберите назначение: «Внутренний документ», «Для публикации (ЕИС, сайт)»,
   «Договор с контрагентом» или «Международный контракт». Нажмите «Проверить документ»
   или «Проверить текст».
4. Дождитесь окончания проверки на странице с ходом обработки. Документ обрабатывается
   в очереди; после завершения откроется отчёт.
5. Прочитайте светофор: красный — серьёзное нарушение, жёлтый — замечания,
   зелёный — нарушения не найдены включёнными проверками. Изучите три пункта чек-листа,
   нарушения, рекомендации и цитаты; нажмите на ссылку статьи закона, чтобы открыть её текст.
   Кнопка «Скачать обезличенную копию» доступна, если копия создана; рядом можно скачать
   отчёт JSON. Из копии удаляются только ПДн — коммерческую тайну нужно убрать отдельно.

## Примеры API

Команды проверены 24.10.2026 в PowerShell 7.6, `LLM_PROVIDER=mock`. Выполняйте из корня
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
`.pdf`, `.docx`, `.xlsx`, до 50 МБ; неподдерживаемый файл — HTTP 400.

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

## Режим без нейросети и с нейросетью

Во всех режимах работают извлечение текста, регулярные выражения с проверкой контрольных
сумм, Natasha NER, словари, маскирование, обычные правила, три категории чек-листа,
отчёт и создание обезличенной копии. Локальный классификатор `grif-cls` ищет КТ и особые
категории ПДн на CPU без ключа и сети; если файлов модели нет, этот шаг пропускается
с предупреждением. Ниже «нейросеть» означает генеративную модель (LLM).

| Режим | Что работает и что добавляется | Что уходит наружу при проверке |
|---|---|---|
| **Без нейросети — по умолчанию**, `LLM_PROVIDER=mock` | Базовая проверка выше, работает без ключа у всех. Поиск через LLM и условия правил `llm` не выполняются | Текст документа не отправляется внешней модели |
| **Облачный OpenAI-совместимый API**, ключ в локальном `.env` | Базовая проверка + поиск особых категорий ПДн, повторная маска, поиск коммерческой тайны и смысловые условия правил `llm` | Провайдер получает только текст с замаскированными найденными ПДн; КТ и финансовые сведения в нём сохраняются |
| **Локальная модель через Ollama**, профиль Compose `local-llm` | Те же дополнительные проверки LLM; качество и скорость зависят от модели. Профили CPU и GPU — в [выборе модели](docs/local-llm.md#какую-модель-выбрать-t-029b) | Маскированный текст передаётся Ollama внутри сети Compose, облачному провайдеру не отправляется; для первичной загрузки образов и весов нужен интернет |

Для Ollama на CPU задайте `LLM_TIMEOUT_S=120` в `.env`. Профиль CPU использует
Qwen2.5-7B Q4_K_M, GPU — T-lite-it-2.1 Q4_K_M; требования, настройка NVIDIA и
известные зависания GPU-модели описаны в [docs/local-llm.md](docs/local-llm.md).
Профиль Compose запускает Ollama, а выбор CPU/GPU задаётся по этой инструкции.

Для OpenAI-совместимого API заполните **локальный** `.env` переменными из
[.env.example](.env.example):

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://<адрес-провайдера>/v1
LLM_API_KEY=<ваш-ключ>
LLM_MODEL=<имя-модели-у-провайдера>
LLM_MAX_CALLS_PER_DOC=30
LLM_TIMEOUT_S=30
```

После изменения настроек выполните `docker-compose up -d --force-recreate api worker`.
В нейросеть передаётся только текст с замаскированными ПДн (`[PERSON_1]`, `[PHONE_1]`);
его можно проверить в разделе отчёта «Что видела нейросеть» и через `/llm-log`.
Ключи не добавляйте в репозиторий. В mock `llm_used=false`; при включённой LLM это поле
отражает реальные вызовы, поэтому ответы только из кэша также дают `false`.
Таймаут или ответ без подтверждённой цитаты даёт `unknown`, а не доказательство
отсутствия нарушения; обычные проверки продолжают работать.

## Демо

В [data/demo](data/demo/README.md) лежат шесть документов с выдуманными данными.
Выбирайте назначение из таблицы: от него зависит результат. Ожидания для режима mock:

| Файл | Назначение | Ожидаемый результат |
|---|---|---|
| `01_tz_publication.docx` | `publication` | **red**: ПДн и коммерческая тайна в публикации; 2 нарушения |
| `02_contract_clean.docx` | `contract` | **green**: 0 нарушений; ФИО и паспорт при необходимых условиях обработки |
| `03_application_pd.pdf` | `contract` | **red**: нет поручения обработки, согласия, согласования и срока хранения; 4 нарушения |
| `04_estimate.xlsx` | `internal` | **red**: 2 фрагмента коммерческой тайны, нет грифа и согласования; 2 нарушения |
| `05_anketa_special.docx` | `internal` | **red**: особые категории ПДн без согласия, нет согласования, грифа и срока хранения; 4 нарушения |
| `06_medical_tz.docx` | `publication` | **green**: общие контакты и медицинские требования к товару; 0 сущностей, 0 нарушений |

## Качество

Качество проверяем на трёх наборах синтетических документов с разными ролями:

| Набор | Как используется |
|---|---|
| Эталонный `eval/seed` | Составлен вместе с детектором: проверяем, что изменения ничего не сломали. Высокий результат здесь не доказывает качество на новых документах |
| Для настройки [eval/dev](eval/dev/README.md) | Написан другим автором независимо от seed и реализации детектора; на нём разбираем ошибки и настраиваем детектор, классификатор и промпты |
| Слепой [eval/blind](eval/blind/README.md) | Написан другим автором; детектор под него не настраивается. Считается один раз перед сдачей, результаты — [финальный замер 27.10] |

Полнота (recall) — доля найденных размеченных сущностей. Замер без вызовов LLM
(`--llm` не задан), с локальным классификатором по умолчанию, 25.10.2026:

| Набор | Recall ПДн | Recall коммерческой тайны |
|---|---|---|
| `eval/seed` | 99,25% | 100,00% |
| `eval/dev` | 95,24% | 89,09% |
| `eval/blind` | [финальный замер 27.10] | [финальный замер 27.10] |

На чужих документах полнота ниже из-за новых формулировок: расширяем смысловое
распознавание локальным классификатором и нейросетью, настраивая их на dev.

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
Результаты не коммитятся. Флаг `--strict` возвращает код 1 при недостижении порогов;
обычный запуск с кодом 0 означает успешный расчёт, а не достижение целевых значений.

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

