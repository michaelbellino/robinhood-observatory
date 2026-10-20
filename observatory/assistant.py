"""Deterministic command assistant: no paid inference and no discretionary strategy."""
from uuid import UUID
from pydantic import Field
from .domain import StrictModel
from . import rehearsal
from .security import require_control
from .service import status, change_pause
from .market import market_status


class Message(StrictModel):
    message: str = Field(min_length=1, max_length=240)
    request_id: UUID


HELP = (
    "Commands: status, portfolio, quotes, limits, paper equity, paper option, pause, resume. "
    "Paper commands create a $1 synthetic example for review, using no real funds or live quote. "
    "This command assistant does not use a language model. Strategies and free-form analysis are not enabled."
)


def respond(db, message: Message):
    command = " ".join(message.message.lower().split())
    command = {"show my portfolio": "portfolio", "show my balance": "portfolio",
               "how is monitoring doing?": "status", "pause monitoring": "pause",
               "resume monitoring": "resume", "help": "help"}.get(command, command)
    if command == "help":
        return {"reply": HELP}
    if command in {"status", "portfolio"}:
        data = status(db)
        snapshot = data.get("snapshot") or {}
        return {"reply": "Stored account observation; check the source time and coverage warnings.",
                "data": {key: data[key] for key in ("account", "paused", "health", "last_success", "analysis")}
                | {"portfolio": snapshot.get("portfolio"), "warnings": snapshot.get("warnings", []),
                   "real_trading": "disabled"}}
    if command == "quotes":
        return {"reply": "Stored quotes; this request does not poll Robinhood.", "data": market_status(db)}
    if command == "limits":
        return {"reply": "These caps apply only to synthetic paper tests.", "data": rehearsal.limits()}
    if command in {"pause", "resume"}:
        actor = require_control()
        return {"reply": "Monitoring state updated. This does not cancel orders or close positions.",
                "data": change_pause(db, actor["sub"], command == "pause", str(message.request_id))}
    if command in {"paper equity", "paper option"}:
        actor = require_control()
        data = rehearsal.create(db, actor["sub"], rehearsal.CreateRehearsal(
            request_id=message.request_id, terms=rehearsal.fixture(command.split()[1])))
        return {"reply": "Synthetic paper ticket created. Review its exact terms below. No order will be sent.",
                "ticket": data}
    return {"reply": "Command not recognized; no action taken. " + HELP}
