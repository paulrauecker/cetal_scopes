# AGENTS.md

## Project status

Early scaffold. `Capture`, `Channel`, `Antenna` (with a frequency-dependent
`TransferFunction`) live in `src/cetal_scopes/{capture,channel,antenna}.py` and
are exported from `__init__.py`; the pydantic `CaptureFile` schema is in
`metadata.py` and `load_capture` / `save_capture` in `storage.py`. The `Scope`
driver template lives in `src/cetal_scopes/scopes/base.py`. The Siglent SDS6204L
driver (`src/cetal_scopes/scopes/siglent.py`) works over the raw-socket LAN
interface with a lazy PyVISA fallback for USB/VXI-11, and has been validated
against an SDS6204L over LAN (single-channel capture). A downstream
`cetal_scopes.analysis` subpackage (time / spectral / analytic / metrics plus
result classes) and `cetal_scopes.plotting` are implemented. Downstream
applications live in `apps/` (a real-time signal/FFT viewer, a Tk B-dot probe
viewer, and a Dash/Plotly B-dot web app); they are not part of the library. The
web app is a uv workspace member (`apps/bdot_web`) with its own `dash`/`plotly`
dependencies. `Shot` groups captures with per-capture time offsets
and is in-memory only; `analysis.fields` turns a calibrated B-dot's volts into
`dB/dt` and `B`, and `analysis.alignment` measures inter-capture offsets. Treat
docs and docstrings as design intent, not current behavior — verify against
source before relying on them.

## Architecture

Read `docs/architecture.md` before changing the data model. In short:

- `Scope` (ABC) drivers acquire and return a `Capture`.
- `Capture` is the persistence + analysis boundary: 2D `raw` (ADC codes) and
  `volts` (float64) arrays, shared `t0`/`dt`, `metadata`, and
  `channels: dict[str, Channel]`.
- `Channel` is a row-view of the Capture arrays plus an `Antenna` (persisted
  inline); `time` is derived (`t0 + arange(n) * dt`), never stored.
- `Shot` groups `Capture`s in memory with per-capture time offsets; it is not
  persisted.
- `cetal_scopes.analysis` (`time`, `spectral`, `analytic`, `metrics`, `fields`,
  `alignment`, `results`) operates on `Channel`s and returns
  `Spectrum`/`ChannelStats`/`TimeOffset`; it never mutates captures. `cetal_scopes.plotting` draws captures and spectra with matplotlib.
- On disk: `<stem>.json` + `<stem>.volts.npy` + `<stem>.raw.npy`.
- Vendor SDKs (`spcm`, `spcm-core`, `pyvisa`, `pyvisa-py`) are hard deps but
  imported lazily inside `scopes/` so core import and CI need no hardware.
- `pyspcm` is not on PyPI; use `spcm` / `spcm_core` instead.
- Siglent gotcha: `:SYSTem:SELFCal` is **asynchronous and slow** (minutes; the
  front panel shows "doing self cal ... NN%"). There is **no reliable SCPI
  progress or completion signal**: it returns immediately, `*OPC?` is `1` at
  once, `:SYSTem:SELFCal?` reports `DONE` even mid-run (observed at 78%), and
  `STATus:OPERation` stays 0. Wait for the front panel to finish. While it
  runs, waveform queries time out and `:ACQuire:NUMACq?` may stop advancing.
  Also expect the deterministic ADC comb from the 16-bit transfer path: a
  256-sample pattern giving spurs at every `k*fs/256` (`fs/8`, `fs/4`, `fs/2` are
  the strongest) on every channel including open ones. The driver defaults to
  `sample_width="WORD"`; `BYTE` is a lossy top-byte truncation and still carries
  the comb. Use `analysis.remove_adc_comb()` for broadband work or analyze a
  band/tone rather than the global FFT peak.

## Toolchain

- `uv` manages everything (Python 3.12+, `uv_build` backend, **src layout**).
- Dev shell is Nix + direnv (`use flake`); `flake.nix` pins the interpreter and
  sets `UV_PYTHON_DOWNLOADS=never`. Do not install a separate Python via uv.
- Dependencies live in `pyproject.toml`; `uv.lock` is authoritative.

## Commands

Setup first: `uv sync --all-groups --all-packages` (direnv runs this
automatically in the shell; `--all-packages` pulls in the `apps/bdot_web`
workspace member's `dash`/`plotly`).

- Lint: `uv run ruff check src tests apps`
- Format: `uv run ruff format src tests apps` (CI enforces `--check`)
- Typecheck: `uv run pyright src apps`
- Test: `uv run pytest`
  - Single test: `uv run pytest tests/test_capture.py::test_default_channel_names_and_shape`
  - App test: `uv run pytest apps/realtime_viewer/tests`
- Docs preview: `uv run mkdocs serve` (build: `uv run mkdocs build`, output `site/`)

  Ruff defaults apply (line length 88, double quotes); `[tool.pyright]` adds
  `apps/realtime_viewer`, `apps/bdot_probe` and `apps/bdot_web` to `extraPaths`
  so the app modules resolve. CI (`.github/workflows/ci.yml`) runs lint -> typecheck -> test on push
  to `main` only.

## Conventions

- New code goes in `src/cetal_scopes/`. The package ships `py.typed`; keep all
  code type-annotated, as `pyright src apps` is required to pass.
- Downstream applications live in `apps/`, one directory per app with its own
  entry module, README and `tests/`. They are **not** packaged and must not be
  imported by the library; promote anything reusable into
  `src/cetal_scopes/`. Apps are linted and type-checked but excluded from
  coverage. See `apps/README.md`.
- Coverage is on by default via pytest `addopts` (`--cov=cetal_scopes
  --cov-report=term-missing`).
- Docstrings use the NumPy style; mkdocstrings is configured for it.
- Docs are auto-deployed to GitHub Pages from `main` via
  `.github/workflows/docs.yml`.
