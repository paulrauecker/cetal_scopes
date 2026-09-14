# B-dot probe viewer

Live display of a magnetic-field (B-dot) probe. It acquires from a scope, applies
a **nominal flat sensitivity** to each channel, and draws two panes: the probe
response `dB/dt` on top and the integrated field `B` (plus the vector magnitude
`|B|`) below. Peak values are latched so a moved magnet is easy to see.

> A B-dot measures the field's **time derivative** (`V ∝ dB/dt`), so a
> stationary magnet reads zero. Move or rotate the magnet to induce a signal.

## Run

By default the app is in **triggered single-shot** mode: it arms an edge trigger
on the first channel, waits for the magnet pulse to cross the trigger level,
fetches that one record and **holds it on screen for analysis**. Every trigger is
printed to the console with the trigger channel's raw peak and the integrated
`|B|` peak, e.g.
`[17:30:31] trigger: captured shot 1  C1 peak=2.4e-03 V  |B|peak=6.1e-07 T`.
Press **space** (or `n`) to re-arm for another shot, `r` to reset the latched
peaks, `q` to quit.

```bash
# B-dot triad on C1-C3 of an SDS6204L; trigger on C1 above 5 mV
uv run apps/bdot_probe/bdot_probe.py \
    --address 192.168.5.197 --channels C1 C2 C3 \
    --vdiv 0.005 --timebase 0.005 --trigger-level 0.005

# Re-arm automatically after each shot instead of holding
uv run apps/bdot_probe/bdot_probe.py --continuous

# No hardware: synthetic derivative-of-Gaussian pulses
uv run apps/bdot_probe/bdot_probe.py --demo

# Free-running instead of triggered
uv run apps/bdot_probe/bdot_probe.py --trigger-mode AUTO
```

While waiting for a trigger the window shows `armed - waiting for trigger`; after
a shot it shows `shot - press space to arm` (or `done - q to quit` once
`--frames` is reached, which keeps holding the last shot open).

## Options

```
--address IP           scope IP for the raw socket (default: 192.168.5.193)
--channels C1 C2 C3    channels to read (default: C1 C2 C3)
--labels X Y Z         orientation label per channel (default: X Y Z)
--sensitivity S        nominal flat sensitivity in V per (T/s) (default: 1)
--fmin HZ              calibration low band edge (default: 10x the DFT bin spacing)
--fmax HZ              calibration high band edge (default: Nyquist)
--interval SECONDS     seconds between frames (default: 0.2)
--frames N             stop after N captured shots (hold keeps the last open)
--continuous           re-arm after each shot instead of holding for analysis
--timebase S           horizontal scale in s/div (use a short one, e.g. 0.005)
--vdiv V               vertical scale in V/div, snapped to the 1-2-5 ladder
--impedance {1M,50}    input impedance (default: 1M)
--max-points N         points drawn per trace (default: 20000)
--trigger-mode MODE    SINGle, AUTO or NORMal (default: SINGle)
--trigger-channel CH   edge-trigger source (default: first channel)
--trigger-level V      edge-trigger level (default: 0)
--trigger-slope EDGE   RISing or FALLing (default: RISing)
--trigger-timeout S    wait per poll before re-arming (default: 5)
--demo                 use the synthetic source
```

## Tips

- Use the **most sensitive V/div that does not clip** (down to 0.5 mV/div at
  1 MΩ). The ADC comb is fixed in codes, so a more sensitive scale shrinks its
  relative size and resolves small probe signals.
- Use a **short timebase** (e.g. `--timebase 0.005`, 5 ms/div). At a
  seconds-long timebase each record is large and slow to fetch, and the
  integrated noise is dominated by drift.
- The trigger fires on the *probe* signal, so set `--trigger-level` above the
  noise floor; otherwise it triggers on noise.
- The scope **clamps the trigger level to the source channel's vertical range**
  (about `±4.5 × V/div`). At a very sensitive `--vdiv` the whole range can lie
  inside the noise, so no `--trigger-level` will clear it; use a coarser
  `--vdiv` (or a vertical offset). The app reads the level back and prints a
  `warning:` when it was clamped.

## Calibration caveat

`--sensitivity` is a placeholder: it assumes the same real gain at every
frequency and no phase. The displayed tesla values are therefore proportional
(arbitrary scale) until you replace it with the probe's measured
`TransferFunction` — e.g. via the library's `Antenna`/`TransferFunction` and
`cetal_scopes.analysis.b_field`.

Integration is `1/f`, so without a low band edge the lowest DFT bins dominate the
reconstructed `B` and it drifts as a slow, nearly-linear wander. The app
band-limits with `outside="zero"` and defaults `--fmin` to ten times the record's
bin spacing (`df = 1 / (n·dt)`) to keep that in check; set `--fmin` to the
probe's real low-frequency cutoff once known. Calibrated code should use the
real band and let `b_field` raise when the signal falls outside it.
