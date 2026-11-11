"""Корпус «сканов» из текстового корпуса с разметкой. Задача T-077b.

Для каждого `name.txt` с разметкой `[[TYPE:значение]]`:
  чистый текст → страницы А4 «как со сканера» → `name.pdf` (только картинки) →
  extract() (OCR, T-071) → эталон переносится в координаты OCR-текста → `name.json`.
Разметка не подгоняется под детектор: перенос зависит только от чистого текста и OCR.
Сущности, потерянные OCR целиком, остаются в эталоне (`"lost": true`) и считаются пропуском.

Локально:  cd backend
           python ../eval/make_scan_corpus.py --source ../eval/dev --out ../eval/scan-dev
В Docker:  docker-compose run --rm --no-deps -v "${PWD}/eval:/eval" api \
               python /eval/make_scan_corpus.py --source /eval/dev --out /eval/scan-dev

Код выхода: 0 — успех; 2 — ошибка источника или OCR недоступен.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("make_scan_corpus")

PURPOSE = "scan"
README = "README.md"


def ensure_app_importable() -> None:
    """Добавляет в sys.path папку с пакетом app: backend/ рядом с eval/ или текущую (/app)."""
    if importlib.util.find_spec("app") is not None:
        return
    for candidate in (Path(__file__).resolve().parent.parent / "backend", Path.cwd()):
        if (candidate / "app" / "__init__.py").is_file():
            sys.path.insert(0, str(candidate))
            return
    raise SystemExit("Не найден пакет app: запускайте из backend/ или из контейнера api")


ensure_app_importable()

from app.evaluation.markup import GoldEntity, parse_markup  # noqa: E402
from app.evaluation.scan import (  # noqa: E402
    DEFAULT_FONT,
    ocr_similarity,
    render_scan_pages,
    scan_pdf_bytes,
    transfer_gold,
)

TextExtractor = Callable[[Path], str]


def extract_text(path: Path) -> str:
    """Тот же extract(), что использует load_corpus для `.pdf`: координаты совпадут."""
    from app.services.extraction.extract import extract

    return extract(path, path.name, path.stem).text


@dataclass
class DocStats:
    """Итог по одному документу для README корпуса."""

    name: str
    pages: int
    similarity: float
    total: Counter[str]
    lost: Counter[str]


def build_document(
    source: Path, out_dir: Path, *, seed: int, dpi: int, font: Path, extractor: TextExtractor,
) -> DocStats:
    """Один документ: PDF-скан, OCR, перенос эталона, `name.json`."""
    clean, gold = parse_markup(source.read_text(encoding="utf-8"), source.name)
    pages = render_scan_pages(clean, seed=f"{seed}:{source.stem}", dpi=dpi, font_path=font)
    pdf_path = out_dir / f"{source.stem}.pdf"
    pdf_path.write_bytes(scan_pdf_bytes(pages))
    ocr_text = extractor(pdf_path)
    scan_gold = transfer_gold(gold, clean, ocr_text)
    write_json(out_dir / f"{source.stem}.json", scan_gold, source.name)
    return DocStats(
        name=source.stem, pages=len(pages), similarity=ocr_similarity(clean, ocr_text),
        total=Counter(g.type.value for g in gold),
        lost=Counter(g.type.value for g in scan_gold if g.lost),
    )


def write_json(path: Path, gold: list[GoldEntity], source_name: str) -> None:
    """Формат 2 корпуса; значения сущностей не пишутся — только тип и координаты."""
    entities = []
    for g in gold:
        item: dict[str, object] = {
            "type": g.type.value, "category": g.category.value, "start": g.start, "end": g.end,
        }
        if g.lost:
            item["lost"] = True
        entities.append(item)
    data = {"purpose": PURPOSE, "source": source_name, "entities": entities}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def render_readme(stats: list[DocStats], *, source: Path, seed: int, dpi: int) -> str:
    """Отчёт генератора: потери OCR по типам и похожесть текста по документам."""
    total: Counter[str] = Counter()
    lost: Counter[str] = Counter()
    for s in stats:
        total.update(s.total)
        lost.update(s.lost)
    lines = [
        f"# Корпус сканов `{source.name}` → PDF (T-077b)",
        "",
        "Сгенерировано `eval/make_scan_corpus.py`, не редактировать вручную.",
        f"Источник: `eval/{source.name}`, {len(stats)} документов; dpi={dpi}, seed={seed}.",
        "Каждый PDF — только картинки: DejaVuSans 11 pt, поля 2 см, поворот ±1,5°, шум sigma 6,",
        "JPEG 85.",
        "Эталон перенесён в координаты текста OCR посимвольным выравниванием",
        "(см. `eval/README.md`, раздел «Сканы»).",
        "",
        "## Сущности, потерянные OCR целиком",
        "",
        "Они остаются в эталоне (`\"lost\": true`, нулевая длина) и считаются пропуском.",
        "",
        "| Тип | В эталоне | Потеряно OCR |",
        "|---|---:|---:|",
    ]
    for entity_type in sorted(total):
        lines.append(f"| {entity_type} | {total[entity_type]} | {lost[entity_type]} |")
    lines += [
        f"| **Всего** | **{sum(total.values())}** | **{sum(lost.values())}** |",
        "",
        "## Документы",
        "",
        "| Документ | Страниц | Похожесть OCR | Потеряно |",
        "|---|---:|---:|---:|",
    ]
    for s in stats:
        lines.append(
            f"| {s.name} | {s.pages} | {s.similarity:.3f} | {sum(s.lost.values())} |",
        )
    lines += [
        "",
        "Похожесть — `difflib.SequenceMatcher.ratio()` чистого текста и текста OCR",
        "(1.0 — совпали посимвольно).",
        "",
    ]
    return "\n".join(lines)


def make_corpus(
    source: Path, out: Path, *, seed: int = 7, dpi: int = 200, font: Path = DEFAULT_FONT,
    extractor: TextExtractor = extract_text,
) -> list[DocStats]:
    """Строит корпус сканов: `out/name.pdf` + `out/name.json` + `out/README.md`.

    Берутся только `.txt` с разметкой в тексте (формат 1) без пары `.json`.
    """
    sources = sorted(
        p for p in source.iterdir()
        if p.is_file() and p.suffix == ".txt" and not p.with_suffix(".json").is_file()
    )
    if not sources:
        raise ValueError(f"в папке {source} нет .txt с разметкой")
    out.mkdir(parents=True, exist_ok=True)
    stats = [
        build_document(p, out, seed=seed, dpi=dpi, font=font, extractor=extractor)
        for p in sources
    ]
    readme = render_readme(stats, source=source, seed=seed, dpi=dpi)
    (out / README).write_text(readme, encoding="utf-8")
    return stats


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Корпус сканов из корпуса с разметкой (T-077b)")
    parser.add_argument("--source", type=Path, required=True, help="папка .txt с [[TYPE:...]]")
    parser.add_argument("--out", type=Path, required=True, help="папка корпуса сканов")
    parser.add_argument("--dpi", type=int, default=200, help="разрешение «скана» (200)")
    parser.add_argument("--seed", type=int, default=7, help="seed поворота и шума (7)")
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT, help="путь к DejaVuSans.ttf")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if hasattr(sys.stderr, "reconfigure"):  # консоль Windows (cp1251)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("app").setLevel(logging.WARNING)
    from app.services.extraction.errors import ExtractionError

    try:
        stats = make_corpus(args.source, args.out, seed=args.seed, dpi=args.dpi, font=args.font)
    except ExtractionError as exc:
        logger.error(
            "OCR недоступен (%s): нужен extract() с OCR (T-071) и tesseract-ocr-rus в образе",
            exc.case,
        )
        return 2
    except (ValueError, FileNotFoundError) as exc:
        logger.error("Ошибка: %s", exc)
        return 2
    lost = sum(sum(s.lost.values()) for s in stats)
    total = sum(sum(s.total.values()) for s in stats)
    logger.info("Документов: %d, сущностей: %d, потеряно OCR: %d. Папка: %s",
                len(stats), total, lost, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
