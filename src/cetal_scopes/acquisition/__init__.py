"""Synchronized multi-instrument acquisition.

Drives several :class:`~cetal_scopes.scopes.base.Scope` drivers through one
shot, holding an arm barrier so that no trigger is solicited until every
instrument is ready.
"""

from cetal_scopes.acquisition.events import (
    AcquisitionEvent,
    EventSink,
    InstrumentState,
    Phase,
)
from cetal_scopes.acquisition.instrument import AsyncScope, InstrumentSpec
from cetal_scopes.acquisition.orchestrator import (
    InstrumentResult,
    MultiScopeAcquisition,
    ShotResult,
)

__all__ = [
    "AcquisitionEvent",
    "AsyncScope",
    "EventSink",
    "InstrumentResult",
    "InstrumentSpec",
    "InstrumentState",
    "MultiScopeAcquisition",
    "Phase",
    "ShotResult",
]
