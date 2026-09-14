# RF waterfall

A classical RF waterfall (spectrogram): frequency on the x-axis, sweep index on
the y-axis, color = amplitude. Repeatedly acquires one channel from a scope,
computes its spectrum, and stacks spectra over time.

## Run

Three modes, selected with `--mode`:

```bash
# Live scrolling waterfall (default mode) against an SDS6204L over LAN
uv run apps/rf_waterfall/rf_waterfall.py --address 192.168.5.197 --channel C1

# Fixed recording: 200 SEPARATE triggered sweeps, saved to disk
uv run apps/rf_waterfall/rf_waterfall.py --mode record --sweeps 200 \
    --trigger-level 0.01 --output-plot waterfall.png --output-csv waterfall.csv

# One single-shot transient (e.g. an EMP event): ONE triggered record, sliced
# into overlapping 20us segments (a short-time Fourier transform)
uv run apps/rf_waterfall/rf_waterfall.py --mode transient \
    --timebase 0.001 --trigger-level 0.05 --stft-window 2e-5 \
    --output-plot emp.png

# No hardware: synthetic chirping tone
uv run apps/rf_waterfall/rf_waterfall.py --demo
uv run apps/rf_waterfall/rf_waterfall.py --demo --mode record --sweeps 100 --no-show
uv run apps/rf_waterfall/rf_waterfall.py --demo --mode transient --stft-window 1e-6 --no-show
```

In `record` mode with `--trigger-mode SINGle` or `NORMal` (the default is
`SINGle`), each of the `--sweeps` records waits for the edge trigger to fire;
every captured sweep is printed to the console with its peak frequency/amplitude.

`transient` mode is for a single event that only happens once and can't be
repeat-triggered (a spark, an EMP pulse): it waits for **one** trigger, then
slices that one record in time and FFTs each slice, so the resulting waterfall
shows how the event's own spectral content evolved during the pulse — unlike
`record`, which stacks independent trigger events. Size `--timebase` (and any
scope pretrigger delay) so the single record spans the whole event; size
`--stft-window` (seconds) to trade time resolution for frequency resolution —
a shorter window resolves faster spectral changes but with coarser frequency
bins. `--stft-hop` defaults to half the window (50% overlap).

## Options

```
--address IP             scope IP for the raw socket (default: 192.168.5.193)
--channel CH              channel to display (default: C1)
--mode {dynamic,record,transient}
                          dynamic: live scrolling waterfall
                          record: N separate triggered sweeps
                          transient: one triggered record, sliced into an STFT
--window NAME             FFT window (default: hann)
--detrend MODE            constant, linear, or none (default: constant)
--remove-comb             subtract the ADC comb before FFT
--comb-period N           comb period in samples (default: 256)
--timebase S              horizontal scale in s/div
--vdiv V                  vertical scale in V/div
--impedance {1M,50}       input impedance (default: 1M)
--depth N                 dynamic mode: rolling rows kept (default: 200)
--interval SECONDS        dynamic mode: seconds between frames (default: 0.2)
--frames N                dynamic mode: stop after N frames
--sweeps N                record mode: number of sweeps to capture (required)
--stft-window S           transient mode: STFT segment length in seconds (required)
--stft-hop S              transient mode: STFT hop in seconds (default: half the window)
--trigger-mode MODE       record/transient mode: SINGle, AUTO or NORMal (default: SINGle)
--trigger-channel CH      edge-trigger source (default: --channel)
--trigger-level V         edge-trigger level (default: 0)
--trigger-slope EDGE      RISing or FALLing (default: RISing)
--trigger-timeout S       wait per poll before repolling (default: 5)
--db / --no-db            show amplitude in dB vs. linear volts (default: dB)
--db-floor DB             dB floor (default: -120)
--cmap NAME               matplotlib colormap (default: viridis)
--freq-max HZ             upper frequency limit shown
--output-csv PATH         write sweep data to CSV
--output-plot PATH        save the plot to a PNG
--no-show                 skip opening an interactive plot window
--demo                    use the synthetic source
```

## Tips

- **ADC comb**: the SDS6204L's 16-bit acquisition path carries a deterministic
  comb (period 256 samples, spurs at multiples of `fs/256`). If horizontal
  stripes show up in the waterfall, especially at a coarse `--vdiv` or a long
  capture window, pass `--remove-comb`.
- **dB floor / colormap**: `--db-floor` sets both the displayed floor and the
  reference level the log is clamped against; lower it to reveal weaker
  signals, at the cost of a noisier-looking floor. `viridis` (the default)
  reads well in both light and dark terminals; try `magma` or `inferno` for
  a punchier peak.
- **Trigger clamp**: the scope clamps the edge-trigger level to about
  `±4.5 × V/div` of the trigger source channel. The app reads the level back
  after configuring it and prints a `warning:` when it was clamped — see
  `apps/bdot_probe/README.md` for the same caveat in more detail.
