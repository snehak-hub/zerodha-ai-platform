"""SQLAlchemy tables. JSON columns become JSONB on PostgreSQL."""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BrokerConnection(Base):
    """One Zerodha link per user. Only a masked token reference is stored, never passwords/TOTP/full tokens."""
    __tablename__ = "broker_connections"
    __table_args__ = (UniqueConstraint("user_id", "broker", name="uq_broker_user"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    broker: Mapped[str] = mapped_column(String(32), default="ZERODHA")
    client_id: Mapped[str] = mapped_column(String(64), default="")
    display_name: Mapped[str] = mapped_column(String(160), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    token_hint: Mapped[str] = mapped_column(String(64), default="")
    live_market_synced: Mapped[bool] = mapped_column(Boolean, default=False)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Holding(Base):
    __tablename__ = "holdings"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_holding_user_symbol"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    sector: Mapped[str] = mapped_column(String(80), default="")
    instrument_id: Mapped[str] = mapped_column(String(64), default="")
    quantity: Mapped[int] = mapped_column(Integer)
    average_price: Mapped[float] = mapped_column(Float)
    current_price: Mapped[float] = mapped_column(Float)
    extra: Mapped[dict] = mapped_column(JsonType, default=dict)  # any additional broker fields, round-tripped
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (Index("ix_jobs_user_created", "user_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    portfolio_id: Mapped[str] = mapped_column(String(64))
    timeframe: Mapped[str] = mapped_column(String(8), default="1M")
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Insight(Base):
    __tablename__ = "insights"
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id", ondelete="CASCADE"), primary_key=True)
    payload: Mapped[dict] = mapped_column(JsonType)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id", ondelete="CASCADE"), primary_key=True)
    payload: Mapped[dict] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    card_id: Mapped[str] = mapped_column(String(128))
    user_action: Mapped[str] = mapped_column(String(64))
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_user_id", "user_id", "id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
