"""The instrument inventory: a TOML file describing what is on the bench.

A run is defined by a file rather than by whatever was typed into a form, so a
bench setup is reproducible and reviewable. The UI edits the same structure in
memory and can write it back.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cetal_scopes.acquisition import InstrumentSpec
from cetal_scopes.scopes.registry import DRIVERS, create_scope

__all__ = [
    "InstrumentConfig",
    "Inventory",
    "build_specs",
    "demo_inventory",
    "dumps_toml",
    "load_inventory",
    "parse_inventory",
    "save_inventory",
]


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

    def build(self) -> InstrumentSpec:
        """Instantiate the driver and wrap it in an :class:`InstrumentSpec`."""
        kwargs: dict[str, Any] = dict(self.options)
        if self.channels:
            kwargs["channels"] = tuple(self.channels)
        if self.address is not None:
            kwargs[_address_keyword(self.driver)] = self.address

        settings = dict(self.settings)
        if self.channels:
            # Keep configure() consistent with the constructor: a driver that
            # takes channels both ways must not be told two different things.
            settings.setdefault("channels", list(self.channels))

        return InstrumentSpec(
            label=self.label,
            scope=create_scope(self.driver, **kwargs),
            settings=settings,
            timeout=self.timeout,
            direct_trigger=self.direct_trigger,
            required=self.required,
        )


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
