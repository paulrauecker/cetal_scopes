"""Separate cable, splitter-port and scope-input delays across swap runs.

Each ``phase_delay.py`` run measures, per scope channel, the delay of the whole
path relative to that run's reference channel. A path is a splitter *port*
(with its pad), a *cable*, and a scope *input*. Moving cables between ports and
inputs and re-running gives, for run ``r`` and channel ``i``::

    tau(r, i) = S[port] + K[cable] + I[i] + b[r]

``b[r]`` absorbs the run's reference (its own ``tau`` is 0 by definition).
This module solves for ``S``, ``K`` and ``I`` by weighted least squares. Only
differences are observable, so every factor is reported relative to its first
level seen (first cable named, first port named, the first run's first
channel). With more equations than unknowns, the scatter of the residuals is an
honest error bar: it includes connector repeatability, drift and the reflection
ripple that changes between arrangements.

Mappings name every channel of a run as ``CHANNEL=CABLE@PORT``. Leaving out
``@PORT`` everywhere drops the port factor: the scope input then stands for
the whole fixed position (port + pad + input), which is right when each port
always goes to the same input.

Usage (one ``--run`` per arrangement)::

    uv run apps/phase_delay/cable_swap.py \\
        --run measurements/phase_delay/RUN1 C1=1@P1,C2=2@P2,C3=3@P3 \\
        --run measurements/phase_delay/RUN2 C2=1@P1,C3=2@P2,C4=3@P3

``--fmin`` / ``--fmax`` re-fit every run's slope on a sub-band from its
``points.csv`` instead of using the saved ``result.json`` delays.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cetal_scopes.analysis.phase_delay import delay_to_length, fit_delay

#: A reference channel's own delay is 0 exactly; give it a tiny sigma rather
#: than zero so it still fits the weighted design.
REFERENCE_SIGMA_S = 1e-15
#: Factor names, in report order.
FACTORS = ("cable", "input", "port")


@dataclass(frozen=True)
class Observation:
    """One path delay from one run: ``tau`` relative to that run's reference."""

    run: int
    input: str
    cable: str
    port: str | None
    tau: float
    sigma: float

    def level(self, factor: str) -> str | None:
        """This observation's level of ``factor`` (``None``: factor absent)."""
        return {"cable": self.cable, "input": self.input, "port": self.port}[factor]


@dataclass(frozen=True)
class Solution:
    """Least-squares result; delays in seconds, per factor and level."""

    values: dict[str, dict[str, float]]
    errors: dict[str, dict[str, float]]
    residuals: list[tuple[Observation, float]]
    chi2_reduced: float
    dof: int


def parse_mapping(text: str) -> dict[str, tuple[str, str | None]]:
    """``"C1=A@P1,C2=B@P2"`` -> ``{"C1": ("A", "P1"), "C2": ("B", "P2")}``."""
    out: dict[str, tuple[str, str | None]] = {}
    for item in text.split(","):
        channel, sep, rest = item.partition("=")
        cable, at, port = rest.partition("@")
        if not sep or not channel.strip() or not cable.strip() or (at and not port):
            raise ValueError(f"expected CHANNEL=CABLE[@PORT], got {item!r}")
        out[channel.strip()] = (cable.strip(), port.strip() if at else None)
    cables = [c for c, _ in out.values()]
    ports = [p for _, p in out.values() if p is not None]
    if len(set(cables)) != len(cables) or len(set(ports)) != len(ports):
        raise ValueError(f"a cable or port appears twice in {text!r}")
    if ports and len(ports) != len(out):
        raise ValueError(f"give @PORT for every channel or none in {text!r}")
    return out


def refit(points_csv: Path, fmin: float, fmax: float) -> dict[str, tuple[float, float]]:
    """Per channel (delay, stderr) from ``points.csv`` on ``[fmin, fmax]``."""
    rows: dict[str, list[tuple[float, float]]] = {}
    with points_csv.open() as handle:
        for row in csv.DictReader(handle):
            f = float(row["f_set_hz"])
            if int(row["used_in_fit"]) and fmin <= f <= fmax:
                rows.setdefault(row["channel"], []).append(
                    (float(row["f_fit_hz"]), np.radians(float(row["dphi_deg"])))
                )
    out = {}
    for channel, pts in rows.items():
        f, phase = np.array(pts).T
        fit = fit_delay(f, phase)
        out[channel] = (fit.delay, fit.delay_stderr)
    return out


def load_run(
    run: int,
    directory: Path,
    mapping: dict[str, tuple[str, str | None]],
    band: tuple[float, float] | None = None,
) -> list[Observation]:
    """Observations of one run, its reference included as ``tau = 0``.

    They come in ``mapping`` order, so the first cable (and port) named in the
    first run is the one the solution is relative to.
    """
    result = json.loads((directory / "result.json").read_text())
    reference = result["reference"]
    if band is None:
        delays = {
            ch: (v["delay_ps"] * 1e-12, v["delay_stderr_ps"] * 1e-12)
            for ch, v in result["channels"].items()
        }
    else:
        delays = refit(directory / "points.csv", *band)
    delays[reference] = (0.0, REFERENCE_SIGMA_S)
    if set(mapping) != set(delays):
        raise ValueError(
            f"{directory}: mapping names {sorted(mapping)}, run has {sorted(delays)}"
        )
    return [
        Observation(
            run,
            ch,
            cable,
            port,
            delays[ch][0],
            max(delays[ch][1], REFERENCE_SIGMA_S),
        )
        for ch, (cable, port) in mapping.items()
    ]


def solve(observations: Sequence[Observation]) -> Solution:
    """Weighted least squares for every factor's delays."""
    if len({o.port is None for o in observations}) > 1:
        raise ValueError("either every run names ports or none does")
    factors = [f for f in FACTORS if observations[0].level(f) is not None]
    levels = {
        f: list(dict.fromkeys(str(o.level(f)) for o in observations)) for f in factors
    }
    # Gauge: the first level of each factor is 0 -> no column for it.
    columns = [(f, lv) for f in factors for lv in levels[f][1:]]
    runs = sorted({o.run for o in observations})
    n = len(columns) + len(runs)
    a = np.zeros((len(observations), n))
    y = np.array([o.tau for o in observations])
    w = np.array([1.0 / o.sigma for o in observations])
    index = {c: j for j, c in enumerate(columns)}
    for i, o in enumerate(observations):
        for f in factors:
            j = index.get((f, str(o.level(f))))
            if j is not None:
                a[i, j] = 1.0
        a[i, len(columns) + runs.index(o.run)] = 1.0
    if np.linalg.matrix_rank(a) < n:
        raise ValueError(
            "the arrangements leave some delays undetermined: every cable, port "
            "and input must be linked through runs that share them"
        )
    aw = a * w[:, None]
    x, *_ = np.linalg.lstsq(aw, y * w, rcond=None)
    resid = y - a @ x
    dof = len(observations) - n
    chi2 = float(np.sum((resid * w) ** 2) / dof) if dof > 0 else float("nan")
    cov = np.linalg.inv(aw.T @ aw) * (max(chi2, 1.0) if dof > 0 else 1.0)
    err = np.sqrt(np.diag(cov))
    values = {f: dict.fromkeys(levels[f], 0.0) for f in factors}
    errors = {f: dict.fromkeys(levels[f], 0.0) for f in factors}
    for (f, lv), j in index.items():
        values[f][lv] = float(x[j])
        errors[f][lv] = float(err[j])
    return Solution(
        values=values,
        errors=errors,
        residuals=list(zip(observations, resid.tolist(), strict=True)),
        chi2_reduced=chi2,
        dof=dof,
    )


def format_solution(s: Solution, velocity_factor: float) -> str:
    """Human-readable tables."""
    titles = {
        "cable": "Cables",
        "input": "Scope inputs (skew)",
        "port": "Splitter ports (+ pad)",
    }
    lines = []
    for f, vals in s.values.items():
        first = next(iter(vals))
        lines.append(f"{titles[f]}, relative to {first}:")
        for k, v in vals.items():
            line = f"  {k:>6}  {v * 1e12:+9.2f} ± {s.errors[f][k] * 1e12:5.2f} ps"
            if f == "cable":
                mm = delay_to_length(v, velocity_factor) * 1e3
                line += f"  ({mm:+7.2f} mm @ VF {velocity_factor})"
            lines.append(line)
    if s.dof > 0:
        r = [r for o, r in s.residuals if o.sigma != REFERENCE_SIGMA_S]
        rms = float(np.sqrt(np.mean(np.square(r))))
        lines.append(
            f"Fit: dof {s.dof}, residual rms {rms * 1e12:.2f} ps, "
            f"reduced chi2 {s.chi2_reduced:.1f} (errors scaled by sqrt when > 1)"
        )
        worst = max(s.residuals, key=lambda t: abs(t[1]))
        o = worst[0]
        lines.append(
            f"Largest residual: run {o.run + 1} {o.input} (cable {o.cable}) "
            f"{worst[1] * 1e12:+.2f} ps"
        )
    else:
        lines.append("Fit: exactly determined (no redundancy, no error check)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Command-line options."""
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument(
        "--run",
        nargs=2,
        action="append",
        metavar=("DIR", "MAPPING"),
        required=True,
        help="run directory and CHANNEL=CABLE[@PORT],... for every channel",
    )
    p.add_argument("--fmin", type=float, help="Hz; re-fit from points.csv")
    p.add_argument("--fmax", type=float, help="Hz; re-fit from points.csv")
    p.add_argument("--vf", type=float, default=0.85, help="cable velocity factor")
    p.add_argument("--json", type=Path, help="also write the solution here")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Load every run, solve, print (and optionally save) the result."""
    args = build_parser().parse_args(argv)
    band = None
    if args.fmin is not None or args.fmax is not None:
        band = (args.fmin or 0.0, args.fmax or np.inf)
    obs: list[Observation] = []
    try:
        for i, (directory, mapping) in enumerate(args.run):
            obs += load_run(i, Path(directory), parse_mapping(mapping), band)
        sol = solve(obs)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    print(format_solution(sol, args.vf))
    if args.json:
        record = {
            "runs": [{"dir": d, "mapping": m} for d, m in args.run],
            "band_hz": band,
            "velocity_factor": args.vf,
            "delays_ps": {
                f: {k: v * 1e12 for k, v in vals.items()}
                for f, vals in sol.values.items()
            },
            "errors_ps": {
                f: {k: v * 1e12 for k, v in vals.items()}
                for f, vals in sol.errors.items()
            },
            "chi2_reduced": sol.chi2_reduced,
            "dof": sol.dof,
        }
        args.json.write_text(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
