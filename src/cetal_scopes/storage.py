"""Reading and writing :class:`~cetal_scopes.capture.Capture` files.

A capture is a JSON metadata file plus NumPy sidecars::

    <stem>.json        # CaptureFile: timing, channel order, inline antennas
    <stem>.volts.npy   # float64, shape (n_channels, n_samples)
    <stem>.raw.npy     # ADC codes, same shape (omitted when raw is None)

Sidecars are resolved relative to the JSON path, so a capture directory can be
moved. Writes are atomic (temp file + :func:`os.replace`) and the JSON is
written last, so its presence implies the sidecars are complete.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cetal_scopes.capture import Capture
from cetal_scopes.metadata import (
    FORMAT_VERSION,
    AntennaModel,
    CaptureFile,
    ChannelMetadata,
)

__all__ = ["load_capture", "save_capture"]


def _json_path(path: str | os.PathLike[str]) -> Path:
    resolved = Path(path)
    return resolved if resolved.suffix == ".json" else resolved.with_suffix(".json")


def _sidecar_path(json_path: Path, suffix: str) -> Path:
    stem = json_path.with_suffix("")
    return stem.with_name(stem.name + suffix)


def _write_text_atomic(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp_name = tempfile.mkstemp(
        dir=target.parent, prefix=target.name + ".", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_array_atomic(target: Path, array: NDArray[Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp_name = tempfile.mkstemp(
        dir=target.parent, prefix=target.name + ".", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            np.save(handle, array)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def save_capture(capture: Capture, path: str | os.PathLike[str]) -> Path:
    """Save ``capture`` as ``<path>`` plus its sidecars.

    Parameters
    ----------
    capture : Capture
        The capture to persist.
    path : str or path-like
        Target JSON path. A missing ``.json`` suffix is appended, and the
        sidecars are placed next to it with ``.volts.npy`` / ``.raw.npy``.

    Returns
    -------
    pathlib.Path
        The JSON path that was written.
    """
    json_path = _json_path(path)
    volts_path = _sidecar_path(json_path, ".volts.npy")
    raw_path = _sidecar_path(json_path, ".raw.npy")

    names = capture.channel_names
    if names is None:  # pragma: no cover - always set by Capture
        raise RuntimeError("capture has no channel names")

    _write_array_atomic(volts_path, capture.volts)
    raw_sidecar: str | None = None
    if capture.raw is not None:
        _write_array_atomic(raw_path, capture.raw)
        raw_sidecar = raw_path.name

    channels: list[ChannelMetadata] = []
    for name in names:
        antenna = capture.channels[name].antenna
        channels.append(
            ChannelMetadata(
                name=name,
                antenna=None if antenna is None else AntennaModel.from_runtime(antenna),
            )
        )

    file = CaptureFile(
        t0=capture.t0,
        dt=capture.dt,
        sample_rate=1.0 / capture.dt,
        channel_names=list(names),
        n_samples=capture.n_samples,
        volts_sidecar=volts_path.name,
        raw_sidecar=raw_sidecar,
        channels=channels,
        metadata=dict(capture.metadata),
    )
    _write_text_atomic(json_path, file.model_dump_json(indent=2) + "\n")
    return json_path


def _resolve_sidecar(base: Path, name: str) -> Path:
    if Path(name).name != name:
        raise ValueError(f"sidecar name must be a bare filename, got {name!r}")
    path = base / name
    if not path.is_file():
        raise FileNotFoundError(f"missing sidecar {name!r} next to {base}")
    return path


def _check_channel_metadata(file: CaptureFile) -> None:
    names = [channel.name for channel in file.channels]
    if names and names != file.channel_names:
        raise ValueError(
            f"channel metadata {names!r} does not match channel_names "
            f"{file.channel_names!r}"
        )


def load_capture(path: str | os.PathLike[str]) -> Capture:
    """Load a capture saved by :func:`save_capture`.

    Parameters
    ----------
    path : str or path-like
        JSON path of the capture. A missing ``.json`` suffix is appended.

    Returns
    -------
    Capture
        Reconstructed capture, including per-channel antennas.

    Raises
    ------
    ValueError
        If the format version is unknown or the data is inconsistent.
    FileNotFoundError
        If the JSON or a referenced sidecar is missing.
    """
    json_path = _json_path(path)
    file = CaptureFile.model_validate_json(json_path.read_text(encoding="utf-8"))
    if file.format_version != FORMAT_VERSION:
        raise ValueError(
            f"unsupported capture format_version {file.format_version!r}; "
            f"this reader supports {FORMAT_VERSION}"
        )
    _check_channel_metadata(file)

    base = json_path.parent
    volts = np.load(_resolve_sidecar(base, file.volts_sidecar), allow_pickle=False)
    if volts.dtype != np.float64:
        raise ValueError(f"expected float64 volts sidecar, got dtype {volts.dtype}")
    if volts.shape != (len(file.channel_names), file.n_samples):
        raise ValueError(
            f"volts shape {volts.shape} does not match metadata "
            f"({len(file.channel_names)}, {file.n_samples})"
        )

    raw: NDArray[Any] | None = None
    if file.raw_sidecar is not None:
        loaded = np.load(_resolve_sidecar(base, file.raw_sidecar), allow_pickle=False)
        if loaded.shape != volts.shape:
            raise ValueError(
                f"raw shape {loaded.shape} does not match volts shape {volts.shape}"
            )
        raw = loaded

    antennas = {
        channel.name: channel.antenna.to_runtime()
        for channel in file.channels
        if channel.antenna is not None
    }
    return Capture(
        volts=volts,
        t0=file.t0,
        dt=file.dt,
        channel_names=tuple(file.channel_names),
        raw=raw,
        antennas=antennas,
        metadata=dict(file.metadata),
    )
