"""Unified containers for normalizing oscilloscope and digitizer captures."""

from cetal_scopes.antenna import Antenna, TransferFunction
from cetal_scopes.capture import Capture
from cetal_scopes.channel import Channel
from cetal_scopes.scopes.base import Scope
from cetal_scopes.scopes.siglent import SiglentSDS6204L
from cetal_scopes.shot import Shot
from cetal_scopes.storage import load_capture, save_capture

__all__ = [
    "Antenna",
    "Capture",
    "Channel",
    "Scope",
    "Shot",
    "SiglentSDS6204L",
    "TransferFunction",
    "load_capture",
    "save_capture",
]
