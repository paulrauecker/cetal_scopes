# Storage Format Spec

Captures are stored as a **JSON + `.npy` sidecar** pair:

```
capture_001.json # metadata (pydantic-validated)
capture_001.npy # raw waveform data (numpy float64)
```

## JSON metadata schema

*(fill in as the pydantic model stabilizes)*

```json
{
  "sample_rate": 1.0e9,
  "channels": ["CH1", "CH2"],
  "vertical_scale": [0.1, 0.1],
  "timestamp": "2026-08-13T12:00:00Z",
  "source_instrument": "spectrum_m5i_3367"
}
```

## `.npy` array shape

*(document dimension order, e.g. `[n_channels, n_samples]`, dtype, units)*

## Design notes

- Why JSON+npy instead of a single binary blob: human-readable metadata,
  numpy can mmap large arrays directly, no custom binary parser needed.
- Versioning: *(note here once you add a `format_version` field)*
