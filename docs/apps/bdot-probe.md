# B-dot probe viewer

`apps/bdot_probe/bdot_probe.py` is a downstream application (not part of the
library) for prototyping magnetic-field sensing with a B-dot probe. It acquires
from a scope and draws a live two-pane window: the **probe response** `dB/dt` on
top and the **integrated field** `B` plus the vector magnitude `|B|` below. Peak
values are latched across shots.

> A B-dot measures the field's **time derivative** (`V ∝ dB/dt`), so a
> stationary magnet reads zero. Move or rotate the magnet to induce a signal.

## Run

By default the app runs in **triggered single-shot** mode: it arms an edge
trigger, waits for the magnet pulse to cross `--trigger-level`, then displays
that record and **holds it on screen for analysis**. Each trigger is printed to
the console with its amplitude (the trigger channel's raw peak and the integrated
`|B|` peak); press space to re-arm for another shot, `r` to reset the latched
peaks, `q` to quit. Use a short timebase and a sensitive V/div.

```sh
uv run apps/bdot_probe/bdot_probe.py \
    --address 192.168.5.197 --channels C1 C2 C3 \
    --vdiv 0.005 --timebase 0.005 --trigger-level 0.005
```

Without hardware, from a synthetic source:

```sh
uv run apps/bdot_probe/bdot_probe.py --demo
```

Pass `--continuous` to re-arm immediately after each shot, or
`--trigger-mode AUTO` for the free-running behaviour. While waiting, the title
shows `armed - waiting for trigger`.

## Calibration

Each channel is calibrated with a **nominal flat sensitivity**
(`--sensitivity`, volts per tesla-per-second) using a placeholder
:class:`~cetal_scopes.antenna.TransferFunction`, then
:func:`cetal_scopes.analysis.b_field` integrates `dB/dt` to `B`. The displayed
tesla values are therefore proportional (arbitrary scale) until the probe's real
complex `TransferFunction` is measured. Integration is `1/f`, so the app
band-limits with `outside="zero"` and defaults `--fmin` to ten times the record's
bin spacing (`10·df`) to stop the lowest bins from dominating `B`; `--fmax`
defaults to Nyquist.

See `apps/bdot_probe/README.md` for the full option list.
