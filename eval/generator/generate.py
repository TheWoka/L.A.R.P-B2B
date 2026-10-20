"""CLI генератора T-011b. Вся логика документов находится в backend/app/evaluation/generator."""

import argparse
import importlib.util
import json
import logging
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from random import Random

logger = logging.getLogger("generator")


def ensure_app_importable() -> None:
    """Как run_eval.py: локальный backend/ или текущая папка контейнера /app."""
    if importlib.util.find_spec("app") is not None:
        return
    for candidate in (Path(__file__).resolve().parents[2] / "backend", Path.cwd()):
        if (candidate / "app" / "__init__.py").is_file():
            sys.path.insert(0, str(candidate))
            return
    raise SystemExit("Не найден пакет app: запускайте из корня, backend/ или контейнера api")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Генератор синтетического корпуса закупок")
    parser.add_argument("--out", type=Path, default=Path("eval/corpus"))
    parser.add_argument("--count", type=int, default=210)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-pdf", action="store_true", help="вместо PDF сохранять DOCX")
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("--count должен быть положительным")
    return args


def generate(args: argparse.Namespace) -> None:
    from app.evaluation.generator import TEMPLATES, make_document

    rng = Random(args.seed)
    formats: Counter[str] = Counter()
    templates: Counter[str] = Counter()
    entities: Counter[str] = Counter()
    # Не смешиваем результаты разных запусков: чужие файлы никогда не удаляем.
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError("Папка результата должна быть пустой: выберите другой --out")
    for index in range(args.count):
        template = TEMPLATES[index % len(TEMPLATES)]
        if template.name == "offer" and not args.no_pdf and (index // len(TEMPLATES)) % 2:
            template = replace(template, format="pdf")
        path = make_document(template, rng, args.out, f"{index + 1:04d}_{template.name}")
        payload = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        entities.update(item["type"] for item in payload["entities"])
        formats[path.suffix[1:]] += 1
        templates[template.name] += 1
    logger.info("Документов по форматам: %s", dict(sorted(formats.items())))
    logger.info("Документов по шаблонам: %s", dict(sorted(templates.items())))
    logger.info("Сущностей по типам: %s", dict(sorted(entities.items())))
    logger.info("Всего сущностей: %d; TRADE_SECRET: %d", entities.total(), entities["TRADE_SECRET"])
    logger.info("PDF: %s", "выключен (--no-pdf)" if args.no_pdf else "включён")
    if args.count >= 210 and (entities.total() < 1500 or entities["TRADE_SECRET"] < 150):
        raise ValueError("Недостаточный объём разметки: нужно ≥1500 сущностей и ≥150 TRADE_SECRET")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("app").setLevel(logging.WARNING)
    ensure_app_importable()
    try:
        generate(args)
    except ValueError as exc:
        logger.error("Ошибка генерации: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
