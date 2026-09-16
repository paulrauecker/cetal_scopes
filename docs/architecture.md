# Architecture (agreed design)

Status of this document: **implemented.** The core containers (`Antenna`,
`Channel`, `Capture`, `Shot`), the pydantic `CaptureFile` / `ShotFile` schemas,
`load_capture` / `save_capture` / `load_shot` / `save_shot`, the staged
acquisition lifecycle, `cetal_scopes.acquisition`, and
`cetal_scopes.analysis` are all in the source. Keep this in sync as it moves.

## Goal

A vendor-agnostic acquisition/analysis layer: each oscilloscope or digitizer
gets a driver, every driver produces the same `Capture` container, and all
downstream analysis consumes `Capture`s without knowing which instrument they
came from.

## Layering

```
Scope (ABC template)          driver base; one subclass per instrument
 ├─ SiglentSDS6204L
 ├─ SpectrumM5i3367
 └─ DemoScope                 synthetic; runs with no hardware
        │  connect() / configure() / acquire()
        │  arm() / wait() / fetch()        staged, for a multi-instrument shot
        ▼
Capture                       acquisition result + persistence unit
 ├─ raw   (n_channels, n_samples)   ADC codes
 ├─ volts (n_channels, n_samples)   float64, volts
 ├─ t0, dt                         shared timebase (seconds)
 ├─ metadata: dict                 free-form provenance
 └─ channels: dict[str, Channel]
        │
        ▼
Channel                       per-channel row-view of the Capture
 ├─ antenna: Antenna          physical detector identity/calibration
 └─ unit: str                 unit of the samples (V, T, T/s, ...)
        │
        ▼
Shot                          aggregator of Captures with per-capture offsets
        │
        ▼
analysis code                 consumes Capture / Channel / Antenna
```

Responsibilities:

- **`Scope`** — abstract base class. Subclasses implement the vendor protocol
  and return a `Capture`. It knows nothing about file format or analysis.
- **`Capture`** — one instrument acquisition (one trigger, one timebase). It
  carries the arrays, timing, per-channel metadata, and is the unit that
  `load_capture` / `save_capture` read and write. This is the boundary between
  acquisition and analysis.
- **`Channel`** — a per-channel view onto a `Capture`'s arrays, plus its own
  metadata and an assigned `Antenna`. Kept separate so analysis can reason
  about a single detector without slicing arrays by hand.
- **`Antenna`** — the physical detector/sensor attached to a channel (kind,
  orientation, cable delay, calibration). Its frequency-dependent complex
  `TransferFunction` lets analysis turn volts into physical units, e.g.
  `analysis.b_field` integrating a B-dot's `V` to `B` in tesla.
- **`Shot`** — groups several `Capture`s belonging to one experiment, each under
  a label, together with a scalar per-capture time offset that maps them onto a
  common axis. `save_shot` / `load_shot` persist it as a directory of captures
  plus an index.
- **`cetal_scopes.acquisition`** — drives several drivers through one
  synchronized shot, holding an arm barrier so that no trigger is solicited
  until every instrument has armed. It returns the captures that arrived even
  when one instrument fails, together with per-instrument diagnostics.

## Data model decisions

- **Both raw and converted data are stored.** `Capture.raw` holds ADC codes in
  their native integer dtype; `Capture.volts` holds the same data converted to
  float64 volts. `raw` is optional so drivers that only expose volts still work.
- **`t0` and `dt` live on `Capture`** and are shared by all channels of the
  acquisition (one trigger, one timebase).
- **The time axis is derived, never stored**: `channel.time == t0 +
  arange(n_samples) * dt`.
- **Channels are keyed by name**: `capture.channels["CH1"]`. Insertion order is
  the array row order.
- **`Channel` objects are views**, not copies: a channel references a row of the
  `Capture` arrays so memory stays single-copy.
- **Antennas are persisted inline** with the capture so files are
  self-describing and analysis needs no external registry. (A registry-based
  override for retrospective re-calibration was considered and deferred.)

## Storage format

A capture is a JSON metadata file plus NumPy sidecars:

```
<stem>.json        # CaptureFile: timing + per-channel metadata + inline
                   #   antennas + sidecar filenames + channel order + version
<stem>.volts.npy   # float64, shape (n_channels, n_samples)
<stem>.raw.npy     # native code dtype, same shape (omitted when raw is None)
```

- Sidecars are resolved relative to the JSON path, so a capture directory can be
  moved.
- Load with `np.load(..., allow_pickle=False)`; validate shape, dtype, and
  `format_version`. Reject an unknown major version.
- Writes are atomic (temp file + `os.replace`).
- Assumes all channels share one sample count. Unequal-length or segmented
  channels are deferred.

A shot is a directory of those, plus an index:

```
<shot>/shot.json        # ShotFile: labels, offsets, reference, metadata
<shot>/<label>.json     # one capture per instrument, as above
<shot>/<label>.volts.npy
<shot>/<label>.raw.npy
```

The index is written last, so its presence implies every capture it references
is complete. Labels are mapped to filename-safe stems, since they come from
user configuration and from segmented acquisitions (`m5i[0]`); a collision is
an error rather than a silent overwrite.

Public I/O surface: `load_capture(path) -> Capture`,
`save_capture(capture, path) -> Path`, `load_shot(path) -> Shot` and
`save_shot(shot, path) -> Path`.

## Scope interface

The template `Scope` class is an ABC with:

- `connect()` — open the instrument connection.
- `configure(settings)` — apply acquisition settings.
- `acquire() -> Capture` — run one acquisition and normalize the result.
- `close()` — release the connection.
- context-manager support (`__enter__` / `__exit__`).

Alongside the one-shot `acquire()`, every driver exposes a **staged
lifecycle**, so several instruments can be armed before any of them is
triggered:

```
idle --arm()--> armed --wait()--> triggered --fetch()--> idle
```

- `arm()` — make the hardware ready for a trigger and return *without*
  blocking on it.
- `wait(timeout) -> bool` — `True` when complete, `False` on expiry. It
  **never raises `TimeoutError`**, and a `False` leaves the instrument armed so
  the call can be repeated. That re-enterability is the point: it is the
  primitive an application slices to stay responsive to an abort while waiting.
- `fetch() -> Capture` / `fetch_all() -> list[Capture]` — transfer the result.
- `abort()` — disarm and discard; safe when unarmed or disconnected, never
  raises.
- `force_trigger()` — trigger in software (a debugging path).
- `trigger_status() -> str | None` — the vendor's own trigger state, when it
  reports one.

Two class-level capability flags say what a driver can really do:
`supports_staged_acquisition` is `False` when the base class's emulation is in
use — the emulation runs the whole acquisition inside `wait()`, so such an
instrument cannot honestly join an arm barrier — and `supports_force_trigger`
says whether a software trigger exists. `connect`/`configure`/`acquire`/`close`
remain the only abstract methods, so existing drivers keep working unchanged.

Driver-specific notes: the SDS6204L has no stateless force command — force
trigger is a value of `:TRIGger:MODE` (`FTRIG`), so it clobbers the configured
sweep mode and the driver re-asserts it on the next `arm()`. The M5i's
`_apply_common_setup()` runs inside `arm()`; it cannot run later because it
opens with `M2CMD_CARD_RESET`, which would destroy an in-flight acquisition.

The first concrete driver is **Siglent SDS6204L** (SCPI; raw socket port 5025
first, VISA fallback). The second is **Spectrum M5i.3367-x16**
(`SpectrumM5i3367`), a register-based card driven directly over `spcm_core`
(the vendor's low-level `ctypes` bindings) rather than the high-level `spcm`
package, since the latter returns `pint.Quantity` values and this codebase's
`Capture`/`Channel` containers are plain-float SI-unit, not pint-aware.

In addition to each driver's own panel-native `configure()` keys (e.g.
Siglent's `timebase` in s/div), every driver also accepts a shared, physical,
SI-unit vocabulary (`sample_rate`, `record_length`, `pretrigger`, `range`,
`offset`, `coupling`, `impedance`, `trigger`), documented in full on the
`Scope` class docstring and implemented in
`cetal_scopes.scopes._settings`. The M5i implements it natively; the Siglent
driver accepts it additively, alongside (and interoperating with) its
existing panel keys -- mixing a physical key with the panel key it aliases in
one `configure()` call (e.g. `sample_rate` with `timebase`) raises
`ValueError`, since both spellings write the same instrument setting and
there is no honest way to prefer one.

The M5i also exposes `acquire_segments() -> list[Capture]` for Multiple
Recording (hardware-segmented acquisition, one `Capture` per segment); plain
`acquire()` covers Standard Single and raises if the card is configured for
segments.

## Dependencies

- Core runtime: `numpy`, `pydantic`.
- Vendor SDKs are hard dependencies but must be imported **lazily inside
  `scopes/`** so that `import cetal_scopes` and CI do not require hardware or a
  vendor driver to be present.
- `pyvisa` + `pyvisa-py` are declared and used by the Siglent driver's VISA
  fallback (USB / VXI-11); the raw-socket LAN path needs only the stdlib.
  `pyvisa` is imported lazily inside `_VisaTransport.open()`.
- `spcm` (which pulls in `spcm-core`) is declared for the Spectrum driver;
  `spcm_core` is imported lazily inside `_RealCard`'s methods in
  `scopes/spectrum.py`. `pyspcm` is **not** on PyPI and cannot be a pip
  dependency -- `spcm_core` is the pip-installable equivalent.
- The vendor driver library (`libspcm_linux.so`) has no `SONAME` and is
  loaded by bare name with no path override, so it must be on the linker
  search path. `flake.nix` handles this for the Nix devShell (see its
  comments for a real footgun found along the way: putting the *whole*
  system library directory on `LD_LIBRARY_PATH` also shadows Nix's
  `libpython`, breaking `ctypes` itself -- only a narrow directory
  containing just that one library is safe).

## Milestones

1. **Core container** — `Antenna`, `Channel`, `Capture`, `Shot`, pydantic
   metadata, `load_capture` / `save_capture`, the `Scope` ABC (no concrete
   driver yet), tests, docs. *(All done, including per-capture time offsets and
   the cross-correlation estimator in `cetal_scopes.analysis`.)*
2. **Siglent SDS6204L driver** producing a `Capture`. *(LAN raw socket + VISA
   fallback done; verify against hardware.)*
3. **Spectrum M5i.3367-x16 driver** over `spcm_core`. *(Done: Standard Single
   and Multiple Recording, hardware-verified including trigger configuration
   and the sample-rate readback path. FIFO streaming deferred, see below.)*
4. **Synchronized multi-instrument shots** — the staged `arm`/`wait`/`fetch`
   lifecycle, `cetal_scopes.acquisition`, `Shot` serialization, and the
   `apps/capture_studio` browser front end. *(Done.)*
5. Documentation finalization.

## Deliberately deferred

- Clock/external synchronization across scopes. The arm barrier in
  `cetal_scopes.acquisition` guarantees only that no trigger is solicited
  until every instrument has armed; it is not a shared clock, and the
  residual is reported as `ShotResult.arm_spread_s` rather than hidden.
- FIFO streaming acquisition (continuous, longer than on-board memory).
  Multiple Recording (segmented, one `Capture` per trigger) is implemented.
- Unequal-length or segmented channels within a single `Capture`.
