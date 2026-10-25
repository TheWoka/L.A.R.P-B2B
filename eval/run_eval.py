"""Оценка качества детекции на эталонном корпусе. Задача T-014a.

Тонкая обёртка командной строки: вся логика — в backend/app/evaluation.

Локально:   cd backend && python ../eval/run_eval.py --corpus ../eval/seed --out ../eval/results
В Docker:   docker-compose run --rm --no-deps -v "${PWD}/eval:/eval" api \
                python /eval/run_eval.py --corpus /eval/seed --out /eval/results

Код выхода: 0 — успех; 1 — с --strict не выполнен критерий (recall ПДн < 0.90 или
коммерческой тайны < 0.85); 2 — ошибка в корпусе (разметка, файлы) или --llm без ключа.

По умолчанию (T-036b) — как в конвейере без ключа: detect → mask → классификатор grif-cls
(data/models/grif-cls, CPU, без сети). Страница «Качество» показывает реальную систему.
--no-model — только regex, NER и словари (для сравнения «до/после»); в Metrics.corpus
добавляется « (без классификатора)».

--llm (T-013d, T-013e): после классификатора — поиск особых категорий и коммерческой тайны
нейросетью, как в конвейере. Нужен LLM_PROVIDER=openai_compatible и ключ в .env; вызовы платные.
"""

import argparse
import importlib.util
import logging
import sys
from pathlib import Path

logger = logging.getLogger("eval")


def ensure_app_importable() -> None:
    """Добавляет в sys.path папку с пакетом app: backend/ рядом с eval/ или текущую (/app)."""
    if importlib.util.find_spec("app") is not None:
        return
    for candidate in (Path(__file__).resolve().parent.parent / "backend", Path.cwd()):
        if (candidate / "app" / "__init__.py").is_file():
            sys.path.insert(0, str(candidate))
            return
    raise SystemExit("Не найден пакет app: запускайте из backend/ или из контейнера api")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Precision / recall детекции на корпусе")
    parser.add_argument("--corpus", type=Path, required=True, help="папка корпуса")
    parser.add_argument("--out", type=Path, required=True, help="папка для metrics.json")
    parser.add_argument(
        "--strict", action="store_true", help="код выхода 1, если критерий не выполнен",
    )
    parser.add_argument(
        "--llm", action="store_true",
        help="добавить поиск коммерческой тайны нейросетью (нужен ключ в .env, вызовы платные)",
    )
    parser.add_argument(
        "--no-model", action="store_true",
        help="без классификатора grif-cls: только regex, NER и словари (для сравнения)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if hasattr(sys.stderr, "reconfigure"):  # консоль Windows (cp1251) и символы ✅/❌
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("app", "pymorphy2"):  # без построчных логов детектора и словарей Natasha
        logging.getLogger(noisy).setLevel(logging.WARNING)
    ensure_app_importable()
    from app.evaluation.corpus import load_corpus
    from app.evaluation.report import (
        LLM_CORPUS_SUFFIX,
        NO_MODEL_CORPUS_SUFFIX,
        LlmDetector,
        build_metrics,
        criteria_met,
        detect_with_model,
        evaluate_corpus,
        format_table,
        write_results,
    )
    from app.services.detection.detect import detect
    from app.services.llm.client import is_llm_enabled

    if args.llm and not is_llm_enabled():
        logger.error(
            "--llm: нейросеть не настроена (LLM_PROVIDER=mock или пустые LLM_BASE_URL/LLM_MODEL). "
            "Пропишите ключ в .env или запустите без --llm"
        )
        return 2
    try:
        docs = load_corpus(args.corpus)
    except ValueError as exc:
        logger.error("Ошибка корпуса: %s", exc)
        return 2
    llm_detector = LlmDetector(use_model=not args.no_model) if args.llm else None
    plain = detect if args.no_model else detect_with_model
    result = evaluate_corpus(docs, llm_detector or plain)
    corpus = (
        args.corpus.resolve().name
        + (LLM_CORPUS_SUFFIX if args.llm else "")
        + (NO_MODEL_CORPUS_SUFFIX if args.no_model else "")
    )
    metrics = build_metrics(result, corpus=corpus)
    write_results(metrics, result.errors, args.out)
    logger.info("%s", format_table(metrics))
    logger.info("Ошибок: %d. Результаты: %s", len(result.errors), args.out)
    if llm_detector is not None:
        logger.info(
            "Запросов к LLM: %d на %d документов (попадания в кэш не считаются)",
            llm_detector.calls, llm_detector.documents,
        )
    return 1 if args.strict and not criteria_met(metrics) else 0


if __name__ == "__main__":
    raise SystemExit(main())
