"""Оценка качества детекции на синтетическом корпусе. Задача T-014.

Запуск (после реализации): `python eval/run_eval.py --corpus eval/corpus --out eval/results`

Что делает:
1. Для каждого документа корпуса (T-011) вызывает extract() и detect().
2. Сравнивает найденные сущности с разметкой: совпадение — тот же type и пересечение
   отрезков [start, end) не меньше 50%.
3. Считает precision / recall / F1 по каждому EntityType и по категориям (pd, trade_secret).
4. Сохраняет таблицу в eval/results/ и печатает итог: recall ПДн ≥ 0.90,
   recall коммерческой тайны ≥ 0.85 — критерии приёмки хакатона.
"""


def main() -> int:
    raise NotImplementedError("T-014: подсчёт precision / recall")


if __name__ == "__main__":
    raise SystemExit(main())
