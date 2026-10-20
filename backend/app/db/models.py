"""Таблицы БД (SQLAlchemy 2).

Колонки минимальные — расширяется в T-009 (API и хранение задач/отчётов).
Отчёт и правило храним целиком как JSONB: их форма задана контрактом
(app.schemas.Report / app.schemas.Rule), дублировать её колонками не нужно.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DocumentRow(Base):
    """Загруженный документ или текстовый запрос. Расширяется в T-009."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    filename: Mapped[str | None] = mapped_column(String(255))  # None для текстового запроса
    purpose: Mapped[str] = mapped_column(String(32))
    stored_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskRow(Base):
    """Задача обработки (зеркало TaskInfo). Расширяется в T-009."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    stage: Mapped[str | None] = mapped_column(String(16))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    report_id: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReportRow(Base):
    """Готовый отчёт: data = Report.model_dump(mode="json"). Расширяется в T-009."""

    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    data: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RuleRow(Base):
    """Правило: data = Rule.model_dump(mode="json"). Расширяется в T-009 / T-010."""

    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    data: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"))
