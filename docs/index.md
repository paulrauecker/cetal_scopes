# cetal_scopes

A unified Python module for normalizing oscilloscope captures from multiple
scope vendors into a single universal container format, used across CETAL's
laser-matter interaction diagnostics.

## Features

- Normalizes captures from multiple oscilloscope models into one container class
- Pydantic-validated metadata
- JSON + `.npy` sidecar storage format

## Quick example

```python
from cetal_scopes import load_capture

capture = load_capture("path/to/capture.json")
print(capture.metadata.sample_rate)
```

See [Getting Started](getting-started.md) for installation and basic usage,
or the [Format Spec](format-spec.md) for details on the storage format.
