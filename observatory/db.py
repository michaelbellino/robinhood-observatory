from contextlib import contextmanager
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Integer, Boolean, DateTime, Text, JSON, Numeric, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


class Base(DeclarativeBase):
    pass


class State(Base):
    __tablename__ = "monitor_state"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    paused: Mapped[bool] = mapped_column(Boolean, default=True)
    account_cipher: Mapped[str | None] = mapped_column(Text)
    account_label: Mapped[str | None] = mapped_column(String(80))
    account_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), default="not_connected")
    analysis_status: Mapped[str] = mapped_column(String(40), default="disabled")
    last_analysis: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(Integer, default=0)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    config: Mapped[dict] = mapped_column(JSON, default=lambda: {"watchlist": [], "calendars": {}})


class RequestBudget(Base):
    __tablename__ = "broker_request_budget"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    next_allowed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarketState(Base):
    __tablename__ = "market_state"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), default="not_collected")
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)


class Secret(Base):
    __tablename__ = "encrypted_secrets"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    cipher: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MarketSample(Base):
    __tablename__ = "market_samples"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class OAuthFlow(Base):
    __tablename__ = "oauth_flows"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_cipher: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    owner: Mapped[str] = mapped_column(String(255))


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    source: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_state: Mapped[str] = mapped_column(String(30), default="pending")


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    snapshot_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(100))
    strategy_version: Mapped[str] = mapped_column(String(40), default="observe-only/v1")


class PaperProposal(Base):
    __tablename__ = "paper_proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="paper_unfilled")


class Rehearsal(Base):
    """Isolated workflow tests. Never brokerage orders, approvals, or simulated fills."""
    __tablename__ = "review_rehearsals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    request_hash: Mapped[str] = mapped_column(String(64))
    terms_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    reserved_usd: Mapped[float] = mapped_column(Numeric(18, 8))
    status: Mapped[str] = mapped_column(String(30), default="awaiting_paper_review")


class AICost(Base):
    __tablename__ = "ai_cost_reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    reserved_usd: Mapped[float] = mapped_column(Numeric(14, 8))
    state: Mapped[str] = mapped_column(String(30), default="reserved")


class Work(Base):
    __tablename__ = "work_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(30), default="running")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)


class Observation(Base):
    __tablename__ = "session_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), default="pending")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class Database:
    def __init__(self, url):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        args = (
            {"check_same_thread": False}
            if url.startswith("sqlite")
            else {
                "connect_timeout": 5,
                "options": "-c statement_timeout=30000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=60000",
            }
        )
        self.engine = create_engine(url, pool_pre_ping=True, hide_parameters=True, connect_args=args)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self):
        with self.sessions.begin() as session:
            yield session

    def initialize_test(self):
        Base.metadata.create_all(self.engine)
        with self.session() as s:
            if s.get(State, 1) is None:
                s.add(State(id=1))
            if s.get(RequestBudget, 1) is None:
                s.add(RequestBudget(id=1))

    def bound(self, connection):
        parent = self

        class BoundDatabase:
            engine = parent.engine

            @contextmanager
            def session(self):
                if connection.invalidated or connection.closed:
                    raise RuntimeError("Leadership database session lost")
                with parent.sessions(bind=connection) as session:
                    with session.begin():
                        yield session

        return BoundDatabase()

    @contextmanager
    def leader(self):
        # The lock lives on one physical PostgreSQL session for the entire work cycle.
        # No lease expires while a slow broker request remains in flight.
        if self.engine.dialect.name != "postgresql":
            raise RuntimeError("Worker leadership requires PostgreSQL")
        with self.engine.connect() as connection:
            held = connection.scalar(text("SELECT pg_try_advisory_lock(734105016)"))
            connection.commit()
            try:
                yield connection if held else None
            finally:
                if held and not connection.invalidated:
                    connection.execute(text("SELECT pg_advisory_unlock(734105016)"))
                    connection.commit()
