"""Calibrate the B-dot loops against the PBS-H3 (C4) from a cal_sweep.py CSV.

Usage: uv run python plot_calibration.py [CSV ...]   (default: the fine sweep)

The H3 is an ideal derivative probe in its band, V = A_H3 * dB/dt with a flat
A_H3 (vendor workbook, ~6.4e-4 m^2, to 50 MHz). So at every frequency

    B      = V_C4 / (A_H3 * 2 pi f)          field at the probes (peak)
    A_eff  = A_H3 * V_Cn / V_C4              effective area of loop n

A_eff is the calibration: flat in frequency for an ideal B-dot. It only means
"sensitivity" for a loop on the same axis as the H3 (+z here); for the others
it is cross-axis pickup. Above the H3 band (shaded) neither number holds.
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from cetal_scopes.antennas import PBS1_PROBES

from plot_sweep import load

HERE = Path(__file__).parent
REF = "C4"
H3 = PBS1_PROBES["H3"]


def plot(path: Path) -> None:
    data = load(path)
    ref = data[REF]
    f = ref["frequency_hz"]
    fm = f / 1e6
    in_band = f <= H3.f_max
    b = ref["amplitude_v"] / (H3.gain * 2 * np.pi * f)

    fig, (ax_b, ax_a, ax_p) = plt.subplots(
        3,
        1,
        sharex=True,
        figsize=(10, 10),
        height_ratios=(1, 2, 1),
        layout="constrained",
    )
    fig.suptitle(
        f"{path.stem}: B-dot calibration against {REF} PBS-H3 "
        f"(A_H3 = {H3.gain:.2e} m², valid to {H3.f_max / 1e6:g} MHz)"
    )

    ax_b.plot(fm, b * 1e9, "o-", ms=3, color="C3")
    ax_b.set_ylabel("B at probe [nT]\n(from H3)")
    ax_b.set_yscale("log")

    print(f"{path.name}: median A_eff up to {H3.f_max / 1e6:g} MHz")
    for ch, d in data.items():
        if ch == REF:
            continue
        a_eff = H3.gain * d["amplitude_v"] / ref["amplitude_v"]
        coaxial = d["axis"][0] == ref["axis"][0]
        role = "co-axial: calibration" if coaxial else "cross-axis pickup"
        label = f"{ch} {d['antenna'][0]} ({d['axis'][0]}, {role})"
        style = "o-" if coaxial else "o--"
        (line,) = ax_a.plot(fm, a_eff, style, ms=3, label=label)
        ax_p.plot(fm, d["phase_vs_c4_deg"], style, ms=3, color=line.get_color())
        med = np.median(a_eff[in_band])
        spread = 20 * np.log10(np.ptp(a_eff[in_band]) / med + 1)
        print(f"  {ch} {d['antenna'][0]:14s} {med:.3e} m^2  ({role}, {spread:.1f} dB)")

    ax_a.axhline(H3.gain, color="C3", lw=0.8, ls=":", label="A_H3 (reference)")
    ax_a.set_ylabel("A_eff = A_H3 · V / V_H3 [m²]")
    ax_a.set_yscale("log")
    ax_a.legend(loc="best", fontsize="small")

    ax_p.set_ylabel("phase vs C4 [deg]")
    ax_p.set_ylim(-180, 180)
    ax_p.set_yticks(range(-180, 181, 90))
    ax_p.set_xlabel("frequency [MHz]")
    ax_p.set_xscale("log")

    for ax in (ax_b, ax_a, ax_p):
        ax.grid(True, which="both", alpha=0.3)
        ax.axvspan(H3.f_max / 1e6, fm.max() * 1.05, color="gray", alpha=0.15, lw=0)
    ax_b.text(
        H3.f_max / 1e6 * 1.1,
        0.9,
        "outside H3 band: not calibrated",
        transform=ax_b.get_xaxis_transform(),
        va="top",
        fontsize="small",
        color="dimgray",
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("csv", nargs="*", type=Path)
    args = p.parse_args()
    for path in args.csv or sorted(HERE.glob("*_nearfield_fine.csv")):
        plot(path)
    plt.show()


if __name__ == "__main__":
    main()
