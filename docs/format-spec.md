# Storage Format Spec

Captures are stored as a **JSON metadata file plus NumPy sidecars**:

```
<stem>.json        # CaptureFile: timing, channel order, inline antennas
<stem>.volts.npy   # float64, shape (n_channels, n_samples)
<stem>.raw.npy     # ADC codes, same shape (omitted when raw is None)
```

`save_capture(capture, path)` appends a missing `.json` suffix and writes the
sidecars next to the JSON. Sidecars are resolved **relative to the JSON path**,
so a capture directory can be moved. Writes are atomic (temp file +
`os.replace`) and the JSON is written last, so its presence implies the
sidecars are complete. Load with `np.load(..., allow_pickle=False)`.

## JSON metadata schema

The JSON is validated by the pydantic `CaptureFile` model. Unknown fields are
rejected (`extra="forbid"`) and `format_version` must match the reader.

```json
{
  "format_version": 1,
  "t0": 2.0,
  "dt": 0.5,
  "sample_rate": 2.0,
  "channel_names": ["C1", "C2"],
  "n_samples": 6,
  "volts_sidecar": "cap.volts.npy",
  "raw_sidecar": "cap.raw.npy",
  "channels": [
    {
      "name": "C1",
      "antenna": {
        "name": "Bdot-X",
        "kind": "b-dot",
        "axis": [1.0, 0.0, 0.0],
        "position": [0.0, 0.0, 0.0],
        "delay_s": 1e-9,
        "transfer_function": {
          "unit": "V/(T/s)",
          "freq_hz": [1000000.0, 10000000.0, 100000000.0],
          "gain_real": [1.0, 2.0, 3.0],
          "gain_imag": [0.0, 1.0, -1.0]
        }
      }
    },
    { "name": "C2", "antenna": null }
  ],
  "metadata": { "instrument": "siglent" }
}
```

Field notes:

- `format_version` — integer major version. The reader rejects any other value.
- `t0`, `dt` — first-sample time (s) and sample interval (s); shared by all
  channels.
- `sample_rate` — `1 / dt`, stored for convenience.
- `channel_names` — array row order; channel metadata must match it.
- `volts_sidecar` — required basename of the float64 sidecar.
- `raw_sidecar` — optional basename; `null` when no raw codes were saved.
- `channels[].antenna` — inline sensor description, `null` when unknown.
- `metadata` — free-form provenance, persisted verbatim.

## Antenna calibration

`TransferFunction` stores a sampled complex gain versus frequency: the raw
voltage is the field times the gain (e.g. `V = H(f) * dB/dt` for a B-dot).
Magnitude and phase are preserved via separate `gain_real` / `gain_imag` lists.
Interpolation is linear on the real and imaginary parts; frequencies outside
`[freq_hz[0], freq_hz[-1]]` return `nan`. `axis` is a 3-vector (only its
direction matters) and `position` is a 3-vector in metres.

## Design notes

- Why JSON+npy instead of a single binary blob: human-readable metadata,
  numpy can mmap large arrays directly, no custom binary parser needed.
- `Channel`/`Capture` runtime containers stay plain dataclasses with NumPy
  arrays; the pydantic models describe only the on-disk form.
