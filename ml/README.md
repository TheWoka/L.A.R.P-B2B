# ml/ — классификатор коммерческой тайны и особых категорий (T-036a)

Офлайн-детектор «по смыслу» для режима без ключа: жюри запускает систему без нейросети, а словари
находят мало КТ и особых категорий в чужих документах. Решение — **дистилляция**: gpt-4o-mini
пишет и размечает синтетические фрагменты, маленький энкодер `cointegrated/rubert-tiny2`
(29 млн параметров, MIT) учится их классифицировать и работает в контейнере на CPU через ONNX (≈ 31 МБ: int8-эмбеддинги, fp16-веса).

Классы фрагмента: `NONE`, `TRADE_SECRET`, `SPECIAL_CATEGORY`. ПДн, финансовые показатели и реквизиты
ищут regex, NER и словари — это не задача модели.

| Файл | Что делает |
|---|---|
| `fragments.py` | `split_fragments(text) -> list[(start, end)]` — нарезка на предложения (только `re`; пойдёт в конвейер T-036b) |
| `prompts/generate.md` | промпт генерации: классы своими словами + «трудные отрицания» |
| `generate_data.py` | генерация `data/train/fragments.jsonl`, `stats.json`, `sample_for_review.md` |
| `train.py` | обучение → `ml/out/best/` (лучшая эпоха по macro-F1 на 10% валидации) |
| `evaluate.py` | честная оценка dev/seed: пороги на dev, таблица «словари → словари + модель», скорость |
| `export_onnx.py`, `quantize.py` | ONNX + сжатие → `data/models/grif-cls/` (model.onnx, tokenizer, labels, thresholds, card.md) |
| `predictors.py`, `common.py` | предсказатели PyTorch/ONNX, пути и метки |
| `tests/` | `python -m pytest ml/tests -q` |

## Честность метрики

- `eval/blind/*` и `eval/results/blind*` не открываются: `evaluate.py` отказывается работать с путём, где есть `blind`.
- `eval/dev/*` — только выбор порогов и решение «идём дальше»; генератору и обучению не показывается.
- `eval/seed/*` — второй контроль, в обучение не идёт.
- В промпте генерации нет ни одной строки из `eval/*` (это проверяет тест `test_prompt_contains_no_eval_lines`).

## Воспроизведение (Windows, PowerShell, из корня репозитория)

**1. Генерация** (нужен ключ в `.env`, ≈ 330 вызовов, оценка ≈ 0,3 $ на gpt-4o-mini; лимиты: 400 вызовов и 2 $):

```powershell
docker-compose up -d redis
docker-compose run --rm -e LLM_TIMEOUT_S=90 -v "${PWD}/ml:/ml" -v "${PWD}/data/train:/train" api python /ml/generate_data.py --out /train --dry-run
docker-compose run --rm -e LLM_TIMEOUT_S=90 -v "${PWD}/ml:/ml" -v "${PWD}/data/train:/train" api python /ml/generate_data.py --out /train
```

Прерванный запуск продолжается с места остановки (`ml/out/gen_raw.jsonl`), повтор идёт из кэша Redis.
ФИО маскируются тем же `detect → mask`, что в конвейере; особые категории не маскируются.

**1б. Добор v2** (после первой оценки на dev: люди в NONE, плейсхолдеры реквизитов, длинные фразы,
особые категории без плейсхолдера; генератор посильнее — gpt-4.1, ≈ 350 вызовов, ≈ 3–5 $):

```powershell
docker-compose run --rm -e LLM_MODEL=gpt-4.1 -e LLM_TIMEOUT_S=120 -v "${PWD}/ml:/ml" -v "${PWD}/data/train:/train" api python /ml/generate_data.py --plan v2 --out /train/v2 --price-in 2 --price-out 8 --max-usd 8 --dry-run
```

Без `--dry-run` — генерация. `train.py` по умолчанию учится на v1 + v2 (дубли и конфликты меток убираются).

**2. Окружение для обучения** (отдельный venv, в образ api не попадает):

```powershell
python -m venv .venv-ml
.venv-ml\Scripts\activate
pip install -r backend/requirements.txt -r ml/requirements-ml.txt
```

**3. Обучение** (CPU ≈ 5–15 мин на 6 000 фрагментов; первая загрузка скачает rubert-tiny2 ≈ 120 МБ):

```powershell
python ml/train.py
```

**4. Оценка и выбор порогов на dev:**

```powershell
python ml/evaluate.py
```

**5. Экспорт в ONNX и повторная оценка сжатой модели:**

```powershell
python ml/export_onnx.py
python ml/evaluate.py --onnx --thresholds ml/out/thresholds.json
python ml/export_onnx.py --card-only
```

**6. Проверки:**

```powershell
ruff check --config backend/pyproject.toml ml/
python -m pytest ml/tests -q
docker-compose run --rm --no-deps api pytest -q
```

## Результаты

- `data/train/` — синтетика (коммитится): `fragments.jsonl` (`text`, `label`, `meta`), `stats.json`, `sample_for_review.md`.
- `ml/out/` — веса, `train_metrics.json`, `thresholds.json`, `eval/summary.{json,md}`, `onnx_check.json` (не коммитится).
- `data/models/grif-cls/` — модель для конвейера (T-036b) и `card.md` с метриками и ограничениями.
