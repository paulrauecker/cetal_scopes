import math
import tomllib
from pathlib import Path

import numpy as np
import pytest

from cetal_scopes import Antenna
from cetal_scopes.antennas import (
    PBS1_PROBES,
    AntennaCatalog,
    AntennaEntry,
    load_antenna_catalog,
    parse_antenna_catalog,
)

CATALOGUE = """
[antenna.bdot-large]
turns = 2
radius = 0.01
f_max = 100e6

[antenna.bdot-small]
sensitivity = 2.0e-5
f_min = 1e3
f_max = 500e6
notes = "Helmholtz, 2026-10"

[antenna.bdot-spare]
kind = "b-dot"
"""


def test_the_builtin_probes_are_always_there() -> None:
    catalog = AntennaCatalog()

    assert sorted(catalog) == sorted(f"PBS-{model}" for model in PBS1_PROBES)
    h3 = catalog["PBS-H3"]
    assert h3.transfer_function is not None
    assert h3.transfer_function.f_max == 50e6
    assert h3.gain_at(1e6).real == pytest.approx(PBS1_PROBES["H3"].gain)


def test_a_file_adds_probes_beside_the_builtins() -> None:
    catalog = parse_antenna_catalog(tomllib.loads(CATALOGUE))

    assert {"bdot-large", "bdot-small", "bdot-spare", "PBS-H3"} <= set(catalog)
    large = catalog["bdot-large"]
    assert large.name == "bdot-large"
    assert large.kind == "b-dot"
    assert large.gain_at(1e6).real == pytest.approx(2 * math.pi * 0.01**2)
    small = catalog["bdot-small"]
    assert small.transfer_function is not None
    assert small.transfer_function.f_min == 1e3
    assert small.gain_at(1e6).real == pytest.approx(2.0e-5)


def test_an_uncalibrated_probe_is_still_an_antenna() -> None:
    spare = parse_antenna_catalog(tomllib.loads(CATALOGUE))["bdot-spare"]

    assert spare.transfer_function is None
    with pytest.raises(ValueError, match="no transfer function"):
        spare.gain_at(1e6)


def test_area_is_an_alternative_to_radius() -> None:
    entry = AntennaEntry(turns=3, area=1e-4, f_max=1e8)

    assert entry.gain == pytest.approx(3e-4)


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({"sensitivity": 1e-5, "turns": 1, "area": 1e-4, "f_max": 1e8}, "not both"),
        ({"sensitivity": -1.0, "f_max": 1e8}, "positive"),
        ({"turns": 1, "f_max": 1e8}, "exactly one of"),
        ({"turns": 1, "area": 1e-4, "radius": 0.01, "f_max": 1e8}, "exactly one"),
        ({"turns": 0, "area": 1e-4, "f_max": 1e8}, "turns"),
        ({"area": 1e-4, "f_max": 1e8}, "turns"),
        ({"sensitivity": 1e-5}, "f_max"),
        ({"f_max": 1e8}, "without a gain"),
        ({"sensitivity": 1e-5, "f_min": 2e8, "f_max": 1e8}, "f_min < f_max"),
        ({"sensitivity": 1e-5, "f_max": 1e8, "gain": 2}, "Extra inputs"),
    ],
)
def test_bad_entries_are_rejected(fields: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        AntennaEntry.model_validate(fields)


def test_a_bad_entry_is_reported_by_name() -> None:
    with pytest.raises(ValueError, match="'broken'"):
        parse_antenna_catalog({"antenna": {"broken": {"sensitivity": 1e-5}}})


def test_builtin_names_cannot_be_redefined() -> None:
    with pytest.raises(ValueError, match="PBS-H3"):
        AntennaCatalog({"PBS-H3": Antenna(name="PBS-H3")})


def test_only_antenna_tables_are_accepted() -> None:
    with pytest.raises(ValueError, match="unexpected top-level"):
        parse_antenna_catalog({"antennas": {}})


def test_resolve_places_a_probe_without_touching_the_catalogue() -> None:
    catalog = AntennaCatalog()

    placed = catalog.resolve("PBS-H3", axis=(0, 0, 2), delay=3e-9)

    assert placed.delay == 3e-9
    np.testing.assert_allclose(placed.unit_axis, [0.0, 0.0, 1.0])
    assert placed.transfer_function is catalog["PBS-H3"].transfer_function
    assert catalog["PBS-H3"].delay == 0.0
    assert catalog.resolve("PBS-H3") is catalog["PBS-H3"]


def test_resolve_names_the_catalogue_on_a_miss() -> None:
    with pytest.raises(ValueError, match=r"unknown antenna 'PBS-H9'.*PBS-H3"):
        AntennaCatalog().resolve("PBS-H9")


def test_a_catalogue_file_is_loaded(tmp_path: Path) -> None:
    path = tmp_path / "antennas.toml"
    path.write_text(CATALOGUE, encoding="utf-8")

    assert "bdot-large" in load_antenna_catalog(path)


def test_loading_reports_missing_and_malformed_files(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_antenna_catalog(tmp_path / "absent.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[antenna.x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid TOML"):
        load_antenna_catalog(bad)
    invalid = tmp_path / "invalid.toml"
    invalid.write_text("[antenna.x]\nf_max = 1e8\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"invalid\.toml: antenna 'x'"):
        load_antenna_catalog(invalid)
