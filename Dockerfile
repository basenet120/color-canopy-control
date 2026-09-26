FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY conductor ./conductor
COPY config ./config
COPY web ./web

ENV PATH="/app/.venv/bin:$PATH"
CMD ["sh", "-c", "exec uvicorn conductor.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
