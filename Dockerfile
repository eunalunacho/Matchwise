FROM python:3.12-slim AS base
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen

FROM base AS web
COPY . .
CMD ["uv", "run", "uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS bot
COPY . .
CMD ["uv", "run", "python", "-m", "bot.main"]

FROM base AS scheduler
COPY . .
CMD ["uv", "run", "python", "-m", "scheduler.main"]

FROM base AS crawler
RUN uv run playwright install chromium --with-deps
COPY . .
CMD ["uv", "run", "python", "-m", "crawler.main"]
