# cetal_scopes Python module

A unified Python module for normalizing oscilloscope captures from multiple
scope vendors into a single universal container format, used across CETAL's
laser-matter interaction diagnostics.

## Features

- Normalizes captures from multiple oscilloscope models into one container class
- Per-channel sensors (`Antenna`) with frequency-dependent complex calibration
- `Shot` groups captures across instruments with measured time offsets
- Pydantic-validated metadata
- JSON + `.npy` sidecar storage format

## Quick example

```python
from cetal_scopes import load_capture

capture = load_capture("path/to/capture.json")
print(capture.metadata)
print(capture.volts.shape)
print(capture["C1"].antenna)
```

See [Getting Started](getting-started.md) for installation and basic usage,
or the [Format Spec](format-spec.md) for details on the storage format.
