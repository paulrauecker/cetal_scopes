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
