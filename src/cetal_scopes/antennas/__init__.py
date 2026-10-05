"""Calibrated reference antennas, grouped by vendor.

Each factory returns a ready-to-assign :class:`~cetal_scopes.antenna.Antenna`
carrying the vendor calibration as a
:class:`~cetal_scopes.antenna.TransferFunction`.
"""

from cetal_scopes.antennas.aaronia import PBS1_PROBES, PBS1Probe, pbs1

__all__ = ["PBS1_PROBES", "PBS1Probe", "pbs1"]
