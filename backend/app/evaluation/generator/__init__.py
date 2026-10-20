"""Воспроизводимый синтетический корпус для оценки детекции (T-011b)."""

from app.evaluation.generator.build import make_document, read_for_markup
from app.evaluation.generator.templates import TEMPLATES

__all__ = ["TEMPLATES", "make_document", "read_for_markup"]
