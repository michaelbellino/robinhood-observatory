// Reviewed desired state only. Applying provisions paid resources; obtain explicit approval first.
// No GitHub source binding: releases are manual workflow_dispatch at an exact SHA.
import { defineRailway, project, service, database as railwayDatabase, preserve } from "railway/iac";
export default defineRailway(() => {
  const database = railwayDatabase("database", "postgres", {
    image: "postgres:17.7-bookworm@sha256:86e0b703649d7a792bd9243ee28afc9d8f7c6b2b5638077c9d6882d4d472bbfd",
    defaultMountPath: "/var/lib/postgresql/data",
  });
  database.networking = { serviceDomains: {}, tcpProxies: {} };
  const common = {
    DATABASE_URL: database.env.DATABASE_URL,
    APP_ENV: "production", PUBLIC_URL: preserve(), OWNER_SUBJECT: preserve(),
    ENCRYPTION_KEYS: preserve(), SESSION_SECRET: preserve(),
    BROKER_RPM: "6", ACTIVE_POLL_SECONDS: "180", OFFHOURS_POLL_SECONDS: "1800",
  };
  const deploy = { sleepApplication: false, restartPolicyType: "ALWAYS" as const, numReplicas: 1 };
  const web = service("web", {
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: "uvicorn observatory.api:create_app --factory --host 0.0.0.0 --port 8000 --no-access-log --no-server-header",
    preDeploy: "alembic upgrade head", healthcheck: "/health/live", healthcheckTimeout: 60,
    deploy,
    env: { ...common, PORT: "8000", AUTH_PROVIDER: "github", GITHUB_CLIENT_ID: preserve(),
      GITHUB_CLIENT_SECRET: preserve(), MCP_READ_TOKEN_SHA256: preserve(), MCP_CONTROL_TOKEN_SHA256: preserve(),
      MCP_INTERNAL_HOST: "web.railway.internal:8000" },
  });
  const worker = service("worker", {
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" }, start: "python -m observatory.worker", deploy,
    env: { ...common, HEARTBEAT_URL: preserve(), NOTIFICATION_URL: preserve(), NOTIFICATION_TOKEN: preserve(),
      OPENAI_API_KEY: preserve(), OPENAI_MODEL: preserve(), AI_DAILY_USD: "0", AI_MONTHLY_USD: "0",
      AI_DAILY_CALLS: "0", AI_INPUT_USD_PER_MILLION: "0", AI_OUTPUT_USD_PER_MILLION: "0" },
  });
  // Database and worker have no public domains/TCP proxies. Add web HTTPS only after auth is configured.
  // Optional Secure MCP Tunnel is created separately after OpenAI organization enrollment is verified.
  return project("robinhood-observatory", { resources: [database, web, worker] });
});
