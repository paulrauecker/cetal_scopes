# phase_delay

Delay differences between cable paths, from the phase between Siglent channels
across an SG8 CW sweep (default 100 MHz-2 GHz, 25 MHz steps, 5 repeats).

Per frequency, all channels come from **one** acquisition and are fitted at
**one** frequency (estimated on the reference) on one time axis; per channel,
the phase relative to `--reference` is unwrapped along frequency and fitted
with a line. Slope / 2π = delay (positive: channel later than reference);
intercept ≈ 0° normal, ≈ 180° polarity inversion. The fitting lives in
`cetal_scopes.analysis.phase_delay`.

One run measures the **whole path** per channel: cable + far-end pickup
position + scope channel skew (< 100 ps spec). Moving cables between scope
inputs in a second run separates skew from cable.

```sh
uv run apps/phase_delay/phase_delay.py --label "cable1-4 on C1-C4"
uv run apps/phase_delay/phase_delay.py --demo      # synthetic, known delays
```

Each run writes `measurements/phase_delay/<UTC timestamp>/`: `points.csv`,
`sweep.npz` (every repeat), `result.json`, `metadata.json`, `phase_delay.png`.

Notes:

- Power is capped at +5 dBm. The SG8 needs ~1 s after output-on to reach
  level; the app waits 3 s, then `--settle` (0.2 s) per step.
- Timebase is fixed at 50 µs/div (500 µs): the shortest window that stays at
  the native 5 GS/s. Interpolation is off (driver default).
- Frequencies within `--comb-guard` of the 5 GS/s ADC comb (multiples of
  39.0625 MHz: 625/1250/1875 MHz on the default grid) are kept in the CSV but
  excluded from the fit.
