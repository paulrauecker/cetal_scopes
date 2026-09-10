"""Scalar metrics extracted from channels."""

from __future__ import annotations

import numpy as np

from cetal_scopes.analysis.results import ChannelStats
from cetal_scopes.channel import Channel

__all__ = ["stats"]


def stats(channel: Channel) -> ChannelStats:
    """Compute descriptive time-domain statistics for a channel.

    ``peak_value``/``peak_time`` refer to the sample with the largest absolute
    amplitude.

    Parameters
    ----------
    channel : Channel
        Source channel.

    Returns
    -------
    ChannelStats
        The computed statistics; all fields are ``nan`` for an empty channel.
    """
    values = channel.volts.astype(np.float64)
    if values.size == 0:
        nan = float("nan")
        return ChannelStats(
            name=channel.name,
            n_samples=0,
            mean=nan,
            rms=nan,
            std=nan,
            minimum=nan,
            maximum=nan,
            peak_to_peak=nan,
            peak_value=nan,
            peak_time=nan,
        )
    peak_index = int(np.argmax(np.abs(values)))
    return ChannelStats(
        name=channel.name,
        n_samples=int(values.size),
        mean=float(np.mean(values)),
        rms=float(np.sqrt(np.mean(values**2))),
        std=float(np.std(values)),
        minimum=float(np.min(values)),
        maximum=float(np.max(values)),
        peak_to_peak=float(np.max(values) - np.min(values)),
        peak_value=float(values[peak_index]),
        peak_time=float(channel.time[peak_index]),
    )
