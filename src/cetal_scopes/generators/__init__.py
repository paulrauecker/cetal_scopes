"""Signal-generator drivers.

Unlike :mod:`cetal_scopes.scopes`, these instruments don't acquire waveforms,
so they don't implement the :class:`~cetal_scopes.scopes.base.Scope`
interface.
"""

from cetal_scopes.generators.sg8 import SG8SignalGenerator

__all__ = ["SG8SignalGenerator"]
