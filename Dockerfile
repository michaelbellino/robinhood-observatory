FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.23
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY observatory ./observatory
COPY migrations ./migrations
COPY alembic.ini ./
RUN useradd --uid 10001 --create-home monitor && chown -R monitor:monitor /app
USER monitor
ENV PATH="/app/.venv/bin:$PATH"
CMD ["uvicorn", "observatory.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-server-header"]
