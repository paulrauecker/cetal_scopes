# Capture studio

Multi-instrument single-shot capture and analysis in the browser. Configure
every scope on the bench, arm them **together**, take one shot, and work on the
result: flexible pane layouts, an FFT panel, manual and automatic time
alignment, a processing pipeline, and measurements.

FastAPI + Plotly.js. Plotly is vendored in `static/`, so there is no node
toolchain and nothing is fetched at runtime.

```bash
uv sync --all-groups --all-packages    # this app is a workspace member
```

## Run

```bash
# Synthetic instruments -- no hardware needed
uv run apps/capture_studio/capture_studio.py --demo --instruments 3

# A real bench
uv run apps/capture_studio/capture_studio.py --config bench.toml
```

Then open <http://127.0.0.1:8000>.

## The inventory

A run is defined by a TOML file rather than by whatever was typed into a form,
so a bench setup is reproducible and reviewable. The UI edits the same
structure and **Save** writes it back.

```toml
poll_interval = 0.2       # how finely the trigger wait is sliced (abort latency)
default_timeout = 10.0
arm_timeout = 5.0

[[instrument]]
label = "siglent"
driver = "siglent_sds6204l"
address = "192.168.5.197"
channels = ["C1", "C2", "C3"]
timeout = 10.0

[instrument.settings]
sample_rate = 2.0e9       # the SDS6204L is trusted to 2 GHz, not to Nyquist
record_length = 100000
pretrigger = 0.5
range = 1.0
impedance = 50.0

[instrument.settings.trigger]
source = "C1"             # or "EX" / "EX5" for the external input
level = 0.2
slope = "RISing"

[[instrument]]
label = "m5i"
driver = "spectrum_m5i3367"
address = "/dev/spcm0"
channels = ["CH0", "CH1"]

[instrument.settings.trigger]
source = "EXT"            # the card's Ext0 "Trig In"
level = 1.5
slope = "RISing"
```

`settings` goes straight to the driver's `configure()`, so it takes the shared
physical vocabulary (`sample_rate`, `record_length`, `pretrigger`, `range`,
`offset`, `coupling`, `impedance`, `trigger`) plus any panel-native keys that
driver accepts. `options` goes to its constructor. `enabled = false` keeps an
instrument in the file but out of the run.

Drivers: `siglent_sds6204l`, `spectrum_m5i3367`, `demo`.

## How a shot works

1. **Connect** opens and configures every enabled instrument.
2. **Capture shot** arms them all and waits at a barrier until *every* one has
   returned from `arm()`. Only then is any trigger solicited. That is what
   stops a fast scope catching a trigger while a slow one is still writing
   registers.
3. The residual is reported, not hidden: **arm spread** is the window between
   the first and last instrument arming. It is not clock skew — it bounds how
   much of the shot's jitter comes from the arming itself.
4. Each instrument's record is fetched as soon as *it* triggers.

**Direct trigger** fires the shot in software instead of waiting for the
experiment. It is a debugging path, and it happens strictly after the barrier.

If one instrument never triggers, the shot still comes back with the captures
that did arrive, marked incomplete, with the failure named. The timed-out
instrument contributes **no** capture: a scope that never triggered still holds
the *previous* shot's waveform, and reading it would yield a well-formed record
silently from the wrong event.

## Timing

Each capture has its own clock, so they need putting on a common axis.

- **Manual**: type a per-capture offset in nanoseconds. Traces move live.
- **Auto-fit**: cross-correlates every capture against the reference. The fit
  is inspectable rather than magic — each result shows its correlation and
  whether it found an inversion, and a weak fit (`|r| < 0.5`) is flagged. Treat
  a weak fit as "no answer", not as a small one.

## Panels

- **Traces** — three layouts: everything overlaid, one row per capture, or one
  row per channel. Stacked rows share the x-axis, so zoom moves them together.
  Long records are drawn through a min/max envelope, so a one-sample spike
  survives decimation instead of being aliased away.
- **Spectrum** — FFT per channel, amplitude or PSD, log or linear, selectable
  window, peak annotated.
- **Two-channel** — coherence, transfer function (gain, phase, and the
  coherence beneath them, because a transfer function has a value at every
  frequency whether or not the channels are related there), XY, a spectrogram,
  and a **field vector**: a phase-correct 3-D arrow from three channels of one
  capture at a chosen frequency. It uses complex amplitudes, so a component
  pointing the other way is drawn pointing the other way — plotting three FFT
  magnitudes instead can only ever produce an arrow in the `+++` octant.
- **Measurements** — RMS, peak-to-peak, amplitude, rise/fall, FWHM, overshoot.
  Levels come from the waveform's histogram modes rather than min/max, so
  ringing does not read as a shorter rise time. A measurement the waveform does
  not support shows as `—`, never as a fabricated number.
- **Processing** — an ordered pipeline of `cetal_scopes.analysis` steps applied
  before display. A step that cannot run on a given channel (`b_field` without
  an antenna, say) is skipped and reported rather than failing the view, so one
  pipeline can serve a whole shot. **raw** bypasses it.

## Export

**Export CSV** interpolates every channel onto one common grid, since a CSV has
a single time column and the captures do not share a sample rate; times outside
a capture's own span are left blank rather than filled with edge values.
**Export NPZ** keeps every channel on its own axis, so nothing is resampled and
what you get is what was recorded.

There is no PNG export button: Plotly's own toolbar already saves the figure
exactly as drawn, so adding a headless renderer to reproduce it would buy
nothing.

## Shots on disk

**Save** writes a shot directory: one capture per instrument in the ordinary
`<label>.json` + `.npy` format, plus `shot.json` holding the labels, offsets,
reference, the per-instrument diagnostics, and the processing pipeline. The
pipeline travels with the shot, which is what makes it reproducible: the
recorded samples plus the exact steps applied to them. **Load** restores all of
it.

## Options

```
--config PATH        TOML inventory
--demo               synthetic instruments instead of hardware
--instruments N      how many, with --demo (default: 2)
--channels-each N    channels per synthetic instrument (default: 2)
--host ADDR          bind address (default: 127.0.0.1)
--port N             bind port (default: 8000)
--log-level LEVEL    uvicorn log level (default: warning)
```

## Notes

- One session owns the hardware for the whole process, behind a single lock, so
  two browser tabs cannot arm the same scopes at once.
- Abort takes effect at the next wait slice (`poll_interval`) plus any driver
  call already in flight — a full 10 M-point Siglent transfer is about a
  second, so "aborting…" is not instant.
- An instrument whose driver cannot really be staged is still run, but is
  flagged `late-armed`: its acquisition does not start until the wait phase, so
  it is not truly part of the barrier.
