default:
    @just --list

setup:
    uv sync --locked

fmt:
    uv run --locked ruff format src tests
    uv run --locked ruff check --fix src tests

fmt-check:
    uv run --locked ruff format --check src tests

check:
    bash scripts/check

test:
    uv run --locked pytest -q

start:
    uv run --locked hacklab doctor

doctor:
    uv run --locked hacklab doctor

bench:
    uv run --locked hacklab benchmark --config configs/mock.toml --out results/mock.json

benchmark: bench

build:
    uv build

vm-build:
    nix build .#vm

vm:
    bash scripts/vm-run
