"""Direct owner-operated order entry. No scheduler, model tool, or retry worker.

Only the authenticated browser route invokes this module. A transmission consumes
its durable reservation before I/O. An ambiguous response never authorizes retry.
"""
import asyncio
import hashlib
import itertools
import time
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from typing import Literal

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select

from .broker import PreviewBroker, MalformedData
from .db import OrderSubmission, TradeIdea, State, Audit, now, aware
from .domain import StrictModel
from .oauth import RobinhoodOAuth
from .rate_limit import RequestLimiter
from .rehearsal import fingerprint
from . import trade_review as review

_sessions = {}  # Short-lived MCP metadata only, never credentials or order grants.
_rpc_ids = itertools.count(100000)


class SubmitOrder(StrictModel):
    terms_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledge_real_order: Literal[True]


class OwnerOrderBroker(PreviewBroker):
    """Requires an exact, single-use local grant. Never used by the collector."""
    allowed = PreviewBroker.allowed | {"place_equity_order", "place_option_order"}
    account_reads = PreviewBroker.account_reads | {"place_equity_order", "place_option_order"}
    client_name = "robinhood-observatory-owner-order-entry"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.grant = None
        self.consumed = False
        self.cache_key = hashlib.sha256((self.account + "\0" + self.token).encode()).hexdigest()

    async def initialize(self):
        cached = _sessions.get(self.cache_key)
        if cached and time.monotonic()-cached["at"] < 120:
            self.session_id, self.schemas = cached["session_id"], dict(cached["schemas"])
            return
        await super().initialize()

    async def _rpc(self, method, params=None, notification=False):
        self.sequence = next(_rpc_ids)
        return await super()._rpc(method, params, notification)

    async def close(self):
        if self.session_id and self.schemas:
            if self.cache_key not in _sessions and len(_sessions) >= 4:
                _sessions.pop(next(iter(_sessions)))
            _sessions[self.cache_key] = {"at": time.monotonic(), "session_id": self.session_id,
                                        "schemas": dict(self.schemas)}
        else:
            _sessions.pop(self.cache_key, None)
        await super().close()

    def validate_call(self, name, args):
        super().validate_call(name, args)
        if name.startswith("place_") and (self.grant != (name, fingerprint(args)) or self.consumed):
            raise PermissionError("Exact owner submission grant required")

def permitted_terms(terms):
    if terms.asset == "option":
        if len(terms.legs) != 1:
            raise HTTPException(409, "Direct submission currently supports single-leg options; use Robinhood for spreads")
        leg = terms.legs[0]
        if (leg.side, leg.position_effect) not in {("buy", "open"), ("sell", "close")}:
            raise HTTPException(409, "Direct options submission supports opening or closing long calls/puts only")


def validate_review(row, body, generation):
    if (row.status != "awaiting_review" or row.broker_order_id or aware(row.expires_at) <= now()
            or row.terms_hash != body.terms_hash or fingerprint(row.payload) != body.terms_hash):
        raise HTTPException(409, "Order terms changed, expired, or were already handled")
    terms = review.Terms.model_validate(row.payload["terms"])
    permitted_terms(terms)
    if (row.preview_generation != generation or not row.preview or not row.preview_at or aware(row.preview_at) < now()-timedelta(seconds=60)
            or fingerprint(row.preview) != body.preview_hash
            or not review.fresh_preview(row.preview, terms.asset)):
        raise HTTPException(409, "Refresh the broker preview and review it again before submitting")
    review.validate_echo(terms, row.preview)
    if row.preview["order_checks"]:
        raise HTTPException(409, "Broker alerts must be resolved before direct submission; review them in Robinhood")
    return terms


def result(sub):
    state = sub.state
    if state in {"preparing", "transmitting"} and aware(sub.updated_at) < now()-timedelta(minutes=3):
        state = "unknown"
    return {"state": state, "ref_id": sub.ref_id, "order": sub.order,
            "updated_at": aware(sub.updated_at), "message": sub.message or (
                "Submission interrupted. Check Robinhood; automatic resend is disabled." if state == "unknown" else None),
            "may_have_been_submitted": state in {"transmitting", "unknown", "acknowledged"},
            "retry_allowed": state == "not_sent"}


def get_results(db):
    with db.session() as s:
        return {x.idea_id: result(x) for x in s.scalars(select(OrderSubmission)
                .order_by(OrderSubmission.started_at.desc()).limit(100))}


def reserve(db, actor, ident, body):
    """Serialize competing requests before any broker write; reused keys never resubmit."""
    request_hash = fingerprint(body.model_dump(mode="json"))
    with db.session() as s:
        state = s.scalar(select(State).where(State.id == 1).with_for_update())
        row = s.scalar(select(TradeIdea).where(TradeIdea.id == ident).with_for_update())
        if not row:
            raise HTTPException(404, "Idea not found")
        old = s.get(OrderSubmission, ident)
        if old and result(old)["state"] != "not_sent":
            return None, result(old)
        unresolved = s.scalar(select(OrderSubmission).where(OrderSubmission.idea_id != ident,
                              OrderSubmission.state.in_(["preparing", "transmitting", "unknown"])))
        if unresolved:
            raise HTTPException(409, "Another submission is in progress or has an unknown outcome; resolve it before submitting again")
        if not state.account_verified or state.paused:
            raise HTTPException(409, "Resume and verify the selected account before submitting")
        terms = validate_review(row, body, state.generation)
        if row.preview_started_at:
            raise HTTPException(409, "Wait for the current broker preview to finish")
        sub = old or OrderSubmission(idea_id=ident, ref_id=str(uuid4()))
        sub.actor, sub.request_hash, sub.generation = actor, request_hash, state.generation
        sub.state, sub.started_at, sub.updated_at, sub.message = "preparing", now(), now(), None
        s.add(sub)
        s.add(Audit(id=str(uuid4()), actor=actor, action="owner_order_requested", details={"idea_id": ident, "ref_id": sub.ref_id}))
        return (state.account_cipher, terms, sub.ref_id, sub.generation, request_hash), None


async def eligibility(broker, account, terms):
    accounts = (await broker.call("get_accounts", {})).get("accounts") or []
    selected = next((x for x in accounts if x and x.get("account_number") == account), None)
    if (not selected or selected.get("agentic_allowed") is not True or selected.get("state") != "active"
            or selected.get("deactivated") is not False or selected.get("permanently_deactivated") is not False):
        raise HTTPException(409, "The selected account is not currently eligible for submission through this connection")
    if terms.asset == "option":
        if selected.get("brokerage_account_type") == "trust_revocable" and not selected.get("user_option_level"):
            raise HTTPException(409, "This user has no verified options approval on the selected account")
        level = selected.get("user_option_level", selected.get("option_level"))
        if level not in {"option_level_2", "option_level_3"}:
            raise HTTPException(409, "Options approval is unavailable through this connection")
    # Opening a long requires no additional inventory checks; the fresh broker
    # preview gates funds. A sell may only close a verified long holding.
    is_sell = terms.side == "sell" if terms.asset == "equity" else terms.legs[0].side == "sell"
    if is_sell:
        args = {"account_number": account}
        if terms.asset == "option":
            args["nonzero"] = True
            args["option_ids"] = str(terms.legs[0].option_id)
            args["type"] = "long"
        positions = await broker.pages(f"get_{terms.asset}_positions", "positions", args)
        if terms.asset == "equity":
            held = [x for x in positions if x.get("symbol") == terms.symbol]
        else:
            held = [x for x in positions if x.get("option_id") == str(terms.legs[0].option_id) and x.get("type") == "long"]
        if not held or sum(Decimal(str(x.get("quantity", "0"))) for x in held) < terms.quantity:
            raise HTTPException(409, "Insufficient verified long holdings for this sell order")


def checked_order(terms, response):
    order = response.get("order")
    if not isinstance(order, dict):
        raise MalformedData()
    UUID(order["id"])
    if not isinstance(order.get("state"), str) or not order["state"]:
        raise MalformedData()
    echoed = dict(order)
    if terms.asset == "equity":
        echoed["limit_price"] = order.get("price")
    review.validate_echo(terms, echoed, require_checks=False)
    if (order.get("trigger") != "immediate" or order.get("market_hours") != terms.market_hours
            or order.get("time_in_force") != terms.time_in_force):
        raise MalformedData()
    if terms.asset == "option" and (order.get("chain_symbol") != terms.symbol
            or Decimal(str(order.get("trade_value_multiplier"))) != terms.legs[0].multiplier):
        raise MalformedData()
    return review.mask_accounts(order)


async def submit(db, settings, vault, ident, actor, body, broker_factory=OwnerOrderBroker):
    ident = str(ident)
    reservation, previous = reserve(db, actor, ident, body)
    if previous is not None:
        return previous
    cipher, terms, ref_id, generation, request_hash = reservation
    broker, dispatched = None, False
    try:
        account = vault.decrypt(cipher)
        broker = broker_factory(await RobinhoodOAuth(db, vault, settings).access_token(), account, settings.broker_rpm)
        broker.limiter = RequestLimiter(db, settings.broker_rpm)
        await broker.initialize()
        await eligibility(broker, account, terms)
        args = terms.broker_arguments(account)
        # Placement schema deliberately differs from review: these two fields
        # request fee/contract metadata only and must not enter the write call.
        args.pop("chain_symbol", None)
        args.pop("underlying_type", None)
        args["ref_id"] = ref_id
        name = f"place_{terms.asset}_order"
        broker.grant = (name, fingerprint(args))

        async def before_transmission():
            nonlocal dispatched
            with db.session() as s:
                state = s.scalar(select(State).where(State.id == 1).with_for_update())
                row = s.scalar(select(TradeIdea).where(TradeIdea.id == ident).with_for_update())
                sub = s.get(OrderSubmission, ident)
                if (not state.account_verified or state.paused or state.generation != generation
                        or state.account_cipher != cipher or sub.state != "preparing"
                        or sub.ref_id != ref_id or sub.request_hash != request_hash):
                    raise HTTPException(409, "Account or submission state changed; refresh before continuing")
                validate_review(row, body, generation)
                sub.state, sub.updated_at = "transmitting", now()
                row.status = "submission_in_progress"
            dispatched, broker.consumed = True, True

        broker.preflight = before_transmission
        response = await broker.call(name, args)
        order = checked_order(terms, response)
        with db.session() as s:
            sub = s.get(OrderSubmission, ident)
            row = s.get(TradeIdea, ident)
            sub.state, sub.order, sub.updated_at = "acknowledged", order, now()
            sub.message = "Broker acknowledged the order. Submission is not proof of a fill."
            row.status, row.broker_order_id = "submitted_by_owner", order["id"]
            s.add(Audit(id=str(uuid4()), actor=actor, action="owner_order_acknowledged",
                        details={"idea_id": ident, "ref_id": ref_id, "order_id": order["id"]}))
            return result(sub)
    except (Exception, asyncio.CancelledError) as exc:
        with db.session() as s:
            sub = s.get(OrderSubmission, ident)
            sub.state, sub.updated_at = ("unknown" if dispatched else "not_sent"), now()
            row = s.get(TradeIdea, ident)
            row.status = "submission_unknown" if dispatched else "awaiting_review"
            sub.message = ("Broker outcome unknown. Check Robinhood before doing anything else; this app will not resend."
                if dispatched else exc.detail if isinstance(exc, HTTPException)
                else "Order was not sent. Refresh the preview and check the connection before trying again.")
            outcome = result(sub)
        if isinstance(exc, asyncio.CancelledError):
            raise
        return outcome
    finally:
        if broker:
            try:
                await broker.close()
            except Exception:
                pass  # Closing transport cannot change a recorded broker outcome.
