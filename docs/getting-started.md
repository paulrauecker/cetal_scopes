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

# Inspect metadata
print(capture.metadata)

# Access waveform data
print(capture.data.shape)

# Save back out
save_capture(capture, "example_copy.json")
```

## Supported scopes

*(list vendors/models as parsers are implemented)*

- [ ] Spectrum M5i.3367
- [ ] Siglent SDS6204L 
- [ ] Tektronix (TBD)
