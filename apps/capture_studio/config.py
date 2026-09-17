"""The instrument inventory: a TOML file describing what is on the bench.

A run is defined by a file rather than by whatever was typed into a form, so a
bench setup is reproducible and reviewable. The UI edits the same structure in
memory and can write it back.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cetal_scopes.acquisition import InstrumentSpec
from cetal_scopes.scopes.registry import DRIVERS, create_scope, driver_class
from cetal_scopes.scopes.siglent import VDIV_LADDER

__all__ = [
    "VERTICAL_DRIVERS",
    "InstrumentConfig",
    "Inventory",
    "build_specs",
    "demo_inventory",
    "dumps_toml",
    "load_inventory",
    "parse_duration",
    "parse_inventory",
    "save_inventory",
]

#: Drivers whose ``configure()`` takes a per-channel ``vertical`` mapping, so
#: a channel can be given a true panel V/div instead of the SI ``range``, plus
#: the V/div ladder that driver snaps to. The two spellings write the same
#: instrument setting, and the driver rejects being given both for one channel
#: -- there is no honest precedence rule between them -- so the UI offers one
#: or the other per channel, never both. Drivers with no panel vertical
#: vocabulary (the M5i) are simply absent, and take ``range`` alone.
VERTICAL_DRIVERS: dict[str, dict[str, Any]] = {
    "siglent_sds6204l": {"vdiv_ladder": list(VDIV_LADDER), "divisions": 8},
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InstrumentConfig(_Model):
    """One instrument's entry in the inventory.

    Attributes
    ----------
    label : str
        Unique name; becomes the capture's key in the shot.
    driver : str
        A key of :data:`cetal_scopes.scopes.registry.DRIVERS`.
    address : str, optional
        Passed to the driver as its address argument -- an IP for the Siglent,
        a device node for the M5i. Ignored by drivers that take neither.
    channels : list of str, optional
        Channels to acquire.
    settings : dict, optional
        Passed straight to the driver's ``configure()``: the shared physical
        vocabulary (``sample_rate``, ``record_length``, ``pretrigger``,
        ``range``, ``trigger``, ...) plus any panel-native keys that driver
        accepts.
    timeout : float, optional
        Seconds to wait for this instrument's trigger.
    direct_trigger : bool, optional
        Force this instrument in software once every instrument is armed.
    required : bool, optional
        Whether this instrument failing makes the shot incomplete.
    enabled : bool, optional
        Set ``False`` to keep an instrument in the file but out of the run.
    options : dict, optional
        Extra keyword arguments for the driver's constructor.
    """

    label: str
    driver: str
    address: str | None = None
    channels: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
    timeout: float | None = None
    direct_trigger: bool = False
    required: bool = True
    enabled: bool = True
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("label")
    @classmethod
    def _label_is_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("label must be non-empty")
        return value.strip()

    @field_validator("driver")
    @classmethod
    def _driver_is_known(cls, value: str) -> str:
        key = value.strip().lower()
        if key not in DRIVERS:
            raise ValueError(
                f"unknown driver {value!r}; expected one of {sorted(DRIVERS)}"
            )
        return key

    @field_validator("timeout")
    @classmethod
    def _timeout_is_positive(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError(f"timeout must be positive, got {value!r}")
        return value

    def resolved_settings(self) -> dict[str, Any]:
        """The settings as the driver will see them, ceilings filled in.

        In an inventory -- unlike a bare ``configure()`` call, where an absent
        key means "leave this alone" -- the file is a complete description of
        what the bench should be doing. So an absent ``sample_rate`` or
        ``record_length`` means "as high as this instrument goes", answered by
        the driver itself (:meth:`~cetal_scopes.scopes.base.Scope.max_sample_rate`)
        rather than by a table duplicated here. A driver that does not know
        its own ceiling leaves the key absent, and the instrument keeps
        whatever it was already set to.

        The ceiling depends on the channel count, so dropping a channel from
        an interleaved digitiser raises the rate on the next connect with no
        other edit -- which is the point.

        ``window`` (seconds) is resolved here rather than sent on: it is how
        you say "this much time, at whatever rate the instrument can manage",
        which is the useful way round when rate matters more than record
        length. It becomes ``record_length = window * sample_rate`` once the
        rate is known, so raising the rate buys more samples over the same
        window instead of a shorter one.

        With neither ``record_length`` nor ``window`` given, the *shortest*
        record the instrument takes is used. Record length buys no resolution
        once the rate is at its ceiling -- it only sets how much time the shot
        covers, and every extra sample costs transfer time on every shot.
        """
        settings = dict(self.settings)
        if self.channels:
            # Keep configure() consistent with the constructor: a driver that
            # takes channels both ways must not be told two different things.
            settings.setdefault("channels", list(self.channels))

        window = settings.pop("window", None)
        if window is not None:
            window = _as_window(window, label=self.label, field="window")

        # A record length written with a time suffix -- record_length = "50us"
        # -- is a window, said in the place people reach for first.
        length = settings.get("record_length")
        if isinstance(length, str):
            as_time = _as_window(length, label=self.label, field="record_length")
            if window is not None:
                raise ValueError(
                    f"{self.label}: 'record_length' is a duration "
                    f"({length!r}) and 'window' is also set; give one."
                )
            window = as_time
            del settings["record_length"]

        if window is not None and "record_length" in settings:
            raise ValueError(
                f"{self.label}: give 'window' or 'record_length', not both -- "
                "the window is the record length divided by the sample rate."
            )

        driver = driver_class(self.driver)
        n_channels = len(self.channels)
        if "sample_rate" not in settings:
            ceiling = driver.max_sample_rate(n_channels)
            if ceiling is not None:
                settings["sample_rate"] = ceiling

        if window is not None:
            rate = settings.get("sample_rate")
            if rate is None:
                raise ValueError(
                    f"{self.label}: 'window' needs a sample rate to become a "
                    f"record length, and driver {self.driver!r} cannot report "
                    "its own ceiling. Set 'sample_rate', or give "
                    "'record_length' instead of 'window'."
                )
            settings["record_length"] = max(1, round(window * float(rate)))
        elif "record_length" not in settings:
            shortest = driver.min_record_length(n_channels)
            if shortest is not None:
                settings["record_length"] = shortest

        # A window is what pretrigger is a fraction *of*, so asking for one
        # without it is not something the instrument can act on. The driver
        # says so too, but only at connect time and without naming the file
        # or the instrument, which reads like a bug rather than a setting.
        if "pretrigger" in settings and not (
            "sample_rate" in settings or "record_length" in settings
        ):
            raise ValueError(
                f"{self.label}: 'pretrigger' needs a window to be a fraction "
                f"of, but driver {self.driver!r} cannot report its own "
                "sample-rate or record-length ceiling, so neither could be "
                "filled in. Set 'sample_rate' and 'record_length' explicitly."
            )
        return settings

    def build(self) -> InstrumentSpec:
        """Instantiate the driver and wrap it in an :class:`InstrumentSpec`."""
        kwargs: dict[str, Any] = dict(self.options)
        if self.channels:
            kwargs["channels"] = tuple(self.channels)
        if self.address is not None:
            kwargs[_address_keyword(self.driver)] = self.address

        settings = self.resolved_settings()

        return InstrumentSpec(
            label=self.label,
            scope=create_scope(self.driver, **kwargs),
            settings=settings,
            timeout=self.timeout,
            direct_trigger=self.direct_trigger,
            required=self.required,
        )


#: Time suffixes accepted wherever a duration is written as a string, and
#: what each is worth in seconds. Both spellings of "micro" are here because
#: one of them is what a keyboard produces and the other is what physics
#: writes; neither should be a syntax error.
_TIME_UNITS: dict[str, float] = {
    "s": 1.0,
    "ms": 1e-3,
    "us": 1e-6,
    "\u00b5s": 1e-6,  # MICRO SIGN
    "\u03bcs": 1e-6,  # GREEK SMALL LETTER MU
    "ns": 1e-9,
    "ps": 1e-12,
}

_DURATION_RE = re.compile(
    r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-z\u00b5\u03bc]+)\s*$",
    re.IGNORECASE,
)


def parse_duration(value: str) -> float:
    """Seconds from a string like ``"50us"``, ``"1.5 ms"`` or ``"2e-6 s"``.

    Raises
    ------
    ValueError
        If the string is not a number followed by a known time unit.
    """
    match = _DURATION_RE.match(value)
    unit = match.group(2).lower() if match else None
    if match is None or unit not in _TIME_UNITS:
        raise ValueError(
            f"cannot read {value!r} as a duration; expected a number and one "
            f"of {sorted(_TIME_UNITS)} (e.g. '50us')"
        )
    return float(match.group(1)) * _TIME_UNITS[unit]


def _as_window(value: Any, *, label: str, field: str) -> float:
    """A duration in seconds from a number or a suffixed string."""
    seconds = parse_duration(value) if isinstance(value, str) else float(value)
    if seconds <= 0:
        raise ValueError(f"{label}: {field!r} must be positive, got {value!r}")
    return seconds


def _address_keyword(driver: str) -> str:
    """Which constructor argument this driver's ``address`` maps to."""
    if driver == "spectrum_m5i3367":
        return "device"
    if driver == "demo":
        return "label"
    return "address"


class Inventory(_Model):
    """The whole bench: every instrument, plus run-wide defaults."""

    instruments: list[InstrumentConfig] = Field(default_factory=list)
    poll_interval: float = 0.2
    default_timeout: float = 10.0
    arm_timeout: float = 5.0

    @field_validator("instruments")
    @classmethod
    def _labels_are_unique(
        cls, value: list[InstrumentConfig]
    ) -> list[InstrumentConfig]:
        labels = [item.label for item in value]
        duplicates = sorted({label for label in labels if labels.count(label) > 1})
        if duplicates:
            raise ValueError(f"instrument labels must be unique: {duplicates}")
        return value

    @property
    def enabled(self) -> list[InstrumentConfig]:
        """The instruments that will take part in a shot."""
        return [item for item in self.instruments if item.enabled]


def load_inventory(path: str | Path) -> Inventory:
    """Read an inventory from a TOML file.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ValueError
        If the file is not valid TOML, or does not describe a valid inventory.
    """
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"no inventory at {target}")
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{target} is not valid TOML: {exc}") from exc
    return parse_inventory(raw)


def parse_inventory(raw: Mapping[str, Any]) -> Inventory:
    """Build an :class:`Inventory` from a parsed TOML mapping.

    Accepts both the array-of-tables spelling used in files
    (``[[instrument]]``) and the plain ``instruments`` list used over HTTP.
    """
    data = dict(raw)
    if "instrument" in data:
        if "instruments" in data:
            raise ValueError("give either [[instrument]] or 'instruments', not both")
        data["instruments"] = data.pop("instrument")
    return Inventory.model_validate(data)


def build_specs(inventory: Inventory) -> list[InstrumentSpec]:
    """Instantiate every enabled instrument.

    Raises
    ------
    ValueError
        If no instrument is enabled, since a shot needs at least one.
    """
    enabled = inventory.enabled
    if not enabled:
        raise ValueError("no instruments are enabled")
    return [item.build() for item in enabled]


def demo_inventory(n_instruments: int = 2, *, channels_each: int = 2) -> Inventory:
    """An inventory of synthetic instruments, for running with no hardware.

    Each instrument is given a different trigger skew, so the shot genuinely
    needs time alignment rather than arriving pre-aligned.
    """
    if n_instruments < 1:
        raise ValueError(f"need at least one instrument, got {n_instruments!r}")
    instruments = []
    for index in range(n_instruments):
        label = f"demo{index + 1}"
        instruments.append(
            InstrumentConfig(
                label=label,
                driver="demo",
                address=label,
                channels=[f"CH{n + 1}" for n in range(channels_each)],
                settings={
                    "sample_rate": 1e9 / (index + 1),
                    "record_length": 4096,
                    "pretrigger": 0.3,
                    "frequency": 2e7,
                },
                options={
                    "skew": index * 7.5e-9,
                    "trigger_delay": 0.02 * (index + 1),
                    "seed": index,
                },
                direct_trigger=True,
            )
        )
    return Inventory(instruments=instruments)


def save_inventory(inventory: Inventory, path: str | Path) -> Path:
    """Write ``inventory`` back to ``path`` as TOML."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dumps_toml(inventory), encoding="utf-8")
    return target


def dumps_toml(inventory: Inventory) -> str:
    """Render an inventory as TOML.

    The standard library reads TOML but does not write it, and the format here
    is small and fixed, so this emits it directly rather than adding a
    dependency.
    """
    lines = [
        "# cetal-scopes capture studio inventory",
        f"poll_interval = {_scalar(inventory.poll_interval)}",
        f"default_timeout = {_scalar(inventory.default_timeout)}",
        f"arm_timeout = {_scalar(inventory.arm_timeout)}",
    ]
    for item in inventory.instruments:
        lines.append("")
        lines.append("[[instrument]]")
        lines.append(f"label = {_scalar(item.label)}")
        lines.append(f"driver = {_scalar(item.driver)}")
        if item.address is not None:
            lines.append(f"address = {_scalar(item.address)}")
        if item.channels:
            lines.append(f"channels = {_scalar(item.channels)}")
        if item.timeout is not None:
            lines.append(f"timeout = {_scalar(item.timeout)}")
        if item.direct_trigger:
            lines.append("direct_trigger = true")
        if not item.required:
            lines.append("required = false")
        if not item.enabled:
            lines.append("enabled = false")
        lines.extend(_table(f"instrument.{'options'}", item.options))
        lines.extend(_table("instrument.settings", item.settings))
    return "\n".join(lines) + "\n"


def _table(name: str, values: Mapping[str, Any]) -> list[str]:
    """Render a sub-table, recursing for nested mappings (e.g. ``trigger``)."""
    if not values:
        return []
    scalars = {k: v for k, v in values.items() if not isinstance(v, Mapping)}
    nested = {k: v for k, v in values.items() if isinstance(v, Mapping)}
    lines: list[str] = []
    if scalars:
        lines.append("")
        lines.append(f"[{name}]")
        lines.extend(f"{key} = {_scalar(value)}" for key, value in scalars.items())
    for key, value in nested.items():
        lines.extend(_table(f"{name}.{key}", value))
    return lines


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, Sequence):
        return "[" + ", ".join(_scalar(item) for item in value) + "]"
    raise TypeError(f"cannot render {value!r} as TOML")
