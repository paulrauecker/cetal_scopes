"""Instrument drivers."""

from cetal_scopes.scopes.base import Scope
from cetal_scopes.scopes.demo import DemoScope
from cetal_scopes.scopes.registry import DRIVERS, create_scope, driver_class
from cetal_scopes.scopes.siglent import SiglentSDS6204L
from cetal_scopes.scopes.spectrum import SpectrumM5i3367

__all__ = [
    "DRIVERS",
    "DemoScope",
    "Scope",
    "SiglentSDS6204L",
    "SpectrumM5i3367",
    "create_scope",
    "driver_class",
]
