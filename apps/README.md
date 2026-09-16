# Applications

Downstream tools built on the `cetal_scopes` library live here. They are
**not** part of the shipped package: only `src/cetal_scopes/` is packaged and
type-checked against `py.typed`, so apps must not be imported by the library.

Guidelines:

- **No reusable code.** An app is a consumer of the library. If a helper turns
  out to be generally useful, promote it into `src/cetal_scopes/` instead.
- **Self-contained.** Each app is its own directory with a uniquely named entry
  module, a `README.md`, and its own `tests/` (excluded from library coverage).
- **Run with uv**, e.g. `uv run apps/realtime_viewer/viewer.py`.
- Apps that need extra dependencies should become uv workspace members with
  their own `pyproject.toml` rather than growing the library's dependency list.

Apps are linted and type-checked in CI, but not counted in library coverage.

## Current apps

- `realtime_viewer/` — live signal + FFT panes for any `Scope` driver.
- `bdot_probe/` — live `dB/dt` and integrated `B` for a B-dot probe/triad.
- `bdot_web/` — Dash/Plotly browser analysis for B-dot shots (a uv workspace
  member; needs `dash`/`plotly`, so run `uv sync --all-packages`).
- `sg8_sweep/` — SG8 generator frequency-response sweep against a scope channel.
- `rf_waterfall/` — live or recorded RF waterfall (spectrogram) of a scope channel.
- `capture_studio/` — browser front end for multi-instrument single-shot
  capture and analysis (a uv workspace member; needs `fastapi`/`uvicorn`).
