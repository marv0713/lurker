from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


class Symbol(Base):
    __tablename__ = "symbols"

    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    market: Mapped[str] = mapped_column(String)
    asset_type: Mapped[str] = mapped_column(String)
    industry: Mapped[str | None] = mapped_column(String, default=None)
    concepts: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    is_active: Mapped[bool] = mapped_column(default=True)
    liquidity_tier: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class PriceDaily(Base):
    __tablename__ = "price_daily"

    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    adj_close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)
    amount: Mapped[float | None] = mapped_column(Float, default=None)
    market_cap: Mapped[float | None] = mapped_column(Float, default=None)


class SignalEvent(Base):
    __tablename__ = "signal_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String)
    trade_date: Mapped[date] = mapped_column(Date)
    signal_type: Mapped[str] = mapped_column(String)
    signal_score: Mapped[float] = mapped_column(Float)
    trigger_reason: Mapped[str] = mapped_column(String)
    related_theme_id: Mapped[str | None] = mapped_column(String, default=None)
    raw_metrics: Mapped[dict] = mapped_column(JSON, default=dict)


class AIAttribution(Base):
    __tablename__ = "ai_attributions"

    attribution_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(Integer)
    classification: Mapped[str] = mapped_column(String)
    reason_summary: Mapped[str] = mapped_column(String)
    evidence_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    risk_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    upgrade_recommendation: Mapped[str] = mapped_column(String)
    missing_evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Candidate(Base):
    __tablename__ = "candidates"

    candidate_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date)
    theme_id: Mapped[str] = mapped_column(String)
    primary_symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    expanded_symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    stock_score: Mapped[float] = mapped_column(Float)
    sector_score: Mapped[float] = mapped_column(Float)
    ai_score: Mapped[float] = mapped_column(Float)
    total_score: Mapped[float] = mapped_column(Float)
    visibility_tier: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    downgrade_reason: Mapped[str | None] = mapped_column(String, default=None)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    watch_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_id: Mapped[str] = mapped_column(String)
    symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    entry_reason: Mapped[str] = mapped_column(String)
    current_stage: Mapped[str] = mapped_column(String)
    last_score: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String)
    invalidation_rules: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Report(Base):
    __tablename__ = "reports"

    report_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[date] = mapped_column(Date)
    report_type: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
