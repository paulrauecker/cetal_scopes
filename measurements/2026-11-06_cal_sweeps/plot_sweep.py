"""Plot cal_sweep.py CSVs: received amplitude, SNR and phase vs frequency.

Usage: uv run python plot_sweep.py [CSV ...]   (default: every CSV beside this script)

One window per file. Dashed lines are the generator-off background at each
frequency; black X marks clipped points. Click a legend entry to toggle that
channel on all panels.
"""

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).parent


def load(path: Path) -> dict[str, dict[str, np.ndarray]]:
    """Rows grouped by channel, columns as arrays (float where possible)."""
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_ch: dict[str, dict[str, np.ndarray]] = {}
    for ch in dict.fromkeys(r["channel"] for r in rows):
        sel = sorted(
            (r for r in rows if r["channel"] == ch),
            key=lambda r: float(r["frequency_hz"]),
        )
        cols = {}
        for key in sel[0]:
            vals = [r[key] for r in sel]
            try:
                cols[key] = np.array([float(v) if v != "" else np.nan for v in vals])
            except ValueError:
                cols[key] = np.array(vals)
        by_ch[ch] = cols
    return by_ch


def plot(path: Path) -> None:
    data = load(path)
    fig, (ax_a, ax_v, ax_s, ax_p) = plt.subplots(
        4,
        1,
        sharex=True,
        figsize=(10, 11),
        height_ratios=(2, 2, 1, 1),
        layout="constrained",
    )
    first = next(iter(data.values()))
    fig.suptitle(
        f"{path.stem}\n{first['power_dbm'][0]:+g} dBm, "
        f"range ±{first['range_v'][0]:g} V, {first['shots'][0]:g} shots/pt"
    )

    artists: dict[str, list] = {}
    for ch, d in data.items():
        f = d["frequency_hz"] / 1e6
        label = f"{ch} {d['antenna'][0]} ({d['axis'][0]})"
        (line,) = ax_a.plot(f, d["amplitude_dbv"], "o-", ms=3, label=label)
        color = line.get_color()
        group = [line]
        group += ax_a.plot(
            f, 20 * np.log10(d["background_v"]), "--", lw=0.8, color=color, alpha=0.6
        )
        clipped = d["clipped"] == "True"
        group += ax_a.plot(
            f[clipped], d["amplitude_dbv"][clipped], "x", ms=9, mew=2, color="black"
        )
        group += ax_v.plot(f, d["amplitude_v"], "o-", ms=3, color=color)
        group += ax_v.plot(
            f[clipped], d["amplitude_v"][clipped], "x", ms=9, mew=2, color="black"
        )
        group += ax_s.plot(f, d["snr_db"], "o-", ms=3, color=color)
        if ch != "C4":  # phases are relative to C4
            group += ax_p.plot(f, d["phase_vs_c4_deg"], "o-", ms=3, color=color)
        artists[ch] = group

    ax_a.set_ylabel("amplitude [dBV]")
    ax_v.set_ylabel("amplitude [V]")
    ax_s.set_ylabel("SNR [dB]")
    ax_s.axhline(0, color="gray", lw=0.6)
    ax_p.set_ylabel("phase vs C4 [deg]")
    ax_p.set_ylim(-180, 180)
    ax_p.set_yticks(range(-180, 181, 90))
    ax_p.set_xlabel("frequency [MHz]")
    ax_p.set_xscale("log")
    for ax in (ax_a, ax_v, ax_s, ax_p):
        ax.grid(True, which="both", alpha=0.3)

    legend = ax_a.legend(loc="best", fontsize="small")
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
    p.add_argument("csv", nargs="*", type=Path)
    args = p.parse_args()
    for path in args.csv or sorted(HERE.glob("*.csv")):
        plot(path)
    plt.show()


if __name__ == "__main__":
    main()
