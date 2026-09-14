# Getting Started

## Installation

```bash
uv add "cetal_scopes @ git+https://github.com/paulrauecker/cetal_scopes.git"
```

Or for local/editable development:

```bash
uv pip install -e /path/to/cetal_scopes
```

## Basic usage

```python
from cetal_scopes import load_capture, save_capture

# Load a normalized capture
capture = load_capture("example.json")

# Free-form provenance (instrument, timestamp, notes)
print(capture.metadata)

# Access waveform data: (n_channels, n_samples)
print(capture.volts.shape)
print(capture["C1"].volts.shape)

# The sensor attached to a channel, if recorded
print(capture["C1"].antenna)

# Save back out
save_capture(capture, "example_copy.json")
```

## Attaching a sensor

An `Antenna` records which detector is on a channel and how its volts map to a
physical field. The mapping is a frequency-dependent complex `TransferFunction`
(e.g. a B-dot probe calibrated against a Helmholtz coil):

```python
import numpy as np
from cetal_scopes import Antenna, Capture, TransferFunction, save_capture

transfer = TransferFunction(
    freq=np.array([1.0e6, 1.0e7, 1.0e8]),   # Hz
    gain=np.array([1.0, 2.0 + 1.0j, 3.0 - 1.0j]),  # V/(T/s)
    unit="V/(T/s)",
)
b_dot_x = Antenna(
    name="Bdot-X",
    kind="b-dot",
    axis=(1.0, 0.0, 0.0),
    transfer_function=transfer,
    delay=1.0e-9,
)

capture = Capture(
    volts=np.zeros((3, 1000)),
    t0=0.0,
    dt=1.0e-9,
    channel_names=("C1", "C2", "C3"),
    antennas={"C1": b_dot_x},
    metadata={"instrument": "siglent-sds6204l"},
)
save_capture(capture, "triad.json")
```

To annotate a capture produced by a driver (whose channels start without
sensors), merge the antennas into a new capture — the waveform data is shared,
not copied:

```python
capture = scope.acquire()
capture = capture.with_antennas({"C1": b_dot_x, "C2": b_dot_y, "C3": b_dot_z})
save_capture(capture, "triad.json")
```

## Aligning captures from several instruments

A `Shot` groups captures from one experiment and stores a scalar time offset per
capture. Different instruments do not share a clock, so measure the offset from
a signal they both see (e.g. a timing photodiode) and store it:

```python
from cetal_scopes import Shot
from cetal_scopes.analysis import estimate_time_offset

shot = Shot.from_captures({"siglent": cap_a, "m4i": cap_b})
offset = estimate_time_offset(shot["siglent"]["C1"], shot["m4i"]["C1"])
shot.set_offset("m4i", offset.offset)          # seconds, applied to m4i's time
print(offset.correlation, offset.inverted)     # quality and polarity

aligned = shot.aligned_channel("m4i", "C1")    # t0 corrected, data shared
t_start, t_end = shot.time_bounds()
```

## Recovering the field from a B-dot

A B-dot's `TransferFunction` maps volts to `dB/dt`; integrating once gives `B`:

```python
from cetal_scopes.analysis import b_field, b_field_rate, b_magnitude

db_dt = b_field_rate(capture["C1"])   # T/s
bx = b_field(capture["C1"])           # T
by = b_field(capture["C2"])
bz = b_field(capture["C3"])
bmag = b_magnitude(bx, by, bz)        # |B| in T
```

The recovered `Channel`s carry `unit="T"` / `"T/s"` and keep their antenna.
`b_field` raises when significant signal energy lies outside the calibrated band;
pass `outside="zero"` or `"clamp"` to band-limit instead.

## Supported scopes

*(list vendors/models as parsers are implemented)*

- [ ] Spectrum M5i.3367
- [ ] Siglent SDS6204L 
- [ ] Tektronix (TBD)
