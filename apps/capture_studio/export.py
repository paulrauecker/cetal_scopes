"""Download the current shot's traces as CSV or NPZ.

Deliberately no PNG: Plotly's own toolbar already saves the figure exactly as
drawn in the browser, so rendering one server-side would mean adding a headless
browser dependency to reproduce something the page can already do.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

import numpy as np

from cetal_scopes.channel import Channel
from cetal_scopes.plotting import decimate_envelope
from cetal_scopes.shot import Shot

__all__ = ["export_shot"]


def _select(
    shot: Shot,
    channels: Sequence[str] | None,
    processed: dict[str, Channel] | None,
) -> dict[str, Channel]:
    available = shot.aligned_channels()
    if channels is None:
        keys = list(available)
    else:
        missing = [key for key in channels if key not in available]
        if missing:
            raise KeyError(f"unknown channels {missing!r}")
        keys = list(channels)
    if not keys:
        raise ValueError("no channels selected")
    return {key: (processed or {}).get(key, available[key]) for key in keys}


def export_shot(
    shot: Shot,
    *,
    fmt: str = "csv",
    channels: Sequence[str] | None = None,
    processed: dict[str, Channel] | None = None,
    max_points: int = 0,
) -> tuple[bytes, str, str]:
    """Render the selected channels for download.

    Parameters
    ----------
    shot : Shot
        The shot to export, on its aligned time axis.
    fmt : {"csv", "npz"}
        ``csv`` interpolates every channel onto one common grid, since a CSV
        has a single time column and the captures do not share a sample rate.
        ``npz`` keeps each channel on its own axis, so nothing is resampled and
        the exported data is exactly what was recorded.
    channels : sequence of str, optional
        Keys to export. Defaults to every channel.
    processed : dict, optional
        Processed replacements, keyed the same way.
    max_points : int, optional
        Per-trace budget. ``0`` exports every sample. Only meaningful for
        ``npz``; a CSV's shared grid is set by the captures themselves.

    Returns
    -------
    payload : bytes
        The file contents.
    media_type : str
        Its MIME type.
    filename : str
        A suggested download name.

    Raises
    ------
    ValueError
        If the format is unknown or no channel is selected.
    KeyError
        If a named channel is not in the shot.
    """
    selected = _select(shot, channels, processed)
    shot_id = str(shot.metadata.get("shot_id") or "shot")
    stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in shot_id) or "shot"

    if fmt == "npz":
        arrays: dict[str, np.ndarray] = {}
        for key, channel in selected.items():
            name = key.replace(":", "__")
            t, values = channel.time, channel.volts
            if max_points > 0:
                t, values = decimate_envelope(t, values, max_points)
            arrays[f"{name}__t"] = t
            arrays[f"{name}__v"] = values
        buffer = io.BytesIO()
        # Explicit, both because the arrays are plain floats and because it
        # keeps the **kwargs unpacking off the allow_pickle parameter.
        np.savez_compressed(buffer, allow_pickle=False, **arrays)
        return buffer.getvalue(), "application/octet-stream", f"{stem}.npz"

    if fmt != "csv":
        raise ValueError(f"unknown export format {fmt!r}; expected 'csv' or 'npz'")

    # One time column, so every channel has to land on one grid. Gaps outside a
    # capture's own span stay empty rather than being filled with edge values.
    grid = shot.common_time(dt=min(channel.dt for channel in selected.values()))
    columns = [
        np.interp(grid, channel.time, channel.volts, left=np.nan, right=np.nan)
        for channel in selected.values()
    ]

    out = io.StringIO()
    out.write("time_s," + ",".join(selected) + "\n")
    for index, t in enumerate(grid):
        cells = [
            "" if np.isnan(column[index]) else f"{column[index]:.9g}"
            for column in columns
        ]
        out.write(f"{t:.12g}," + ",".join(cells) + "\n")
    return out.getvalue().encode("utf-8"), "text/csv", f"{stem}.csv"
