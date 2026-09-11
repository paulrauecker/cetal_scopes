# Real-time viewer

`apps/realtime_viewer/viewer.py` is a downstream application (not part of the
library) that continuously acquires from a scope and redraws a live two-pane
window: the **signal versus time** on top and its **amplitude spectrum (FFT)**
below.

## Run

Against an SDS6204L over LAN:

```sh
uv run apps/realtime_viewer/viewer.py --address 192.168.5.197 --channels C1 C2
```

Without hardware, from a synthetic source:

```sh
uv run apps/realtime_viewer/viewer.py --demo --remove-comb
```

Each frame is a full :meth:`~cetal_scopes.scopes.base.Scope.acquire` in `AUTO`
sweep mode, with :func:`cetal_scopes.analysis.fft` applied per channel. By
default the scope is stopped for each fetch (one coherent frame, faster);
`--stream` keeps it running so the front panel does not stutter between `Auto`
and `Stop` (a little slower per frame). With `--remove-comb` each frame first
runs :func:`cetal_scopes.analysis.remove_adc_comb`; as documented there, a real
tone exactly on a comb line is erased along with the artifact.

## Display backend

matplotlib must use an interactive backend or no window opens (it falls back to
Agg). The Nix dev shell provides Tk on `PYTHONPATH`, so `TkAgg` is chosen
automatically. If the app warns that it is using a non-interactive backend,
re-enter the dev shell (`direnv reload`) or set `MPLBACKEND` to an interactive
backend.

See `apps/realtime_viewer/README.md` for the full option list.
