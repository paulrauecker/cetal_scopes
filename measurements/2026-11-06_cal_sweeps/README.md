# Calibration sweeps, 2026-11-06

SG8 CW tone into a transmit antenna, received on the Siglent SDS6204L:
C1-C3 are the three loops of the 3D B-dot, C4 the Aaronia PBS-H3.

| Ch | Antenna         | Axis (unverified sign) |
|----|-----------------|------------------------|
| C1 | `bdot-medium-a` | +y |
| C2 | `bdot-large`    | +z |
| C3 | `bdot-small`    | +x |
| C4 | `PBS-H3`        | +z |

## Files

| File | Setup | Power | Range | Points | Shots/pt |
|------|-------|-------|-------|--------|----------|
| `*_farfield_1.5m.csv` | TX at 1.5 m, far field | +10 dBm | +/-20 mV | 14, 30 MHz-2 GHz | 2 |
| `*_nearfield_levelcheck.csv` | TX in near field, loop in xy plane (axis z) | -20 dBm | +/-0.5 V | 6 | 1 |
| `*_nearfield_quick.csv` | same | 0 dBm | +/-0.1 V | 16, 10 MHz-2 GHz | 2 |
| `*_nearfield_fine.csv` | same | 0 dBm | +/-0.1 V | 100 log-spaced, 10 MHz-1 GHz | 5 |

The near-field TX distance was not recorded.

## Method

`cal_sweep.py` (the far-field file came from an earlier, fixed-parameter
version of it). Forced (free-running) triggers, 10 GS/s, coherent tone
detection (`analysis.spectral.tone_amplitude`) at the generator frequency.
Generator-off background shots, evaluated at every sweep frequency, give
`background_v` and `snr_db`. Frequencies within 1 MHz of the Siglent ADC comb
(k * 39.0625 MHz, above 30 MHz) are moved up by 1.5 MHz. The trigger is not
locked to the generator, so phases are relative to C4 (`phase_vs_c4_deg`);
`phase_coherence` near 1 means that relative phase was stable across shots.

## Caveats

- Relative response only: received volts per generator setting, not probe
  gains. The PBS-H3 is calibrated to 50 MHz (-3 dB at 47 MHz, resonance notch
  at 492 MHz), so it is a field reference only below ~50 MHz.
- **The first point of every sweep is invalid** (10 MHz near field, 30 MHz
  far field). The SG8 output was turned on right before it, and the RF is not
  up when `*OPC?` returns (settled by ~0.9 s, found 2026-11-10), so its first
  shot read the noise floor. This explains the large `amplitude_std_v` /
  `amplitude_spread_v` there (30-60 %, against < 0.5 % from the second point
  on). The level check has one shot per point, so its 10 MHz row may be
  entirely noise. `cal_sweep.py` now waits 1 s after turning the output on.
- Far field: C1 clipped at several points (`clipped` column); 1.5 m is not
  far field below ~100-200 MHz anyway.
- Ambient lines seen with the generator off: ~2.4 GHz (WiFi), 925/930 MHz
  (GSM), ~390 MHz.
- Fine sweep: 5 of 500 forced triggers did not complete within 10 s and were
  retried once (successfully). No point is missing; nothing clipped (max
  input 87 mV against the +/-0.1 V range).
