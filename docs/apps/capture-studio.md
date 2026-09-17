# Capture studio

Multi-instrument single-shot capture and analysis in the browser: configure
every scope on the bench, arm them together, take one shot, and work on the
result.

```bash
uv sync --all-groups --all-packages
uv run apps/capture_studio/capture_studio.py --demo --instruments 3
```

Then open <http://127.0.0.1:8000>. Against real hardware, describe the bench in
a TOML inventory and pass `--config bench.toml`.

FastAPI serves the API and a WebSocket progress stream; the browser draws with
a vendored Plotly.js, so there is no node toolchain and nothing is fetched at
runtime. Full usage, the inventory format, and the panel reference are in
[`apps/capture_studio/README.md`](https://github.com/paulrauecker/cetal_scopes/tree/main/apps/capture_studio).

## Bench probes

Three scripts ship alongside the app for finding out what an instrument
actually does with the settings it is given — see
[Bench probes](../bench-probes.md) for the method and the measured results.

## What it is for

A shot watched by several instruments at once. The library pieces it builds on:

- `cetal_scopes.acquisition` (see [the API page](../api/acquisition.md)) arms every instrument
  and holds a barrier until all of them are ready before any trigger is
  solicited.
- [`save_shot`][cetal_scopes.storage.save_shot] /
  [`load_shot`][cetal_scopes.storage.load_shot] persist the result.
- [`align_shot`][cetal_scopes.analysis.alignment.align_shot] fits the
  per-capture time offsets; they can also be typed in by hand.
- `cetal_scopes.analysis` supplies the processing steps and every panel.

## Honest limits

- The arm barrier guarantees that no trigger is solicited until every
  instrument has armed. It is **not** clock synchronisation. The residual is
  reported as the shot's arm spread rather than hidden.
- An instrument that times out contributes no capture. It still holds the
  previous shot's waveform, so reading it would produce a well-formed record
  from the wrong event — which time alignment would happily fit an offset to.
- An auto-fit with a low correlation means the fit found nothing convincing.
  The UI flags it; treat it as "no answer", not as a small one.
