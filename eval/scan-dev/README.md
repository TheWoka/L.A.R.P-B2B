# Корпус сканов `dev` → PDF (T-077b)

Сгенерировано `eval/make_scan_corpus.py`, не редактировать вручную.
Источник: `eval/dev`, 15 документов; dpi=200, seed=7.
Каждый PDF — только картинки: DejaVuSans 11 pt, поля 2 см, поворот ±1,5°, шум sigma 6,
JPEG 85.
Эталон перенесён в координаты текста OCR посимвольным выравниванием
(см. `eval/README.md`, раздел «Сканы»).

## Сущности, потерянные OCR целиком

Они остаются в эталоне (`"lost": true`, нулевая длина) и считаются пропуском.

| Тип | В эталоне | Потеряно OCR |
|---|---:|---:|
| ADDRESS | 3 | 0 |
| BANK_ACCOUNT | 1 | 0 |
| BIRTH_DATE | 3 | 0 |
| CARD | 1 | 0 |
| EMAIL | 5 | 0 |
| FINANCIAL | 11 | 0 |
| INN | 1 | 0 |
| PASSPORT | 1 | 0 |
| PERSON | 42 | 0 |
| PHONE | 5 | 0 |
| SNILS | 2 | 0 |
| SPECIAL_CATEGORY | 20 | 0 |
| TRADE_SECRET | 55 | 0 |
| **Всего** | **150** | **0** |

## Документы

| Документ | Страниц | Похожесть OCR | Потеряно |
|---|---:|---:|---:|
| 01_supplier_letter | 1 | 0.996 | 0 |
| 02_manager_chat | 1 | 0.998 | 0 |
| 03_cost_calculation | 1 | 0.998 | 0 |
| 04_internal_memo | 1 | 0.997 | 0 |
| 05_negotiation_minutes | 1 | 0.997 | 0 |
| 06_commercial_offer | 1 | 0.993 | 0 |
| 07_sales_plan | 1 | 0.988 | 0 |
| 08_technical_knowhow | 1 | 0.990 | 0 |
| 09_procurement_report | 1 | 0.990 | 0 |
| 10_personnel_reference | 1 | 0.992 | 0 |
| 11_sick_leave | 1 | 0.992 | 0 |
| 12_employee_profile | 1 | 0.996 | 0 |
| 13_questionnaire | 1 | 0.993 | 0 |
| 14_supplier_terms | 1 | 0.994 | 0 |
| 15_public_procurement | 1 | 0.995 | 0 |

Похожесть — `difflib.SequenceMatcher.ratio()` чистого текста и текста OCR
(1.0 — совпали посимвольно).
