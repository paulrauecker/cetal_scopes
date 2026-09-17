# Spectrum M5i.3367-x16

2-channel, 12-bit PCIe digitizer (up to 10 GS/s) driven directly through its
register interface. The driver lives in `cetal_scopes.scopes.spectrum`
(`SpectrumM5i3367`); see `code.md` in this directory for the condensed vendor
manual it was built from.

## Connection

- Local device path, e.g. `/dev/spcm0` (the driver's default).
- Driven over `spcm_core` (the vendor's low-level `ctypes` bindings, PyPI
  package `spcm-core`, pulled in by the `spcm` dependency), **not** the
  high-level `spcm` package -- that package returns `pint.Quantity` values,
  out of step with this codebase's plain-float, SI-unit `Capture`/`Channel`
  containers. `spcm_core` is imported lazily inside `_RealCard`'s methods, so
  `import cetal_scopes` never needs the vendor driver installed.
- Bench card observed as `M5i.3367-x16`, serial `24123`.
- **The vendor driver library (`libspcm_linux.so`) has no `SONAME`** and is
  loaded by bare name with no path override, so it must be resolvable via the
  linker search path. Under the project's Nix devShell this is handled by
  `flake.nix` -- see its comments, which also record a real footgun: putting
  the *whole* system library directory on `LD_LIBRARY_PATH` also shadows
  Nix's own `libpython`, breaking `ctypes` itself. Only a narrow directory
  containing a symlink to just that one library is safe.

## Configuration model

Unlike an oscilloscope, this card has no front panel -- every setting is a
numeric register (`SPC_*`), not a SCPI string. `configure()` therefore only
accepts the shared physical (SI-unit) vocabulary documented on
`cetal_scopes.scopes.base.Scope`: `sample_rate` (Hz), `record_length`
(samples), `pretrigger` (samples or a record fraction), `range` (volts
full-scale), `offset` (volts), `coupling`, `impedance`, and `trigger`
(`source`/`level` in volts/`slope`).

`configure()` performs no I/O -- it only validates and stores settings.
Hardware setup order is load-bearing on this card (channel enable constrains
the achievable sample rate and memory; the trigger OR mask defaults to the
software trigger and must be cleared explicitly), so the full,
correctly-ordered register sequence is written fresh by `acquire()` /
`acquire_segments()` on every call, rather than incrementally as each
setting arrives.

`coupling` and `impedance` are fixed in hardware (DC, 50 ohm): the matching
value is accepted as a no-op, any other value raises `ValueError`, so shared
application code can set them on any driver without special-casing this
card.

## Acquisition modes

- `acquire() -> Capture` -- Standard Single. Raises `RuntimeError` if the
  card is configured for Multiple Recording (`configure({"segments": n})` /
  `set_segments(n)`).
- `acquire_segments() -> list[Capture]` -- Multiple Recording: one `Capture`
  per segment, matching `Capture`'s own definition of "one trigger, one
  timebase". `record_length` is the *per-segment* length in this mode.
- FIFO streaming (continuous acquisition longer than on-board memory) is not
  implemented; see `docs/architecture.md`'s deferred list.

Both raise `TimeoutError` with a message containing `"did not complete"` on
no trigger within the configured timeout, matching the convention every app
in `apps/` polls for.

## Sample rate and timing

The card only accepts `[base]/2**n` sample rates (base 10.0 / 8.0 / 6.4
GS/s); a requested rate is rounded to the nearest achievable divided clock
**by the card itself**, not by software, so the driver always reads
`SPC_SAMPLERATE` back after writing it and uses that value for `dt`.
Requesting `10e6` Hz, for example, is silently rounded to `10e9 / 1024 =
9.765625e6` Hz -- read back `Capture.dt` (`= 1 / actual_rate`), never assume
the requested rate.

`t0` is negative for a centered or fractional pretrigger, exactly as on the
Siglent driver: sample 0 is the oldest pretrigger sample, and the trigger
event sits at index `record_length - posttrigger`.

## Verified against real hardware (2026-09-15)

- Open/identify (`connect()`): product name, serial number.
- Single-channel and two-channel Standard Single acquisition, including
  per-channel input ranges (noise floor scaled with range, as expected).
- Sample-rate readback and the resulting `t0`/`dt` (confirmed against the
  achieved `10e9 / 1024` divided clock, not the nominal requested rate).
- Channel trigger configuration: with an open (unconnected) input and a
  configured level, the acquisition correctly timed out rather than
  spuriously triggering -- confirming the OR/AND trigger masks are cleared
  correctly before the channel trigger is armed (the vendor manual's own
  "single most common mistake": the OR mask defaults to the software
  trigger).
- Multiple Recording: 4 segments, consistent per-segment timing.

## Gotchas

- **The trigger OR mask defaults to the software trigger.** If it is not
  cleared, the acquisition free-runs immediately instead of waiting for the
  configured trigger. The driver always clears `SPC_TRIG_ORMASK` /
  `SPC_TRIG_ANDMASK` / `SPC_TRIG_CH_ORMASK0` / `SPC_TRIG_CH_ANDMASK0` before
  deciding between software and channel-trigger mode -- see
  `_apply_common_setup` in `spectrum.py` if modifying the trigger path.
- **`range`/`sample_rate`/`record_length` snap, they do not error.** A
  requested input range above ±2.5 V clamps to ±2.5 V (and will clip); a
  requested sample rate above the channel-count ceiling (10 GS/s one
  channel, 5 GS/s two) clamps down; `record_length` rounds up to the next
  32-sample step. Always read `Capture.metadata` / `Capture.dt` for what was
  actually used.
- **The pretrigger is on the 32-sample grid too, and snapping the record
  length is not enough.** Half of a 32-aligned record is only 32-aligned when
  the record is 64-aligned, so `record_length = 250000` (snapped to 250016)
  with `pretrigger = 0.5` gives 125008 -- off the grid, and the card answers
  `ERR_VALUE` (257) at `arm()`, not at `configure()`. `snap_pretrigger()`
  rounds it down, giving the spare samples to the posttrigger.
- **Trigger level is relative to the current range and offset, in volts on
  the driver's side but ADC codes on the wire.** `volts_to_code` raises
  `ValueError` naming the valid range if the requested level does not fit
  within `±range - offset`.
