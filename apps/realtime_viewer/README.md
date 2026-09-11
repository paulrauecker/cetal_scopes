# Real-time viewer

Live two-pane matplotlib window: the acquired **signal versus time** on top and
its **amplitude spectrum (FFT)** below, refreshed continuously.

## Run

Against an SDS6204L over LAN:

```sh
uv run apps/realtime_viewer/viewer.py --address 192.168.5.197 --channels C1 C2
```

Without hardware, from a synthetic source:

```sh
uv run apps/realtime_viewer/viewer.py --demo --remove-comb
```

The scope is driven in `AUTO` sweep mode so it free-runs without a trigger, and
every pane update applies `cetal_scopes.analysis.fft` per channel.

## Display backend

matplotlib needs an interactive backend, otherwise it falls back to Agg and no
window opens. The Nix dev shell puts Tk on `PYTHONPATH`, so `TkAgg` is selected
automatically:

```
$ uv run apps/realtime_viewer/viewer.py --demo
```

If you see `warning: matplotlib is using the non-interactive 'agg' backend`,
you are outside the dev shell or lack a GUI toolkit. Re-enter the shell
(`direnv reload`) or set `MPLBACKEND` to an interactive backend.

## Options

```text
--address ADDRESS        scope IP for the raw socket
--channels C1 C2 ...     channels to draw (default: C1)
--interval SECONDS       seconds between frames (default: 0.2)
--frames N               stop after N frames; default runs until closed
--window NAME            FFT window (default: hann; run --help for the list)
--frequency-max HZ       upper FFT x-limit
--linear-frequency       linear instead of logarithmic frequency axis
--linear-y               linear instead of logarithmic FFT amplitude axis
--remove-comb            subtract the ADC comb before the FFT
--comb-period N          comb period in samples (default: 256)
--timebase S_PER_DIV     horizontal scale in seconds/division
--vdiv V|auto            vertical scale in V/div, or 'auto' to range once
--impedance {1M,50}      input impedance (default: 1M)
--trigger-mode MODE      SINGle, AUTO or NORMal (default: AUTO)
--sample-width WIDTH     BYTE or WORD (default: WORD)
--stream                 leave the scope running between frames (default: stop)
--demo                   use the synthetic source
```

## Notes

- By default the scope is stopped for every fetch (faster, one coherent frame
  per update). `--stream` keeps it running so the front panel does not flicker
  between `Auto` and `Stop`; that costs roughly 20% per frame because the
  waveform readout is slower while the scope is acquiring/displaying.
- Closing the window ends the app.
- `--window` accepts the string FFT windows supported by
  `scipy.signal.get_window`: `boxcar`, `rect`, `rectangular`, `triang`,
  `blackman`, `hamming`, `hann`, `bartlett`, `flattop`, `parzen`, `bohman`,
  `blackmanharris`, `nuttall`, `barthann`, `cosine`, `exponential`, `tukey`,
  `lanczos`. Parameterised windows (`kaiser`, `gaussian`, ...) are not
  supported.
- `--vdiv` accepts a number or `auto`. The scope snaps numbers to its 1-2-5
  ladder: **0.5 mV/div - 10 V/div at 1 MOhm**, **0.5 mV/div - 1 V/div at
  50 Ohm**. `auto` takes one coarse capture, then picks the most sensitive step
  that keeps each channel's peak within ~4 divisions. A good manual default is
  **1 V/div**; for small signals the most sensitive non-clipping step also
  shrinks the fixed-code ADC comb relative to the signal.
- `--impedance` sets every channel's input impedance, defaulting to **1M**
  (`ONEMeg`, the safe general-purpose mode). `--impedance 50` switches to 50 Ohm
  for RF work; this also caps V/div at 1 V/div. 50 Ohm is fragile above its
  rating, so check the input level first. The library driver leaves impedance
  untouched unless asked.
- Log frequency drops the DC bin; amplitudes are floored at `1e-9 V` so the log
  axis stays finite.
- `--remove-comb` calls `cetal_scopes.analysis.remove_adc_comb` on each frame.
  As documented there, a real tone exactly on a comb line is erased along with
  the artifact.
