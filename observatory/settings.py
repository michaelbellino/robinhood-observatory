import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    auth_provider: str = field(default_factory=lambda: os.getenv("AUTH_PROVIDER", "github"))
    github_client_id: str = field(default_factory=lambda: os.getenv("GITHUB_CLIENT_ID", ""))
    github_client_secret: str = field(
        default_factory=lambda: os.getenv("GITHUB_CLIENT_SECRET", ""), repr=False
    )
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""), repr=False)
    public_url: str = field(default_factory=lambda: os.getenv("PUBLIC_URL", "http://127.0.0.1:8000"))
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "production"))
    encryption_keys: str = field(default_factory=lambda: os.getenv("ENCRYPTION_KEYS", ""), repr=False)
    session_secret: str = field(default_factory=lambda: os.getenv("SESSION_SECRET", ""), repr=False)
    oidc_issuer: str = field(default_factory=lambda: os.getenv("OIDC_ISSUER", ""))
    oidc_client_id: str = field(default_factory=lambda: os.getenv("OIDC_CLIENT_ID", ""))
    oidc_client_secret: str = field(default_factory=lambda: os.getenv("OIDC_CLIENT_SECRET", ""), repr=False)
    owner_subject: str = field(default_factory=lambda: os.getenv("OWNER_SUBJECT", ""))
    read_token_hash: str = field(default_factory=lambda: os.getenv("MCP_READ_TOKEN_SHA256", ""), repr=False)
    control_token_hash: str = field(
        default_factory=lambda: os.getenv("MCP_CONTROL_TOKEN_SHA256", ""), repr=False
    )
    mcp_internal_host: str = field(default_factory=lambda: os.getenv("MCP_INTERNAL_HOST", ""))
    broker_rpm: int = field(default_factory=lambda: int(os.getenv("BROKER_RPM", "6")))
    active_poll_seconds: int = field(default_factory=lambda: int(os.getenv("ACTIVE_POLL_SECONDS", "180")))
    offhours_poll_seconds: int = field(
        default_factory=lambda: int(os.getenv("OFFHOURS_POLL_SECONDS", "1800"))
    )
    heartbeat_url: str = field(default_factory=lambda: os.getenv("HEARTBEAT_URL", ""), repr=False)
    notification_url: str = field(default_factory=lambda: os.getenv("NOTIFICATION_URL", ""), repr=False)
    notification_token: str = field(default_factory=lambda: os.getenv("NOTIFICATION_TOKEN", ""), repr=False)
    openai_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""), repr=False)
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", ""))
    ai_daily_usd: str = field(default_factory=lambda: os.getenv("AI_DAILY_USD", "0"))
    ai_monthly_usd: str = field(default_factory=lambda: os.getenv("AI_MONTHLY_USD", "0"))
    ai_daily_calls: int = field(default_factory=lambda: int(os.getenv("AI_DAILY_CALLS", "0")))
    ai_input_per_million: str = field(default_factory=lambda: os.getenv("AI_INPUT_USD_PER_MILLION", "0"))
    ai_output_per_million: str = field(default_factory=lambda: os.getenv("AI_OUTPUT_USD_PER_MILLION", "0"))

    def validate(self):
        if not self.database_url:
            raise ValueError("DATABASE_URL required")
        if self.environment == "production":
            if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("Production requires PostgreSQL")
            if not self.public_url.startswith("https://"):
                raise ValueError("Production requires HTTPS PUBLIC_URL")
        if len(self.session_secret) < 32 or not self.encryption_keys:
            raise ValueError("Session secret and encryption keys must be provisioned securely")
        if not self.owner_subject:
            raise ValueError("Explicit OWNER_SUBJECT required; no automatic first-user enrollment")
        if not 1 <= self.broker_rpm <= 60:
            raise ValueError("BROKER_RPM must be 1..60; raise only with documented broker permission")
        if self.active_poll_seconds < 60 or self.offhours_poll_seconds < self.active_poll_seconds:
            raise ValueError("Invalid polling intervals")
        return self
