"""Shared physical-unit configuration vocabulary for :class:`Scope` drivers.

Every :class:`~cetal_scopes.scopes.base.Scope` subclass accepts its own
panel-native ``configure()`` keys (e.g. Siglent's ``timebase`` in s/div), but
they additionally accept this common, SI-unit vocabulary so that shared
application code can drive any instrument without knowing its front panel:

- ``sample_rate`` -- Hz
- ``record_length`` -- samples
- ``pretrigger`` -- samples (``int``) or a fraction of the record (``float``
  in ``[0, 1]``)
- ``channels`` -- sequence of channel names
- ``range`` -- volts full-scale (i.e. the channel spans ``+/-range``)
- ``offset`` -- volts
- ``coupling`` -- e.g. ``"DC"``, ``"AC"``
- ``impedance`` -- ohms
- ``trigger`` -- mapping with ``source``, ``level`` (volts), ``slope``

``range``, ``offset``, ``coupling``, and ``impedance`` each accept either a
single value (applied to every channel) or a mapping from channel name to
value, so the same key serves instruments with different channel counts.

This module only normalizes the physical vocabulary; it does not know a
driver's panel-native keys, so it does not detect collisions between the two
(e.g. ``sample_rate`` vs. ``timebase``) -- that is each driver's own
responsibility, since only the driver knows which panel keys alias which
physical quantity.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "PHYSICAL_KEYS",
    "PhysicalSettings",
    "TriggerSettings",
    "expand_per_channel",
    "normalize_physical",
    "normalize_pretrigger",
    "normalize_trigger",
    "pretrigger_samples",
]

PHYSICAL_KEYS = frozenset(
    {
        "sample_rate",
        "record_length",
        "pretrigger",
        "channels",
        "range",
        "offset",
        "coupling",
        "impedance",
        "trigger",
    }
)

_TRIGGER_KEYS = frozenset({"source", "level", "slope"})


@dataclass(frozen=True)
class TriggerSettings:
    """Normalized ``trigger`` mapping: source, level (volts), and slope."""

    source: str | None = None
    level: float | None = None
    slope: str | None = None


@dataclass(frozen=True)
class PhysicalSettings:
    """The physical-vocabulary subset of a ``configure()`` call, normalized.

    Every field is ``None`` (or empty) when its key was absent from the
    input mapping. Per-channel fields are always expanded to
    ``{channel_name: value}``, even when the input gave a single scalar.
    """

    sample_rate: float | None = None
    record_length: int | None = None
    pretrigger: int | float | None = None
    channels: tuple[str, ...] | None = None
    range: dict[str, float] | None = None
    offset: dict[str, float] | None = None
    coupling: dict[str, str] | None = None
    impedance: dict[str, float] | None = None
    trigger: TriggerSettings | None = None


def normalize_pretrigger(value: int | float) -> int | float:
    """Validate a ``pretrigger`` spec: non-negative samples or a ``[0, 1]`` fraction.

    Returns the value unchanged (still either ``int`` samples or a ``float``
    fraction); converting a fraction to samples needs the record length,
    which only the driver has at hand -- see :func:`pretrigger_samples`.
    """
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"pretrigger samples must be non-negative, got {value!r}")
        return value
    if isinstance(value, float):
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                f"pretrigger fraction must be within [0, 1], got {value!r}"
            )
        return value
    raise TypeError(
        f"pretrigger must be an int (samples) or a float (fraction of the "
        f"record), got {type(value).__name__}"
    )


def pretrigger_samples(value: int | float, record_length: int) -> int:
    """Resolve an already-normalized ``pretrigger`` spec to a sample count."""
    if isinstance(value, float):
        return round(value * record_length)
    return value


def expand_per_channel(
    value: Any, channels: Sequence[str], *, key: str
) -> dict[str, Any]:
    """Expand a scalar-or-per-channel-mapping setting to ``{channel: value}``.

    Parameters
    ----------
    value : Any
        Either a single value (applied to every name in ``channels``) or a
        mapping from channel name to value.
    channels : sequence of str
        The channel names a scalar value applies to, and the valid keys for
        the mapping form.
    key : str
        The setting name, used in the error message.
    """
    if isinstance(value, Mapping):
        unknown = set(value) - set(channels)
        if unknown:
            raise ValueError(
                f"{key} given for unknown channel(s) {sorted(unknown)!r}; "
                f"expected a subset of {tuple(channels)!r}"
            )
        return dict(value)
    return {channel: value for channel in channels}


def normalize_trigger(value: Any) -> TriggerSettings:
    """Normalize a ``trigger`` mapping into a :class:`TriggerSettings`."""
    if not isinstance(value, Mapping):
        raise TypeError(f"trigger must be a mapping, got {type(value).__name__}")
    unknown = set(value) - _TRIGGER_KEYS
    if unknown:
        raise ValueError(
            f"unsupported trigger key(s) {sorted(unknown)!r}; expected a "
            f"subset of {sorted(_TRIGGER_KEYS)!r}"
        )
    level = value.get("level")
    return TriggerSettings(
        source=value.get("source"),
        level=None if level is None else float(level),
        slope=value.get("slope"),
    )


def normalize_physical(
    settings: Mapping[str, Any], *, channels: Sequence[str]
) -> PhysicalSettings:
    """Normalize the physical-vocabulary subset of a ``configure()`` mapping.

    Only inspects the keys in :data:`PHYSICAL_KEYS`; keys outside it are
    ignored, so callers must reject unknown keys and detect collisions with
    their own panel-native spellings *before* calling this.

    Parameters
    ----------
    settings : mapping of str to Any
        The full settings mapping passed to ``configure()``.
    channels : sequence of str
        The channel names a bare scalar ``range``/``offset``/``coupling``/
        ``impedance`` applies to, and the valid keys for a per-channel
        mapping form.
    """
    fields: dict[str, Any] = {}

    if "sample_rate" in settings:
        sample_rate = float(settings["sample_rate"])
        if sample_rate <= 0:
            raise ValueError(f"sample_rate must be positive, got {sample_rate!r}")
        fields["sample_rate"] = sample_rate

    if "record_length" in settings:
        record_length = settings["record_length"]
        if not isinstance(record_length, int):
            raise TypeError(
                f"record_length must be an int (samples), got "
                f"{type(record_length).__name__}"
            )
        if record_length <= 0:
            raise ValueError(f"record_length must be positive, got {record_length!r}")
        fields["record_length"] = record_length

    if "pretrigger" in settings:
        fields["pretrigger"] = normalize_pretrigger(settings["pretrigger"])

    if "channels" in settings:
        fields["channels"] = tuple(settings["channels"])

    for key in ("range", "offset", "coupling", "impedance"):
        if key in settings:
            fields[key] = expand_per_channel(settings[key], channels, key=key)

    if "trigger" in settings:
        fields["trigger"] = normalize_trigger(settings["trigger"])

    return PhysicalSettings(**fields)
