"""Calibrated reference antennas, grouped by vendor, and the bench catalogue.

Each factory returns a ready-to-assign :class:`~cetal_scopes.antenna.Antenna`
carrying the vendor calibration as a
:class:`~cetal_scopes.antenna.TransferFunction`. :class:`AntennaCatalog` puts
them, and any bench-specific probes, under one name each.
"""

from cetal_scopes.antennas.aaronia import PBS1_PROBES, PBS1Probe, pbs1
from cetal_scopes.antennas.catalog import (
    AntennaCatalog,
    AntennaEntry,
    builtin_antennas,
    load_antenna_catalog,
    parse_antenna_catalog,
)

__all__ = [
    "PBS1_PROBES",
    "AntennaCatalog",
    "AntennaEntry",
    "PBS1Probe",
    "builtin_antennas",
    "load_antenna_catalog",
    "parse_antenna_catalog",
    "pbs1",
]
