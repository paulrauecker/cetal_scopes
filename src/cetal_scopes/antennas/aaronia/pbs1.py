"""Aaronia PBS1 near-field probe set (PBS-H1..H4 magnetic, PBS-E1 electric).

Aaronia calibrates these probes with a closed-form model rather than a measured
table. The vendor's converter workbooks
(``Aaronia_PBS1_Probe-{Voltage,Power}-Converter.xls``, not distributed with this
package) compute, for a reading into a 50 ohm load::

    dBT   = dBm + offset - 20 log10(f / 1 MHz)    (H probes)
    dBV/m = dBm + offset - 20 log10(f / 1 MHz)    (E1)

with ``dBm = 20 log10(V / sqrt(1 mW * 50 ohm))``. A field proportional to
``V / f`` means ``V`` is proportional to the field's time derivative, so each
probe is an ideal derivative sensor with a frequency-independent, real gain::

    V = gain * dB/dt,    gain = sqrt(0.05) * 10**(-offset / 20) / (2 pi * 1 MHz)

For the H probes ``gain`` is in V/(T/s), i.e. an effective loop area in m^2,
which is exactly what :func:`cetal_scopes.analysis.fields.b_field` deconvolves.
For E1 it is in V/(V/m/s) (a D-dot response).

The model assumes the probe drives a 50 ohm input; set the scope channel to
50 ohm termination. The workbooks give only an upper frequency limit per probe,
so the transfer function spans ``[0, f_max]``: there is no vendor lower bound,
only the falling signal-to-noise of a derivative probe at low frequency.

**Cross-check against the datasheet plots.** Aaronia's datasheet has an
"Output Power vs. Frequency" plot per probe (log frequency axis, 0.1 MHz to
10 GHz; linear dBm axis). The plots were digitized (axes fitted to the
gridlines to ~0.3 px, so ~0.4 dB per pixel) and compared with the workbook
formula above. Residual = plot - workbook, in dB; "-3 dB rolloff" is where
the residual, median-smoothed over +/-0.05 decade to remove trace ripple,
first falls below -3 dB:

| probe | workbook f_max | 1 MHz | 10 MHz | 100 MHz | 1 GHz | at f_max | -3 dB rolloff | stated resonance |
|-------|----------------|-------|--------|---------|-------|----------|----------------|------------------|
| H1    | 3.1 GHz        | -0.2  | -1.4   | -2.0    | -1.4  | -4.0     | 3.0 GHz        | >6 GHz (plot -9.5 dB by 5 GHz) |
| H2    | 1 GHz          | -0.2  | -0.4   | -2.2    | -5.2  | -5.2     | 860 MHz        | 2.6 GHz (notch at 2.64 GHz) |
| H3    | 50 MHz         | +0.4  | -0.1   | -5.8    | --    | -3.2     | 47 MHz         | 500 MHz (notch at 492 MHz) |
| H4    | 70 MHz         | +0.0  | -0.2   | -1.7    | --    | -1.9     | 110 MHz        | 700 MHz (plot ends at 300 MHz) |
| E1    | 6 GHz          | +0.1  | +2.5   | +2.6    | +1.5  | +1.6     | 7.7 GHz        | >3 GHz (peak at 6.2 GHz) |

Findings:

1. **The H plots are mislabelled.** All four say "at 1 mT", but sit
   60.0 +/- 0.6 dB below the workbook at 1 mT, i.e. they are the workbook's
   response at **1 uT**. The workbook is the physically sensible side: its H2
   constant is a ~4.5 mm-radius loop for a 12 mm head, whereas "1 mT" would
   need a ~0.14 mm one. The E1 plot ("at 1 volt/meter") has no such factor.
2. **Below ~10 MHz plot and workbook agree** to within ~0.5 dB at a
   20 dB/decade slope (H2/H3/H4/E1), confirming the ideal-derivative model.
   H1 is slightly tilted (18.5 dB/decade; +1.0 dB at 0.3 MHz, -1.4 at 10 MHz).
3. **The workbook f_max is roughly the -3 dB rolloff point, slightly past it
   for H1, H2 and H3** (3.1 GHz vs 3.0, 1 GHz vs 860 MHz, 50 vs 47 MHz), so at
   their limits the plots are already -4.0, -5.2 and -3.2 dB down and the flat
   model *overestimates* the field there. H4 stays within 2 dB to its 70 MHz
   limit. Separately, H1 and H2 sag ~-2 dB from ~30 MHz up to the rolloff: a
   gain error inside the band, not a band edge, so a lower f_max would not
   remove it.
4. **E1 runs +2 to +3 dB above the workbook from ~2 MHz to ~1 GHz**, a
   systematic offset rather than a rolloff, and bends shallower below 1 MHz
   (+3.7 dB at 0.3 MHz). The plot is also captioned "Isotropic E-field probe,
   3 mm" while the workbook says "Stub electric field probe PBS-E1", so it is
   not certain the two describe the same head.
5. **H4's resonance is not in its plot.** The datasheet claims 700 MHz, but
   the curve stops at 300 MHz after flattening from ~100 MHz. For H3 and H4
   the workbook limit is exactly one tenth of the stated resonance.
6. **H3 and H4 have more effective area than their head.** Their gains
   correspond to single-turn radii of 14.3 mm and 28.3 mm against heads of
   12.5 mm and 25 mm radius (H1: 1.2 vs 3 mm, H2: 4.5 vs 6 mm). Plot and
   workbook agree on this, so they are presumably multi-turn loops.

The transfer functions below still use the workbook's f_max unchanged; the
datasheet only adds magnitude, never phase, so near resonance neither source
calibrates the probe.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from cetal_scopes.antenna import Antenna, TransferFunction

__all__ = ["PBS1_PROBES", "PBS1Probe", "pbs1"]

PBS1Model = Literal["H1", "H2", "H3", "H4", "E1"]

_V_REF_50_OHM = math.sqrt(1.0e-3 * 50.0)
"""RMS volts of 0 dBm into 50 ohm, the workbooks' dBm reference."""

_F_REF_HZ = 1.0e6
"""The workbooks' ``20 log10(f)`` term takes ``f`` in MHz."""


@dataclass(frozen=True)
class PBS1Probe:
    """One probe of the PBS1 set, as defined by the vendor workbooks.

    Parameters
    ----------
    model : str
        Probe designation, e.g. ``"H1"``.
    description : str
        Vendor description of the probe head.
    offset_db : float
        Additive dB constant in the workbook's conversion formula.
    f_max : float
        Upper frequency limit in Hz, as stated by the workbook.
    kind : str
        ``"b-dot"`` for the magnetic probes, ``"d-dot"`` for E1.
    unit : str
        Unit of :attr:`gain`.
    """

    model: PBS1Model
    description: str
    offset_db: float
    f_max: float
    kind: str
    unit: str

    @property
    def gain(self) -> float:
        """Volts per unit field rate (V/(T/s) or V/(V/m/s)), flat in frequency."""
        return (
            _V_REF_50_OHM
            * 10.0 ** (-self.offset_db / 20.0)
            / (2.0 * math.pi * _F_REF_HZ)
        )

    def transfer_function(self) -> TransferFunction:
        """The flat calibration over ``[0, f_max]``."""
        return TransferFunction(
            freq=[0.0, self.f_max],
            gain=[self.gain, self.gain],
            unit=self.unit,
        )


PBS1_PROBES: dict[str, PBS1Probe] = {
    probe.model: probe
    # Datasheet-plot residuals are in the module docstring; in short:
    for probe in (
        # Datasheet plot -4.0 dB at f_max, -9.5 dB by 5 GHz; tilted 18.5 dB/dec.
        PBS1Probe("H1", "6 mm magnetic field probe", -42.2, 3.1e9, "b-dot", "V/(T/s)"),
        # Datasheet plot -5.2 dB at f_max; resonance notch at 2.64 GHz.
        PBS1Probe("H2", "12 mm magnetic field probe", -65.2, 1.0e9, "b-dot", "V/(T/s)"),
        # Datasheet plot -3.2 dB at f_max; resonance notch at 492 MHz.
        PBS1Probe(
            "H3", "25 mm magnetic field probe", -85.1, 50.0e6, "b-dot", "V/(T/s)"
        ),
        # Datasheet plot -1.9 dB at f_max; claimed 700 MHz resonance not shown.
        PBS1Probe(
            "H4", "50 mm magnetic field probe", -96.98, 70.0e6, "b-dot", "V/(T/s)"
        ),
        # Datasheet plot +2..+3 dB above this from 2 MHz to 1 GHz, and captioned
        # "Isotropic E-field probe, 3 mm" -- possibly a different head.
        PBS1Probe(
            "E1", "stub electric field probe", 113.2, 6.0e9, "d-dot", "V/(V/m/s)"
        ),
    )
}
"""Every PBS1 probe, keyed by model designation."""


def pbs1(
    model: PBS1Model,
    *,
    name: str | None = None,
    axis: Sequence[float] | None = None,
    position: Sequence[float] | None = None,
    delay: float = 0.0,
) -> Antenna:
    """Build a calibrated :class:`~cetal_scopes.antenna.Antenna` for a PBS1 probe.

    Parameters
    ----------
    model : {"H1", "H2", "H3", "H4", "E1"}
        Probe designation.
    name : str, optional
        Antenna name; defaults to ``"PBS-<model>"``.
    axis, position : sequence of float, optional
        Placement in the lab frame, passed through to :class:`Antenna`.
    delay : float, optional
        Cable delay in seconds, passed through to :class:`Antenna`.

    Returns
    -------
    Antenna
        The probe with its flat vendor transfer function attached.
    """
    try:
        probe = PBS1_PROBES[model]
    except KeyError:
        raise ValueError(
            f"unknown PBS1 probe {model!r}; expected one of {sorted(PBS1_PROBES)}"
        ) from None
    return Antenna(
        name=name if name is not None else f"PBS-{probe.model}",
        kind=probe.kind,
        axis=axis,
        transfer_function=probe.transfer_function(),
        delay=delay,
        position=position,
    )
