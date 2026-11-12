"""A/B двух версий классификатора grif-cls на одном корпусе. Задача T-080b.

Тонкая обёртка: логика — в backend/app/evaluation/paired.py. Каждая модель прогоняется один
раз в режиме по умолчанию (regex, NER, словари + классификатор, без LLM) — как run_eval.py.

В Docker (PowerShell, из корня репозитория; рабочая папка модели не подменяется —
вторая модель монтируется отдельно):

    docker-compose run --rm --no-deps -v "${PWD}/eval:/eval" -v "${PWD}/ml/out/v3:/models/v3:ro" `
        -v "${PWD}/eval/results:/out" api python /eval/ab_models.py --corpus /eval/blind2 `
        --a v2=/data/models/grif-cls --b v3=/models/v3 --out /out/blind2-ab

Результаты в --out:
- <имя>/metrics.json, <имя>/errors.json — то же, что пишет run_eval.py для этой модели;
- ab.json, ab.md — метрики рядом, попарная таблица по эталонам, тест Макнемара, решение
  по правилу из paired.py. Только числа: ни текстов, ни значений сущностей.

Код выхода: 0 — посчитано; 2 — ошибка корпуса, модель не загрузилась или обе папки дают
одну и ту же модель.
"""

import argparse
import importlib.util
import json
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


def named_dir(value: str) -> tuple[str, Path]:
    """Аргумент «имя=папка» → (имя, Path)."""
    name, sep, path = value.partition("=")
    if not sep or not name or not path:
        raise argparse.ArgumentTypeError("нужно имя=папка, например v3=/models/v3")
    return name, Path(path)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A/B двух моделей grif-cls на одном корпусе")
    parser.add_argument("--corpus", type=Path, required=True, help="папка корпуса")
    parser.add_argument("--a", type=named_dir, required=True, help="текущая модель: имя=папка")
    parser.add_argument("--b", type=named_dir, required=True, help="новая модель: имя=папка")
    parser.add_argument("--out", type=Path, required=True, help="папка результатов")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if hasattr(sys.stderr, "reconfigure"):  # консоль Windows (cp1251) и символы ✅/❌
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("app", "pymorphy2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    ensure_app_importable()
    from app.evaluation.corpus import load_corpus
    from app.evaluation.paired import build_report, format_report, run_model
    from app.evaluation.report import build_metrics, write_results

    (name_a, dir_a), (name_b, dir_b) = args.a, args.b
    if name_a == name_b:
        logger.error("Имена моделей должны различаться")
        return 2
    try:
        docs = load_corpus(args.corpus)
        runs = [run_model(docs, name, path) for name, path in ((name_a, dir_a), (name_b, dir_b))]
    except ValueError as exc:
        logger.error("Ошибка: %s", exc)
        return 2
    if runs[0].version == runs[1].version:
        logger.error("Обе папки дают одну модель (%s): сравнивать нечего", runs[0].version)
        return 2
    corpus = args.corpus.resolve().name
    for run in runs:
        write_results(build_metrics(run.result, corpus=corpus), run.result.errors,
                      args.out / run.name)
    report = build_report(runs[0], runs[1], corpus)
    text = format_report(report)
    (args.out / "ab.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (args.out / "ab.md").write_text("```\n" + text + "\n```\n", encoding="utf-8")
    logger.info("%s", text)
    logger.info("Результаты: %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
