default:
    @just --list

setup:
    uv sync --locked

fmt:
    ruff format src tests scripts
    ruff check --fix src tests scripts

fmt-check:
    ruff format --check src tests scripts

check:
    bash scripts/check

test:
    uv run --locked pytest -q

start:
    uv run --locked hacklab doctor

doctor:
    uv run --locked hacklab doctor

dev-setup:
    uv run --locked hacklab-dev setup
    if [ -n "${HACKATHON_SKILLS_DIR:-}" ]; then bash scripts/vm-skills; fi

dev-status:
    uv run --locked hacklab-dev status

dev-down:
    uv run --locked hacklab-dev down

dev-check:
    uv run --locked python scripts/smoke-dev-tools.py

index:
    uv run --locked hacklab-dev index

bench:
    uv run --locked hacklab benchmark --config configs/mock.toml --out results/mock.json

benchmark: bench

build:
    uv build

vm-build:
    nix build .#vm

vm:
    bash scripts/vm-run
