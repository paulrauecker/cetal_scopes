"""The two sweeps that matter: near-field fine and far-field 1.5 m, one window each.

Usage: uv run python plot_summary.py [--log false] [--ci false]

Top: received tone amplitude in volts per channel (log axis so the
generator-off background, dashed, fits on the same scale).
Middle: each B-dot loop relative to the PBS-H3 (V_Cn / V_C4). Only up to the
H3's 50 MHz limit is this a field-referenced ratio; the shaded band above is not.
Bottom: peak field at the probes from the H3, B = V_C4 / (A_H3 * 2 pi f), linear;
dotted grey above 50 MHz where the H3 is out of band.
Shaded bands (--ci, default on) are 95 % confidence intervals of the mean over
the shots at each point, from the recorded shot-to-shot std (Student t); they
cover repeatability only, not the H3 calibration or placement. Files without
`amplitude_std_v` (the far-field sweep) get none.
Click a legend entry to toggle that channel.
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from cetal_scopes.antennas import PBS1_PROBES
from scipy import stats

from plot_sweep import load

HERE = Path(__file__).parent
SWEEPS = ("*_nearfield_fine.csv", "*_farfield_1.5m.csv")
REF = "C4"
H3 = PBS1_PROBES["H3"]
H3_F_MAX = H3.f_max


def parse_bool(text: str) -> bool:
    if text.lower() in ("true", "1", "yes", "on"):
        return True
    if text.lower() in ("false", "0", "no", "off"):
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {text!r}")


def rel_ci(d: dict[str, np.ndarray]) -> np.ndarray | None:
    """95 % CI half-width of the mean amplitude, relative to the amplitude."""
    if "amplitude_std_v" not in d or np.all(np.isnan(d["amplitude_std_v"])):
        return None
    n = d["shots"]
    t = stats.t.ppf(0.975, n - 1)
    return t * d["amplitude_std_v"] / np.sqrt(n) / d["amplitude_v"]


def band(ax, x, y, rel, color) -> list:
    """CI band around y given a relative half-width (log-safe lower edge)."""
    lo = y * np.clip(1 - rel, 1e-3, None)
    return [ax.fill_between(x, lo, y * (1 + rel), color=color, alpha=0.25, lw=0)]


def plot(path: Path, log: bool = True, ci: bool = True) -> None:
    data = load(path)
    ref = data[REF]
    ref_ci = rel_ci(ref) if ci else None
    fig, (ax_v, ax_r, ax_b) = plt.subplots(
        3,
        1,
        sharex=True,
        figsize=(10, 10),
        height_ratios=(2, 1, 1),
        layout="constrained",
    )
    fig.suptitle(
        f"{path.stem}\n{ref['power_dbm'][0]:+g} dBm, "
        f"range ±{ref['range_v'][0]:g} V, {ref['shots'][0]:g} shots/pt"
    )

    artists: dict[str, list] = {}
    for ch, d in data.items():
        f = d["frequency_hz"] / 1e6
        label = f"{ch} {d['antenna'][0]} ({d['axis'][0]})"
        (line,) = ax_v.plot(f, d["amplitude_v"], "o-", ms=3, label=label)
        color = line.get_color()
        group = [line]
        group += ax_v.plot(f, d["background_v"], "--", lw=0.8, color=color, alpha=0.6)
        ch_ci = rel_ci(d) if ci else None
        if ch_ci is not None:
            group += band(ax_v, f, d["amplitude_v"], ch_ci, color)
        if ch != REF:
            ratio = 20 * np.log10(d["amplitude_v"] / ref["amplitude_v"])
            group += ax_r.plot(f, ratio, "o-", ms=3, color=color)
            if ch_ci is not None and ref_ci is not None:
                # independent errors, added in quadrature
                db = 20 / np.log(10) * np.hypot(ch_ci, ref_ci)
                group += [
                    ax_r.fill_between(
                        f, ratio - db, ratio + db, color=color, alpha=0.25, lw=0
                    )
                ]
        artists[ch] = group

    ax_v.set_ylabel("amplitude [V]")
    if log:
        ax_v.set_yscale("log")
    ax_r.set_ylabel("V / V_C4 (H3) [dB]")
    ax_r.axhline(0, color="gray", lw=0.6)
    f = ref["frequency_hz"]
    b_nt = ref["amplitude_v"] / (H3.gain * 2 * np.pi * f) * 1e9
    in_band = f <= H3_F_MAX
    ax_b.plot(f / 1e6, np.where(in_band, np.nan, b_nt), "o:", ms=3, color="gray")
    ax_b.plot(f / 1e6, np.where(in_band, b_nt, np.nan), "o-", ms=4, color="C3")
    if ref_ci is not None:
        band(ax_b, f / 1e6, np.where(in_band, b_nt, np.nan), ref_ci, "C3")
    ax_b.set_ylabel("B from H3 [nT]")
    ax_b.set_ylim(bottom=0)
    ax_b.set_xlabel("frequency [MHz]")
    ax_b.set_xscale("log")
    f_top = ref["frequency_hz"].max() / 1e6 * 1.05
    for ax in (ax_v, ax_r, ax_b):
        ax.grid(True, which="both", alpha=0.3)
    for ax in (ax_r, ax_b):
        ax.axvspan(H3_F_MAX / 1e6, f_top, color="gray", alpha=0.15, lw=0)
    ax_r.text(
        0.99,
        0.04,
        "shaded: above H3 band (50 MHz)",
        transform=ax_r.transAxes,
        ha="right",
        fontsize="small",
        color="dimgray",
    )

    legend = ax_v.legend(loc="best", fontsize="small")
    pick = {}
    for handle, ch in zip(legend.get_lines(), artists, strict=True):
        handle.set_picker(5)
        pick[handle] = ch

    def on_pick(event):
        ch = pick.get(event.artist)
        if ch is None:
            return
        visible = not artists[ch][0].get_visible()
        for a in artists[ch]:
            a.set_visible(visible)
        event.artist.set_alpha(1.0 if visible else 0.2)
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect("pick_event", on_pick)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--log",
        type=parse_bool,
        default=True,
        metavar="BOOL",
        help="log amplitude axis (default true)",
    )
    p.add_argument(
        "--ci",
        type=parse_bool,
        default=True,
        metavar="BOOL",
        help="95%% confidence bands (default true)",
    )
    args = p.parse_args()
    for pattern in SWEEPS:
        for path in sorted(HERE.glob(pattern)):
            plot(path, log=args.log, ci=args.ci)
    plt.show()


if __name__ == "__main__":
    main()
