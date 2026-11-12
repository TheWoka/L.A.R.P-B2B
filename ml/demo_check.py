"""Демо-документы с другой папкой модели: ушла ли лишняя коммерческая тайна. Задача T-080.

То же, что test_demo_regression (backend/tests/test_model_detector.py), но модель берётся из
--model-dir (например ml/out/v3), а data/models/grif-cls не трогается. Для каждого файла:
overall и набор нарушений против data/demo/answers.yaml и число находок модели по типам.
В вывод попадают только счётчики и id правил — без текста документов.

Запуск (из корня репозитория, venv с backend/requirements.txt):
    python ml/demo_check.py --model-dir data/models/grif-cls      # выпущенная модель
    python ml/demo_check.py --model-dir ml/out/v3                 # кандидат v3
"""

import argparse
import logging
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    OUT_DIR,
    REPO_ROOT,
    ensure_app_importable,
    setup_logging,
    write_json,
)

logger = logging.getLogger("demo_check")

DEMO_DIR = REPO_ROOT / "data" / "demo"
DEFAULT_FILES = ("07_international_dpa.docx", "08_kii_publication.docx", "09_eis_notice_44fz.xml")


def check_file(item: dict) -> dict:
    """Один демо-файл в режиме mock: совпал ли отчёт с ключом и что нашла модель."""
    from app.schemas import DocPurpose
    from app.services.extraction.extract import extract
    from app.services.pipeline import run_audit

    path = DEMO_DIR / item["file"]
    doc = extract(path, path.name, "synthetic-demo")
    report = run_audit(doc, DocPurpose(item["purpose"]), path.read_bytes()).report
    got, want = sorted(v.rule_id for v in report.violations), sorted(item["violations"])
    overall = getattr(report.overall, "value", report.overall)
    by_model = Counter(getattr(e.type, "value", e.type) for e in report.entities
                       if e.detector == "model")
    return {
        "file": item["file"], "overall": overall, "overall_expected": item["overall"],
        "violations": got, "violations_expected": want,
        "extra_violations": sorted(set(got) - set(want)),
        "missing_violations": sorted(set(want) - set(got)),
        "model_entities": dict(by_model),
        "passed": overall == item["overall"] and got == want,
    }


def run(model_dir: Path, files: tuple[str, ...]) -> list[dict]:
    """Подменяет папку модели в конвейере и проверяет выбранные демо-файлы."""
    os.environ["LLM_PROVIDER"] = "mock"
    os.environ["LLM_DEFAULT_MODE"] = "mock"
    ensure_app_importable()
    import yaml
    from app.services.detection import model_detector as md

    if not (model_dir / md.MODEL_FILE).is_file():
        raise SystemExit(f"В {model_dir} нет {md.MODEL_FILE}")
    md.MODEL_DIRS = (model_dir.resolve(),)
    md.load_model.cache_clear()
    answers = yaml.safe_load((DEMO_DIR / "answers.yaml").read_text(encoding="utf-8"))
    return [check_file(item) for item in answers if item["file"] in files]


def markdown(rows: list[dict]) -> str:
    lines = ["| Файл | Итог | Лишние нарушения | Не найдены | Находки модели |",
             "|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['file']} | {'✅' if r['passed'] else '❌'} "
            f"| {', '.join(r['extra_violations']) or '—'} "
            f"| {', '.join(r['missing_violations']) or '—'} "
            f"| {', '.join(f'{k}: {v}' for k, v in sorted(r['model_entities'].items())) or '—'} |"
        )
    return "\n".join(lines)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Демо 07–09 с выбранной моделью (T-080)")
    p.add_argument("--model-dir", type=Path, required=True)
    p.add_argument("--files", nargs="+", default=list(DEFAULT_FILES))
    p.add_argument("--out", type=Path, default=None,
                   help="куда записать JSON (по умолчанию ml/out/demo_check_<папка модели>.json)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging()
    rows = run(args.model_dir, tuple(args.files))
    out = args.out or OUT_DIR / f"demo_check_{args.model_dir.resolve().name}.json"
    write_json(out, {"model_dir": str(args.model_dir), "rows": rows})
    logger.info("\n%s\n\nИтог: %s", markdown(rows), out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
