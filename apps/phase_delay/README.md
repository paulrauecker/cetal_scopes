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

## Separating cables, ports and scope inputs: `cable_swap.py`

Each path is a splitter **port** (with its pad), a **cable** and a scope
**input**. Per channel a run measures `S[port] + K[cable] + I[input]` relative
to its reference; moving cables between runs and solving all runs together by
least squares separates the three, which also calibrates the Siglent's
channel skew. Name every channel of every run as `CHANNEL=CABLE@PORT`:

```sh
uv run apps/phase_delay/cable_swap.py --fmax 1e9 \
    --run measurements/phase_delay/RUN1 C1=1@P1,C2=2@P2,C3=3@P3 \
    --run measurements/phase_delay/RUN2 C2=1@P1,C3=2@P2,C4=3@P3 \
    ...
```

Plan for 6 cables, a 3-way splitter and 4 inputs (8 runs, 6 spare equations;
expected error ~1.4x the per-run error for cables, ~0.9x for inputs):

| Run | P1 | P2 | P3 |
|---|---|---|---|
| 1 | cable 1 -> C1 | cable 2 -> C2 | cable 3 -> C3 |
| 2 | 1 -> C2 | 2 -> C3 | 3 -> C4 |
| 3 | 1 -> C3 | 2 -> C4 | 3 -> C1 |
| 4 | 1 -> C4 | 2 -> C1 | 3 -> C2 |
| 5 | 4 -> C1 | 5 -> C2 | 6 -> C3 |
| 6 | 6 -> C1 | 4 -> C2 | 5 -> C3 |
| 7 | 6 -> C1 | 4 -> C2 | 1 -> C3 |
| 8 | 1 -> C1 | 6 -> C2 | 4 -> C3 |

Runs 1-4 move only the scope ends; runs 5-8 move whole cables. Runs 7 and 8
are not optional: without a run mixing old and new cables, cables 4-6 as a
group float against 1-3.

- Leave `@PORT` out everywhere when each port always feeds the same input;
  the input then stands for the whole position (port + pad + input).
- Factors are relative to their first level seen.
- `--fmin` / `--fmax` re-fit every run on a sub-band from its `points.csv`;
  compare `--fmax 1e9` with `--fmax 1.5e9` to see band dependence.
- Keep scope settings identical across runs: the skew is only valid for them.
- A single 3-way splitter is clean (0.1-0.2 deg rms to 1 GHz); trees of 2-way
  splitters are not (unpadded inter-stage links reflect, +-20-50 deg).
- RF off before every swap.
