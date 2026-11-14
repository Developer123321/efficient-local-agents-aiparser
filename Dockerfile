# syntax=docker/dockerfile:1
# The agent as the organizers build and run it: from the archive of the
# committed tree, with 2 CPUs and 4 GB. At run time only their gateway is
# reachable, so every dependency is installed from uv.lock at build time.
# Image names are fully qualified so that Docker and Podman resolve them alike.
#
# Run contract: ARENA_BASE_URL, ARENA_API_KEY, OPENROUTER_BASE_URL,
# OPENROUTER_API_KEY and ARENA_RUN_KIND come from the environment. Without
# arguments, or with options only, the container runs `hacklab arena run`;
# with a command it runs that hacklab command:
#   docker run --rm --env-file .env -e ARENA_RUN_KIND=dev IMAGE --tasks t01
#   docker run --rm IMAGE doctor --strict --config configs/live.toml
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
    HACKLAB_DB=/tmp/hacklab/tracking.sqlite \
    HACKLAB_ARENA_DIR=/tmp/hacklab/arena
USER agent
WORKDIR /app
ENTRYPOINT ["/bin/sh", "-c", "case \"${1:-}\" in ''|-*) exec hacklab arena run \"$@\";; *) exec hacklab \"$@\";; esac", "hacklab"]
CMD []
