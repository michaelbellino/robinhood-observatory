from datetime import timedelta
import pytest
from sqlalchemy import select
from observatory.analysis import reserve, analyze, minimized_context
from observatory.db import Snapshot, AICost, now
from observatory.oauth import RobinhoodOAuth, TOKEN
from observatory.broker import AuthorizationExpired


def enable(settings):
    settings.openai_key = "synthetic-key"
    settings.openai_model = "synthetic-model"
    settings.ai_daily_calls = 1
    settings.ai_daily_usd = "1"
    settings.ai_monthly_usd = "1"
    settings.ai_input_per_million = "1"
    settings.ai_output_per_million = "2"


def test_spend_cap_persists(db, settings):
    enable(settings)
    assert reserve(db, settings, 100) is not None
    assert reserve(db, settings, 100) is None


def test_too_small_budget_prevents_request(db, settings):
    enable(settings)
    settings.ai_daily_usd = "0.000001"
    assert reserve(db, settings, 100) is None


def test_unpriced_model_disabled(db, settings):
    enable(settings)
    settings.ai_input_per_million = "0"
    assert reserve(db, settings, 100) is None


def test_private_account_fields_not_in_model_context():
    snap = Snapshot(
        id="test",
        observed_at=now(),
        source="synthetic",
        payload={
            "account_number": "PRIVATE-TEST",
            "portfolio": {"cash": "9999"},
            "equity_positions": [],
            "contracts": [],
            "warnings": [],
        },
    )
    context = str(minimized_context(snap))
    assert "PRIVATE-TEST" not in context and "9999" not in context


async def test_failed_model_keeps_reservation(db, settings):
    enable(settings)

    class Failed:
        async def summarize(self, *args):
            raise RuntimeError("synthetic provider failure")

    snap = Snapshot(id="test", observed_at=now(), source="synthetic", payload={})
    assert await analyze(db, settings, snap, Failed()) == "degraded"
    with db.session() as s:
        reservation = s.scalar(select(AICost))
        assert reservation.state == "uncertain_or_failed" and reservation.reserved_usd > 0


async def test_hallucinated_reference_rejected(db, settings):
    enable(settings)

    class Bad:
        async def summarize(self, *args):
            return {
                "summary": "test",
                "observations": [],
                "uncertainties": [],
                "source_refs": ["invented"],
                "mode": "observe_only",
            }

    snap = Snapshot(id="test", observed_at=now(), source="synthetic", payload={})
    assert await analyze(db, settings, snap, Bad()) == "degraded"


async def test_refresh_rotates_and_persists(configured, settings, vault, respx_mock):
    with configured.session() as s:
        vault.put(s, "robinhood_client", {"client_id": "synthetic-client"})
        vault.put(
            s,
            "robinhood_tokens",
            {
                "access_token": "old-synthetic",
                "refresh_token": "old-refresh-synthetic",
                "expires_at": (now() - timedelta(seconds=1)).isoformat(),
            },
        )
    respx_mock.post(TOKEN).respond(
        200,
        json={
            "access_token": "new-synthetic",
            "refresh_token": "new-refresh-synthetic",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    )
    oauth = RobinhoodOAuth(configured, vault, settings)
    assert await oauth.access_token() == "new-synthetic"
    with configured.session() as s:
        assert vault.get(s, "robinhood_tokens")["refresh_token"] == "new-refresh-synthetic"


async def test_refresh_revoked(configured, settings, vault, respx_mock):
    with configured.session() as s:
        vault.put(s, "robinhood_client", {"client_id": "synthetic-client"})
        vault.put(
            s,
            "robinhood_tokens",
            {
                "access_token": "old",
                "refresh_token": "synthetic",
                "expires_at": (now() - timedelta(seconds=1)).isoformat(),
            },
        )
    respx_mock.post(TOKEN).respond(400, json={"error": "invalid_grant"})
    with pytest.raises(AuthorizationExpired):
        await RobinhoodOAuth(configured, vault, settings).access_token()


def test_unknown_token_lifetime_not_assumed(db, settings, vault):
    with pytest.raises(ValueError):
        RobinhoodOAuth(db, vault, settings).validate_token(
            {"access_token": "synthetic", "token_type": "Bearer"}
        )


async def test_callback_wrong_issuer_rejected(db, settings, vault):
    with pytest.raises(ValueError):
        await RobinhoodOAuth(db, vault, settings).callback("owner", "state", "code", "https://malicious.test")
