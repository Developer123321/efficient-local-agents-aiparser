# syntax=docker/dockerfile:1
# The agent as it runs in the evaluation: no network at run time, so every
# dependency is installed from uv.lock at build time. Image names are fully
# qualified so that Docker and Podman resolve them alike.
FROM docker.io/library/python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/local/bin/python3
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM docker.io/library/python:3.13-slim
RUN useradd --uid 10001 --create-home agent && install -d -o agent -g agent /work
COPY --from=build /app/.venv /app/.venv
COPY configs /app/configs
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HACKLAB_DB=/tmp/hacklab/tracking.sqlite
USER agent
WORKDIR /app
ENTRYPOINT ["hacklab"]
# Replace with the organizers' run contract once it is known, for example:
# CMD ["submit", "--config", "configs/live.toml", "--input", "/data/tasks.jsonl", \
#      "--output", "/work/answers.jsonl", "--root", "/data"]
CMD ["doctor"]
