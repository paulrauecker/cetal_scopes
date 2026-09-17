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

### Behind a reverse proxy

`--root-path` serves the app under a URL prefix that the proxy passes through
untouched, as Open OnDemand's node proxy does:

```bash
uv run apps/capture_studio/capture_studio.py --demo \
    --host 0.0.0.0 --root-path /node/pc-oscilloscope/8000
```

The prefix is stripped from incoming paths and baked into the page's `<base>`,
so the API, the static files and the WebSocket all follow it. Unprefixed paths
keep working, so the host itself can still reach the app on the bind address.
There is no authentication in the app -- put it behind one.

## The inventory

A run is defined by a TOML file rather than by whatever was typed into a form,
so a bench setup is reproducible and reviewable. The **Instruments** panel
edits that same structure: sample rate, record length, pretrigger, range,
offset, coupling, impedance, the trigger and a per-channel **V/div** get their
own fields, and anything else the driver accepts -- panel-native keys like
Siglent's `timebase`, or the per-channel mapping form of `range` -- stays in
the JSON boxes beside them, rather than being flattened into a single number it
is not.

- **Apply** hands the setup to the session. It disconnects the instruments,
  since which instruments exist may have changed; the next **Capture shot**
  reconnects and reconfigures them.
- **Save to file** writes it back to the inventory TOML, so what was tuned in
  the browser survives the session.
- **Demo** fills the editor with synthetic instruments, and **Reload** throws
  away edits and re-reads what the session holds. Neither applies anything.

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

### V/div, or `range`

The same vertical setting has two spellings, and which you get depends on the
driver:

- `range` is the shared, SI one: volts **full-scale**, so the channel spans
  `±range`. Every driver takes it.
- `vertical.<channel>.scale` is the Siglent's panel one: volts **per
  division**. With its 8 divisions, `range = 4 × V/div` — `range = 1.0` is
  `0.25 V/div`, and the instrument snaps up its own 1-2-5 ladder from there.
  `vertical` also carries that channel's `probe`, `bandwidth_limit`, `offset`,
  `coupling` and `impedance`; the editor writes `scale` and leaves the rest of
  what the file holds alone.

Because both write the same setting, the driver **rejects being given both for
one channel** — there is no honest precedence rule between them. So the editor
offers one or the other: typing a V/div clears that instrument's `range`, and
typing a `range` clears the V/div fields. A hand-edited TOML that sets both
is accepted by the inventory and fails at **Connect**, where `configure()` runs.

Drivers with no panel vertical vocabulary (the M5i) show no V/div field and
take `range` alone.

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

Sample rates do not have to match, and captures are never resampled onto each
other to be drawn. Each channel carries its own `t0` and `dt`, a capture's
offset shifts its `t0`, and every time-domain figure is drawn on that absolute
axis -- so two instruments digitizing one event put it at one time on the plot,
to within a sample of the coarser of the two. Auto-fit measures the lag on the
finer of the two sample intervals and adds back the difference in recorded
origins, which is the pretrigger mismatch between two instruments set up
differently.

- **Manual**: type a per-capture offset in nanoseconds. Traces move live.
- **Auto-fit**: cross-correlates every capture against the reference. The fit
  is inspectable rather than magic — each result shows its correlation and
  whether it found an inversion, and a weak fit (`|r| < 0.5`) is flagged. Treat
  a weak fit as "no answer", not as a small one.

## Panels

- **Instruments** — the inventory editor described above: what each instrument
  is told before a shot.
- **Traces** — three layouts: everything overlaid, one row per capture, or one
  row per channel. Stacked rows share the x-axis, so zoom moves them together.
  Long records are drawn through a min/max envelope, so a one-sample spike
  survives decimation instead of being aliased away.
  **Zooming re-fetches.** The visible span is sent back to the server and the
  drawing budget is spent on *that* span, so zooming in recovers real samples
  rather than magnifying the ones already sent — down to every sample once the
  window holds fewer than the budget. The note beside **Reset zoom** says which
  you are looking at (`every sample drawn (full rate)` or `262144 samples →
  4000 drawn (min/max envelope)`). The time unit is fixed by the whole record
  and does not change as you zoom, because the browser converts axis
  coordinates back to seconds with it.
  The **Channels** row toggles which channels the traces and the spectrum draw;
  it is a display filter only, so processing, measurements and export still see
  the whole shot. Channels of a later shot arrive visible.
- **Spectrum** — FFT per channel, amplitude or PSD, log or linear, selectable
  window, peak annotated. Follows the Traces panel's channel toggles.
  **Detail** sets the points drawn per trace (see *Drawing cost* below); the
  annotated peak is measured on the full spectrum, before any binning.
  **f min** / **f max** bound the band drawn, in Hz — blank means DC and the
  Nyquist frequency of the fastest capture. The limits are applied *before*
  the drawing budget, so narrowing to a band spends the whole budget inside
  it: zooming in buys resolution rather than showing a binned slice of the
  full span.
  **dB** shows the spectrum in dB relative to one unit of whatever the
  channels are in — `dB re 1 V` for volts, and the axis says so. A PSD is
  already a power quantity, so it converts at `10 log10` where an amplitude
  converts at `20 log10`. A dB axis is already logarithmic, so **log y** does
  not apply and is greyed out. A dead channel (all zeros) is floored at
  -400 dB rather than running to negative infinity and dragging the shared
  autoscaled axis down with it.
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

- **Full fidelity is a window away, not a setting.** Nothing the browser is
  given is ever more than the budget, so the answer to "am I seeing every
  sample" is always "in this window, yes or no", and the Traces panel says
  which. The recorded record is never modified: measurements, the FFT,
  alignment and both exports always run on all of it.
- **Drawing cost.** A figure is binned to a drawing budget before it is sent:
  time traces through a min/max envelope, spectra through the same envelope on
  geometric bins when the axis is logarithmic, so a narrow spur keeps its full
  height and the noise floor keeps its width. Nothing on a 1000-pixel axis can
  show more, and the alternative is real: a full-rate spectrum is half the
  record *per channel*, which on a bench-sized shot was an 18 MB response the
  browser then had to parse and lay out. Every measurement, fit and export
  still runs on the whole record.
- One session owns the hardware for the whole process, behind a single lock, so
  two browser tabs cannot arm the same scopes at once.
- Abort takes effect at the next wait slice (`poll_interval`) plus any driver
  call already in flight — a full 10 M-point Siglent transfer is about a
  second, so "aborting…" is not instant.
- An instrument whose driver cannot really be staged is still run, but is
  flagged `late-armed`: its acquisition does not start until the wait phase, so
  it is not truly part of the barrier.
