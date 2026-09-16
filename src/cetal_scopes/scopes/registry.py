"""Name-to-class lookup for the available instrument drivers.

Applications configure instruments by name (from a file, a form, or a command
line) rather than by importing a class, so the mapping lives here instead of
being hand-rolled in every downstream tool.
"""

from __future__ import annotations

from typing import Any

from cetal_scopes.scopes.base import Scope
from cetal_scopes.scopes.demo import DemoScope
from cetal_scopes.scopes.siglent import SiglentSDS6204L
from cetal_scopes.scopes.spectrum import SpectrumM5i3367

__all__ = ["DRIVERS", "create_scope", "driver_class"]

#: Driver name to class. Names are lowercase and stable: they appear in user
#: configuration files.
DRIVERS: dict[str, type[Scope]] = {
    "demo": DemoScope,
    "siglent_sds6204l": SiglentSDS6204L,
    "spectrum_m5i3367": SpectrumM5i3367,
}


def driver_class(driver: str) -> type[Scope]:
    """Return the :class:`~cetal_scopes.scopes.base.Scope` subclass named ``driver``.

    Parameters
    ----------
    driver : str
        A key of :data:`DRIVERS`, case-insensitive.

    Returns
    -------
    type of Scope
        The driver class.

    Raises
    ------
    ValueError
        If ``driver`` names no known driver.
    """
    key = driver.strip().lower()
    try:
        return DRIVERS[key]
    except KeyError:
        raise ValueError(
            f"unknown driver {driver!r}; expected one of {sorted(DRIVERS)!r}"
        ) from None


def create_scope(driver: str, /, **kwargs: Any) -> Scope:
    """Build an unconnected instance of the driver named ``driver``.

    Keyword arguments are passed to the driver's constructor, so they are
    driver-specific (``address`` for the Siglent, ``device`` for the M5i).

    Raises
    ------
    ValueError
        If ``driver`` names no known driver.
    """
    return driver_class(driver)(**kwargs)
