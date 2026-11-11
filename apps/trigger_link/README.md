# trigger_link

Checks the Siglent SDS6204L **Aux Out → M5i Trig In** trigger link and
measures its timing.

The SG8 drives a CW tone into the Siglent. The Siglent runs SINGle shots on an
edge trigger on that tone, and every shot must trigger the M5i exactly once.

For each frequency the app reports:

- **Shots, missed, spurious**:
  - *Missed*: the Siglent fired a single shot but the M5i didn't trigger.
  - *Spurious*: the M5i triggered while the Siglent was idle.
- **Siglent jitter**: the spread of the Siglent's own trigger point (its
  `t = 0`) against the tone it triggers on.
  - The driver's `t0` carries no sub-sample correction, so expect about one
    sample, uniformly spread: roughly 30 ps rms at 10 GS/s, more on slow edges.
  - *pair* is the C3 − C2 spread within the same shots. Channels of one
    acquisition share the trigger error, so this stays sub-ps.
- **Link jitter**: the spread of the tone phase at the M5i trigger relative to
  the Siglent trigger.
  - Expect at least about 58 ps rms. The datasheet gives the M5i's trigger
    accuracy as 1 sample at 5 GS/s.
  - Needs the **same tone on the M5i channel**, for example a splitter output
    on CH0. Without it the line reads *unresolved*.
- **Link delay**: the slope of the mean link phase across frequencies.
  - Use closely spaced `--freqs` so the phase can be unwrapped, e.g.
    `100 101 102 105 110 120 150 200`.
  - The result includes any difference between the two signal paths to the
    instruments.

```sh
uv run apps/trigger_link/trigger_link.py --shots 30 --freqs 100 300 1000
```

The summary is saved as JSON under `measurements/trigger_link/`.

## Wiring and settings

| Item | Setting | Why |
|---|---|---|
| Siglent Aux Out | Trigger out | Set on the front panel; the driver can't set it or read it back. |
| M5i Ext0 | `{"source": "EXT", "level": 1.5, "slope": "RISING"}` | At 3 kΩ termination (the driver default) the pulse arrives at 3.0–3.5 V. |
| M5i `trigger_termination` | `3000` (default) or `50` | At 50 Ω the pulse is only 0.4–0.75 V, because Aux Out has a high source impedance. Use `--level 0.25` there. |
| M5i `--reference-clock` | `10e6` with Siglent 10 MHz Out → Clk In | Locks the M5i sample clock to the Siglent's reference. Without the signal, every arm fails with error 268. |
| SG8 power | ≤ +5 dBm | It drives the scope inputs directly; `--power` is refused above the cap. |

On the 50 Ω setting: the datasheet rates both terminations well above 3.3 V
logic (±20 V at 50 Ω, 7 Vrms at 3 kΩ). The M5i **analog** inputs are the
fragile part: 1.4 Vrms on the ±200 mV range.
