"""Unified containers for normalizing oscilloscope and digitizer captures."""

from cetal_scopes.capture import Capture
from cetal_scopes.channel import Channel
from cetal_scopes.scopes.base import Scope
from cetal_scopes.scopes.siglent import SiglentSDS6204L

__all__ = ["Capture", "Channel", "Scope", "SiglentSDS6204L"]
