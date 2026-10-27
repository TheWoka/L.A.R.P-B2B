"""Markdown из сохранённого отчёта, без исходника и значений сущностей."""

from app.schemas import Report, Violation
from app.services.report.pdf_export import (
    CATEGORY_LABELS,
    OVERALL_LABELS,
    PURPOSE_LABELS,
    STATUS_LABELS,
)


def _escape_md(value: str) -> str:
    """HTML-сущности сохраняют текст, не создавая таблицы, HTML или Markdown-ссылки."""
    value = " ".join(value.splitlines())
    return "".join(
        f"&#{ord(char)};" if char in "&\\|`*_#<>[]()!+-~" else char
        for char in value
    )


def _violation_md(violation: Violation) -> str:
    level = "Серьёзное нарушение" if violation.severity == "red" else "Замечание"
    parts = [f"### {level}: {_escape_md(violation.title)}"]
    laws = "; ".join(f"{ref.law} {ref.article}" for ref in violation.law_refs)
    parts.append(f"Основание: {_escape_md(laws)}")
    # Описание в контракте Violation представлено title, как и в PDF.
    for evidence in violation.evidence:
        parts.append("\n".join(f"> {_escape_md(line)}" for line in evidence.quote.splitlines()))
        location = []
        if evidence.page is not None:
            location.append(f"Страница {evidence.page}")
        if evidence.section:
            location.append(f"Раздел: {evidence.section}")
        if location:
            parts.append(_escape_md("; ".join(location)))
    parts.append(f"Рекомендация: {_escape_md(violation.recommendation)}")
    return "\n\n".join(parts)


def _audit_md(report: Report) -> str:
    audit = report.audit
    model = audit.llm_model if report.llm_used and audit.llm_model else "не использовалась"
    fields = (
        ("SHA-256 документа", audit.document_sha256), ("Версия правил", audit.ruleset_version),
        ("Версия словарей", audit.dictionaries_version), ("Версия приложения", audit.app_version),
        ("Детекторы", ", ".join(audit.detectors)), ("Модель LLM", model),
    )
    return "\n\n".join(f"{label}: {_escape_md(value)}" for label, value in fields)


def render_report_md(report: Report) -> str:
    """Те же разделы и подписи, что в PDF; цитаты уже замаскированы в Report."""
    verdict = OVERALL_LABELS[report.overall][1].lower()
    checked_at = report.created_at.isoformat(sep=" ", timespec="seconds")
    parts = [
        "# Гриф — отчёт проверки",
        f"Документ: {_escape_md(report.filename or 'Текстовый запрос')}",
        f"Назначение: {PURPOSE_LABELS[report.purpose]}",
        f"Дата проверки: {checked_at}",
        f"Время обработки: {report.processing_ms} мс",
        f"Итог: {verdict}",
        "## Чек-лист",
    ]
    rows = ["| Категория | Статус | Комментарий |", "| --- | --- | --- |"]
    for item in report.checklist:
        cells = (CATEGORY_LABELS[item.category], STATUS_LABELS[item.status], item.comment)
        rows.append("| " + " | ".join(_escape_md(cell) for cell in cells) + " |")
    parts.extend(["\n".join(rows), "## Нарушения"])
    parts.extend(_violation_md(violation) for violation in report.violations)
    if not report.violations:
        parts.append("Нарушений не найдено")
    parts.append("## Найденные данные")
    rows = ["| Тип | Количество |", "| --- | --- |"]
    rows.extend(f"| {_escape_md(kind)} | {count} |" for kind, count in sorted(report.stats.items()))
    parts.extend(["\n".join(rows), "## След проверки", _audit_md(report)])
    return "\n\n".join(parts) + "\n"
