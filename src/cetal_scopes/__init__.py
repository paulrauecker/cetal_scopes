"""Unified containers for normalizing oscilloscope and digitizer captures."""

from cetal_scopes.antenna import Antenna, TransferFunction
from cetal_scopes.capture import Capture
from cetal_scopes.channel import Channel
from cetal_scopes.generators.sg8 import SG8SignalGenerator
from cetal_scopes.scopes.base import Scope
from cetal_scopes.scopes.demo import DemoScope
from cetal_scopes.scopes.registry import DRIVERS, create_scope, driver_class
from cetal_scopes.scopes.siglent import SiglentSDS6204L
from cetal_scopes.scopes.spectrum import SpectrumM5i3367
from cetal_scopes.shot import Shot
from cetal_scopes.storage import load_capture, save_capture

__all__ = [
    "DRIVERS",
    "Antenna",
    "Capture",
    "Channel",
    "DemoScope",
    "SG8SignalGenerator",
    "Scope",
    "Shot",
    "SiglentSDS6204L",
    "SpectrumM5i3367",
    "TransferFunction",
    "create_scope",
    "driver_class",
    "load_capture",
    "save_capture",
]
