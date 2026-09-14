# B-dot web app (Dash + Plotly)

Interactive analysis of B-dot shots in the browser. It arms a single-shot edge
trigger on the scope, fetches one record, calibrates it to `dB/dt` and `B`, and
serves the traces as an interactive Plotly figure (hover readouts, zoom, pan,
legend toggles). The acquisition loop and figure building live in `engine.py`;
`bdot_web.py` holds the Dash layout and callbacks.

This app needs `dash` and `plotly`, so it is a **uv workspace member** with its
own `pyproject.toml` rather than a library dependency. Sync the whole workspace:

```bash
uv sync --all-groups --all-packages
```

## Run

```bash
# Against an SDS6204L over LAN
uv run apps/bdot_web/bdot_web.py --address 192.168.5.197 --channels C1 C2 C3

# Without hardware
uv run apps/bdot_web/bdot_web.py --demo
```

Then open <http://127.0.0.1:8050>.

## Using it

1. Check the form values (address, channels, sensitivity, trigger level, ...).
2. **Apply / arm** connects and configures the trigger. If the requested trigger
   level was clamped to the channel's vertical range (about `±4.5 × vdiv`), the
   status line and log show a `WARNING` — coarsen `vdiv` or add an offset.
3. **Capture one shot** arms and fetches a single triggered record; the status
   line reads `armed - waiting for trigger` until the level is crossed, then
   shows the shot's `|B|` peak.
4. Tick **auto re-arm** to repeat triggered shots on the interval.
5. Hover/zoom/pan the figure; click legend entries to toggle traces.
6. **Clear log** clears the trigger log; **Disconnect** closes the scope.

A shot is blocked on the server for up to the trigger timeout (`--trigger-timeout`
in the engine config, default 5 s), so a capture request can take a few seconds.

## Calibration caveat

As in the matplotlib app, `sensitivity` is a nominal flat `V/(T/s)` placeholder
and the displayed tesla values are proportional until replaced with the probe's
measured `TransferFunction`. Integration is `1/f`, so the field is band-limited:
`fmin` defaults to ten times the record's bin spacing (`10·df`) and `fmax` to
Nyquist, which stops the lowest bins from dominating `B`. Set `fmin` to the
probe's real low-frequency cutoff once known.

## Options

```
--host ADDR        bind address (default: 127.0.0.1)
--port N           bind port (default: 8050)
--address IP       scope IP for the raw socket
--channels C1 C2 C3
--sensitivity S    nominal flat sensitivity in V per (T/s) (default: 1)
--demo             start with the synthetic source
```
