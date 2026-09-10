"""Internal helpers shared by the analysis modules."""

from __future__ import annotations

from typing import Literal

import numpy as np
from numpy.typing import NDArray

from cetal_scopes.channel import Channel

#: Trend orders accepted by :func:`scipy.signal.detrend`.
DetrendMode = Literal["constant", "linear"]

__all__ = ["DetrendMode", "with_volts"]


def with_volts(channel: Channel, volts: NDArray[np.float64]) -> Channel:
    """Return a new channel with replaced voltages and no raw codes.

    Processed channels drop their ``raw`` codes because the samples no longer
    correspond one-to-one with the recorded ADC values.
    """
    return Channel(
        name=channel.name,
        volts=volts,
        t0=channel.t0,
        dt=channel.dt,
    )
