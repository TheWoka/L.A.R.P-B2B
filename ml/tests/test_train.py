"""Тесты вспомогательных функций обучения (T-036a): объединение наборов и параметры в JSON."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import train as tr  # noqa: E402


def _write(path: Path, rows: list[tuple[str, str]]) -> Path:
    path.write_text("\n".join(json.dumps({"text": t, "label": lab}, ensure_ascii=False)
                              for t, lab in rows), encoding="utf-8")
    return path


def test_load_rows_merges_dedups_and_drops_conflicts(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.jsonl", [("Наценка 14%.", "TRADE_SECRET"), ("НМЦК 1 млн", "NONE")])
    b = _write(tmp_path / "b.jsonl", [
        ("наценка 15 %", "TRADE_SECRET"), ("НМЦК 2 млн", "TRADE_SECRET"),
        ("Срок 10 дней", "NONE"), ("x", "OTHER"),
    ])
    rows = tr.load_rows([a, b])
    assert sorted(r["text"] for r in rows) == ["Наценка 14%.", "Срок 10 дней"]


def test_params_with_path_list_are_json_serializable(tmp_path: Path) -> None:
    args = tr.parse_args(["--data", str(tmp_path / "a.jsonl"), str(tmp_path / "b.jsonl")])
    dumped = json.dumps(tr.vars_for_json(args))
    assert "a.jsonl" in dumped and "b.jsonl" in dumped
