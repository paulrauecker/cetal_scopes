# Architecture (agreed design)

Status of this document: **decided, not yet implemented.** It records the
architecture agreed with the team so future work does not have to re-litigate
it. Once code lands, keep this in sync with the source.

## Goal

A vendor-agnostic acquisition/analysis layer: each oscilloscope or digitizer
gets a driver, every driver produces the same `Capture` container, and all
downstream analysis consumes `Capture`s without knowing which instrument they
came from.

## Layering

```
Scope (ABC template)          driver base; one subclass per instrument
 ├─ SiglentSDS6204L
 └─ SpectrumM5i3367
        │  connect() / configure() / acquire()
        ▼
Capture                       acquisition result + persistence unit
 ├─ raw   (n_channels, n_samples)   ADC codes
 ├─ volts (n_channels, n_samples)   float64, volts
 ├─ t0, dt                         shared timebase (seconds)
 ├─ metadata: CaptureMetadata
 └─ channels: dict[str, Channel]
        │
        ▼
Channel                       per-channel row-view of the Capture
 └─ antenna: Antenna          physical detector identity/calibration
        │
        ▼
Shot                          runtime-only convenience aggregator of Captures
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
- **`Antenna`** — the physical detector/sensor attached to a channel
  (sensitivity, orientation, cable delay, calibration). Used by analysis to
  turn volts into physical units.
- **`Shot`** — groups several `Capture`s belonging to one experiment. It is an
  in-memory convenience only; it is not persisted.

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
<stem>.json        # CaptureMetadata + per-channel metadata + inline antennas
                   #   + sidecar filenames + channel order + format_version
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

Public I/O surface: `load_capture(path) -> Capture` and
`save_capture(capture, path) -> None`. The names stay `capture`, not `shot`.

## Scope interface

The template `Scope` class is an ABC with:

- `connect()` — open the instrument connection.
- `configure(settings)` — apply acquisition settings.
- `acquire() -> Capture` — run one acquisition and normalize the result.
- `close()` — release the connection.
- context-manager support (`__enter__` / `__exit__`).

The first concrete driver is **Siglent SDS6204L** (SCPI; raw socket port 5025
first, VISA fallback). The second is **Spectrum M5i.3367-x16**, built on
`spcm` / `spcm_core`.

## Dependencies

- Core runtime: `numpy`, `pydantic`.
- Vendor SDKs are hard dependencies but must be imported **lazily inside
  `scopes/`** so that `import cetal_scopes` and CI do not require hardware or a
  vendor driver to be present.
- `pyvisa` + `pyvisa-py` are declared and used by the Siglent driver's VISA
  fallback (USB / VXI-11); the raw-socket LAN path needs only the stdlib.
  `pyvisa` is imported lazily inside `_VisaTransport.open()`.
- `spcm` / `spcm-core` are still to be added for the Spectrum driver.
- `pyspcm` is **not** on PyPI and cannot be a pip dependency. The pip-installable
  low-level API is `spcm-core` (import `spcm_core`); the high-level API is
  `spcm` (which depends on `spcm-core`). Use these instead of `pyspcm`.

## Milestones

1. **Core container** — `Antenna`, `Channel`, `Capture`, `Shot`, pydantic
   metadata, `load_capture` / `save_capture`, the `Scope` ABC (no concrete
   driver yet), tests, docs. *(Capture/Channel + Scope ABC done; metadata,
   antenna and I/O pending.)*
2. **Siglent SDS6204L driver** producing a `Capture`. *(LAN raw socket + VISA
   fallback done; verify against hardware.)*
3. **Spectrum M5i.3367-x16 driver** over `spcm` / `spcm_core`.
4. Documentation finalization.

## Deliberately deferred

- Clock/external synchronization across scopes.
- Streaming / multi-shot acquisition.
- `Shot` serialization.
- Unequal-length or segmented channels.
