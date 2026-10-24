"""PDF из сохранённого отчёта: без исходного документа, значений сущностей и LLM."""

from html import escape

import pymupdf

from app.schemas import Report, Violation

PURPOSE_LABELS = {
    "internal": "Внутренний документ",
    "publication": "Для публикации (ЕИС, сайт)",
    "contract": "Договор с контрагентом",
    "international": "Международный контракт",
}
OVERALL_LABELS = {
    "red": ("#cf1322", "Есть серьёзные нарушения"),
    "yellow": ("#d48806", "Есть замечания"),
    "green": ("#389e0d", "Нарушений не найдено"),
}
CATEGORY_LABELS = {
    "approvals": "Согласования", "classification": "Грифы конфиденциальности",
    "retention": "Сроки хранения",
}
STATUS_LABELS = {"ok": "В порядке", "missing": "Отсутствует", "not_required": "Не требуется"}
CSS = """
body { font-family: sans-serif; font-size: 10pt; line-height: 1.3; color: #262626; }
h1 { font-size: 19pt; margin: 0 0 12pt; }
h2 { font-size: 14pt; margin: 16pt 0 8pt; }
h3 { font-size: 11pt; margin: 12pt 0 6pt; }
p { margin: 0 0 7pt; }
table { width: 100%; border-collapse: collapse; }
td, th { border: 0.5pt solid #d9d9d9; padding: 6pt; text-align: left; }
th { font-weight: bold; }
blockquote { margin: 6pt 0 10pt 12pt; padding: 6pt; }
.signal { color: white; padding: 10pt; font-size: 12pt; }
.audit { font-size: 8pt; }
"""


def _paragraph(label: str, value: str) -> str:
    return f"<p><b>{escape(label)}:</b> {escape(value)}</p>"


def _violation_html(violation: Violation) -> str:
    level = "Серьёзное нарушение" if violation.severity == "red" else "Замечание"
    parts = [f"<h3>{escape(level)}: {escape(violation.title)}</h3>"]
    laws = "; ".join(f"{ref.law} {ref.article}" for ref in violation.law_refs)
    parts.append(_paragraph("Основание", laws))
    # В контракте Violation нет description: не подгружаем изменяемый каталог правил.
    for evidence in violation.evidence:
        location = []
        if evidence.page is not None:
            location.append(f"Страница {evidence.page}")
        if evidence.section:
            location.append(f"Раздел: {evidence.section}")
        parts.append(f"<blockquote>{escape(evidence.quote)}</blockquote>")
        if location:
            parts.append(f"<p>{escape('; '.join(location))}</p>")
    parts.append(_paragraph("Рекомендация", violation.recommendation))
    return "".join(parts)


def _report_html(report: Report) -> str:
    _, verdict = OVERALL_LABELS[report.overall]
    parts = ["<h1>Гриф — отчёт проверки документа</h1>"]
    parts.append(_paragraph("Документ", report.filename or "Текстовый запрос"))
    parts.append(_paragraph("Назначение", PURPOSE_LABELS[report.purpose]))
    checked_at = report.created_at.isoformat(sep=" ", timespec="seconds")
    parts.append(_paragraph("Дата проверки", checked_at))
    parts.append(_paragraph("Время обработки", f"{report.processing_ms} мс"))
    parts.append(f'<p class="signal">{escape(verdict)}</p>')
    parts.append("<h2>Чек-лист</h2><table><tr><th>Категория</th><th>Статус</th><th>Комментарий</th></tr>")
    for item in report.checklist:
        cells = (CATEGORY_LABELS[item.category], STATUS_LABELS[item.status], item.comment)
        parts.append("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in cells) + "</tr>")
    parts.append("</table><h2>Нарушения</h2>")
    parts.extend(_violation_html(violation) for violation in report.violations)
    if not report.violations:
        parts.append("<p>Нарушений не найдено</p>")
    parts.append("<h2>Найденные данные</h2><table><tr><th>Тип</th><th>Количество</th></tr>")
    for kind, count in sorted(report.stats.items()):
        parts.append(f"<tr><td>{escape(kind)}</td><td>{escape(str(count))}</td></tr>")
    parts.append("</table><h2>След проверки</h2><div class='audit'>")
    audit = report.audit
    model = audit.llm_model if report.llm_used and audit.llm_model else "не использовалась"
    fields = (
        ("SHA-256 документа", audit.document_sha256), ("Версия правил", audit.ruleset_version),
        ("Версия словарей", audit.dictionaries_version), ("Версия приложения", audit.app_version),
        ("Детекторы", ", ".join(audit.detectors)),
        ("Модель LLM", model),
    )
    parts.extend(_paragraph(label, value) for label, value in fields)
    return "".join(parts) + "</div>"


def render_report_pdf(report: Report) -> bytes:
    """A4 с полями 15 мм, автоматической пагинацией и встроенными шрифтами MuPDF."""
    page_rect = pymupdf.paper_rect("a4")
    margin = 15 * 72 / 25.4
    content_rect = pymupdf.Rect(margin, margin, page_rect.width - margin,
                                page_rect.height - margin - 20)
    story = pymupdf.Story(html=_report_html(report), user_css=CSS)
    flow_rect = pymupdf.Rect(0, 0, content_rect.width, content_rect.height)
    # Отдельная область потока обрезает фон HTML-блоков на границах страниц.
    with story.write_with_links(lambda *_: (flow_rect, flow_rect, None)) as flow:
        with pymupdf.open() as document:
            for number in range(1, len(flow) + 1):
                page = document.new_page(width=page_rect.width, height=page_rect.height)
                page.show_pdf_page(content_rect, flow, number - 1)
                if number == 1:
                    color, verdict = OVERALL_LABELS[report.overall]
                    bounds = page.search_for(verdict)[0]
                    banner = pymupdf.Rect(content_rect.x0, bounds.y0 - 8,
                                          content_rect.x1, bounds.y1 + 8)
                    page.draw_rect(banner, color=None,
                                   fill=pymupdf.sRGB_to_pdf(int(color[1:], 16)), overlay=False)
                footer = pymupdf.Rect(margin, page_rect.height - margin - 14,
                                      page_rect.width - margin, page_rect.height - margin)
                page.insert_htmlbox(
                    footer, f"Гриф · стр. {number}",
                    css="body {font-family:sans-serif;font-size:8pt;color:#666;text-align:center;}",
                )
            return document.tobytes(garbage=4, deflate=True)
