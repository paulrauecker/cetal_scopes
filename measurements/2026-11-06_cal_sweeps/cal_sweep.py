"""Calibration sweep: SG8 CW tone -> Siglent C1-C4, coherent tone detection.

Background (generator off) shots at the start and end bracket the sweep and
are evaluated at every sweep frequency. Frequencies within 1 MHz of the ADC
comb (k * 39.0625 MHz) are nudged off it. Stops at --budget and still writes.
"""

import argparse
import csv
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from cetal_scopes import SG8SignalGenerator
from cetal_scopes.analysis.spectral import tone_amplitude
from cetal_scopes.scopes.siglent import SiglentSDS6204L

CHANNELS = ("C1", "C2", "C3", "C4")
ANTENNA = {"C1": ("bdot-medium-a", "+y"), "C2": ("bdot-large", "+z"),
           "C3": ("bdot-small", "+x"), "C4": ("PBS-H3", "+z")}
REF = "C4"
COMB = 39.0625e6

p = argparse.ArgumentParser()
p.add_argument("--freqs-mhz", type=float, nargs="*")
p.add_argument("--fine", type=float, nargs=3, metavar=("START_MHZ", "STOP_MHZ", "N"))
p.add_argument("--power", type=float, required=True)
p.add_argument("--range", type=float, required=True)
p.add_argument("--shots", type=int, default=2)
p.add_argument("--record", type=int, default=25_000)
p.add_argument("--budget", type=float, default=1e9)
p.add_argument("--label", required=True)
p.add_argument("--setup", default="")
p.add_argument("--distance", default="")
args = p.parse_args()

if args.fine:
    a, b, n = args.fine
    freqs = list(np.geomspace(a * 1e6, b * 1e6, int(n)))
else:
    freqs = [f * 1e6 for f in args.freqs_mhz]
freqs = [f + 1.5e6 if f > 30e6 and abs(f - round(f / COMB) * COMB) < 1e6 else f for f in freqs]

out = Path.home() / "coding/python/cetal_scopes/shots/cal_sweeps"
out.mkdir(parents=True, exist_ok=True)
path = out / f"{datetime.now():%Y-%m-%d_%H%M%S}_{args.label}.csv"


def shot(s, tries=3):
    for attempt in range(tries):
        s.arm(); s.force_trigger()
        if s.wait(10):
            return s.fetch()
        s.abort()
        print(f"  missed forced trigger (attempt {attempt + 1}/{tries})", flush=True)
    raise RuntimeError("no trigger")


start = time.monotonic()
rows = []
fh = path.open("w", newline="")
writer = None
with SiglentSDS6204L("192.168.0.11", channels=CHANNELS, timeout=5, acquire_timeout=10) as s, \
        SG8SignalGenerator("ASRL/dev/ttyUSB0::INSTR") as g:
    s.configure({"sample_rate": 5e9, "record_length": args.record, "pretrigger": 0.5,
                 "range": args.range, "offset": 0, "coupling": "DC", "impedance": 50,
                 "trigger": {"source": REF, "level": 0.0, "slope": "EITHER"}})
    try:
        g.set_output(False)
        backgrounds = [shot(s) for _ in range(2)]
        g.set_power(args.power)
        g.operation_complete()
        g.set_output(True)
        g.operation_complete()
        # *OPC? returns before the RF is up; without this the first point
        # reads (partly) the noise floor
        time.sleep(1.0)
        g.clear_errors()
        for f in freqs:
            if time.monotonic() - start > args.budget:
                print(f"budget reached, stopping before {f / 1e6:.2f} MHz")
                break
            g.set_frequency(f)
            g.operation_complete()
            time.sleep(0.05)
            err = g.system_error()
            if err[0] != 0:
                print("SG8 error:", err)
            amps = {c: [] for c in CHANNELS}
            phases = {c: [] for c in CHANNELS}
            peak = {c: 0.0 for c in CHANNELS}
            for _ in range(args.shots):
                cap = shot(s)
                tones = {c: tone_amplitude(cap.channels[c], f) for c in CHANNELS}
                for c in CHANNELS:
                    amps[c].append(tones[c].amplitude)
                    # relative to the reference: the forced trigger is not
                    # locked to the generator
                    phases[c].append(np.exp(1j * (tones[c].phase - tones[REF].phase)))
                    peak[c] = max(peak[c], float(np.abs(cap.channels[c].volts).max()))
            fs = cap.metadata["sample_rate_hz"]
            for c in CHANNELS:
                a_on = float(np.mean(amps[c]))
                bg = [tone_amplitude(b.channels[c], f).amplitude for b in backgrounds]
                a_off = float(np.sqrt(np.mean(np.square(bg))))
                rows.append({
                    "frequency_hz": f, "power_dbm": args.power, "setup": args.setup,
                    "distance_m": args.distance,
                    "channel": c, "antenna": ANTENNA[c][0], "axis": ANTENNA[c][1],
                    "amplitude_v": a_on, "amplitude_dbv": 20 * np.log10(a_on),
                    "amplitude_std_v": float(np.std(amps[c], ddof=1)) if args.shots > 1 else "",
                    "background_v": a_off,
                    "snr_db": 20 * np.log10(a_on / max(a_off, 1e-12)),
                    "phase_vs_c4_deg": float(np.degrees(np.angle(np.mean(phases[c])))),
                    "phase_coherence": float(abs(np.mean(phases[c]))),
                    "peak_v": peak[c], "clipped": peak[c] >= 0.98 * args.range,
                    "shots": args.shots, "sample_rate_hz": fs,
                    "samples": cap.volts.shape[1], "range_v": args.range,
                })
            if writer is None:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
                writer.writeheader()
            writer.writerows(rows[-len(CHANNELS):])
            fh.flush()
            line = "  ".join(f"{c} {20 * np.log10(np.mean(amps[c])):6.1f}" for c in CHANNELS)
            clip = "".join("X" if peak[c] >= 0.98 * args.range else "." for c in CHANNELS)
            print(f"{f / 1e6:8.2f} MHz  {line} dBV  clip {clip}  t={time.monotonic() - start:5.1f}s",
                  flush=True)
    finally:
        g.set_output(False)
        print("SG8 output off:", not g.output())

        fh.close()
print(f"wrote {len(rows)} rows -> {path}  ({time.monotonic() - start:.1f}s)")
