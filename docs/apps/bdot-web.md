# B-dot web app (Dash + Plotly)

`apps/bdot_web/` is a downstream application (not part of the library) that
brings the B-dot workflow into the browser: it arms a single-shot trigger on the
scope, fetches one record, calibrates it to `dB/dt` and `B`, and serves the
traces as an **interactive Plotly figure** (hover readouts, zoom, pan, and
legend toggles). It complements the Tk
[B-dot probe viewer](bdot-probe.md); the acquisition loop and figure building
are in `engine.py`, and the Dash layout/callbacks in `bdot_web.py`.

Because it needs `dash` and `plotly`, it is a **uv workspace member** with its
own `pyproject.toml`; sync the whole workspace first:

```sh
uv sync --all-groups --all-packages
```

## Run

```sh
# Against an SDS6204L over LAN
uv run apps/bdot_web/bdot_web.py --address 192.168.5.197 --channels C1 C2 C3

# Without hardware
uv run apps/bdot_web/bdot_web.py --demo
```

Then open <http://127.0.0.1:8050> and use **Apply / arm** followed by
**Capture one shot**; tick **auto re-arm** to repeat. The status line shows
`armed - waiting for trigger` until the level is crossed, then the shot's `|B|`
peak.

## Calibration

As with the Tk viewer, `sensitivity` is a nominal flat `V/(T/s)` placeholder, so
the tesla values are proportional until a real `TransferFunction` is supplied.
The field is band-limited: `fmin` defaults to `10·df` and `fmax` to Nyquist, so
the `1/f` integration does not let the lowest bins dominate `B`.

See `apps/bdot_web/README.md` for the full option list and usage notes.
