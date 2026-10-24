"""Синтетические большие документы: python data/demo/make_large.py --out <папка>."""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "tests"))

from large_docs import build_docx, build_pdf, build_xlsx  # noqa: E402


def main() -> None:
    """Создаёт только три известных файла в указанном каталоге."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for filename, builder in (
        ("large_contract.docx", build_docx), ("large_application.pdf", build_pdf),
        ("large_estimate.xlsx", build_xlsx),
    ):
        builder(args.out / filename)
    logging.info("Создано 3 синтетических документа T-031")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
