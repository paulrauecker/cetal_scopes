# AGENTS.md

## Project status

Early scaffold. `Capture` and `Channel` (the core container) are implemented in
`src/cetal_scopes/{capture,channel}.py` and exported from `__init__.py`; the
metadata, antenna, I/O and `Scope` pieces described in `docs/architecture.md`
are **not implemented yet**. Treat docs and docstrings as design intent, not
current behavior — verify against source before relying on them.

## Architecture

Read `docs/architecture.md` before changing the data model. In short:

- `Scope` (ABC) drivers acquire and return a `Capture`.
- `Capture` is the persistence + analysis boundary: 2D `raw` (ADC codes) and
  `volts` (float64) arrays, shared `t0`/`dt`, `metadata`, and
  `channels: dict[str, Channel]`.
- `Channel` is a row-view of the Capture arrays plus an `Antenna` (persisted
  inline); `time` is derived (`t0 + arange(n) * dt`), never stored.
- `Shot` only groups `Capture`s in memory; it is not persisted.
- On disk: `<stem>.json` + `<stem>.volts.npy` + `<stem>.raw.npy`.
- Vendor SDKs (`spcm`, `spcm-core`, `pyvisa`, `pyvisa-py`) are hard deps but
  imported lazily inside `scopes/` so core import and CI need no hardware.
- `pyspcm` is not on PyPI; use `spcm` / `spcm_core` instead.

## Toolchain

- `uv` manages everything (Python 3.12+, `uv_build` backend, **src layout**).
- Dev shell is Nix + direnv (`use flake`); `flake.nix` pins the interpreter and
  sets `UV_PYTHON_DOWNLOADS=never`. Do not install a separate Python via uv.
- Dependencies live in `pyproject.toml`; `uv.lock` is authoritative.

## Commands

Setup first: `uv sync --all-groups` (direnv runs this automatically in the shell).

- Lint: `uv run ruff check src tests`
- Format: `uv run ruff format src tests` (CI enforces `--check`)
- Typecheck: `uv run pyright src`
- Test: `uv run pytest`
  - Single test: `uv run pytest tests/test_capture.py::test_default_channel_names_and_shape`
- Docs preview: `uv run mkdocs serve` (build: `uv run mkdocs build`, output `site/`)

There is no ruff/pyright config; defaults apply (ruff line length 88, double
quotes). CI (`.github/workflows/ci.yml`) runs lint -> typecheck -> test on push
to `main` only.

## Conventions

- New code goes in `src/cetal_scopes/`. The package ships `py.typed`; keep all
  code type-annotated, as `pyright src` is required to pass.
- Coverage is on by default via pytest `addopts` (`--cov=cetal_scopes
  --cov-report=term-missing`).
- Docstrings use the NumPy style; mkdocstrings is configured for it.
- Docs are auto-deployed to GitHub Pages from `main` via
  `.github/workflows/docs.yml`.
