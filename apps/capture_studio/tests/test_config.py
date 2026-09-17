"""The TOML instrument inventory."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import cast

import pytest
from config import (
    InstrumentConfig,
    Inventory,
    build_specs,
    demo_inventory,
    dumps_toml,
    load_inventory,
    parse_duration,
    parse_inventory,
    save_inventory,
)

from cetal_scopes import DemoScope, SiglentSDS6204L, SpectrumM5i3367

BENCH = """
poll_interval = 0.1
default_timeout = 4.0

[[instrument]]
label = "siglent"
driver = "siglent_sds6204l"
address = "192.168.5.197"
channels = ["C1", "C2"]
timeout = 8.0

[instrument.settings]
sample_rate = 2.0e9
record_length = 100000
pretrigger = 0.5

[instrument.settings.trigger]
source = "C1"
level = 0.2
slope = "RISing"

[[instrument]]
label = "m5i"
driver = "spectrum_m5i3367"
address = "/dev/spcm0"
channels = ["CH0"]
direct_trigger = true

[instrument.settings.trigger]
source = "EXT"
level = 1.5
"""


def test_a_bench_file_is_parsed() -> None:
    inventory = parse_inventory(tomllib.loads(BENCH))

    assert [item.label for item in inventory.instruments] == ["siglent", "m5i"]
    assert inventory.poll_interval == 0.1
    assert inventory.default_timeout == 4.0
    assert inventory.instruments[0].settings["trigger"]["source"] == "C1"
    assert inventory.instruments[1].direct_trigger is True


def test_specs_build_the_right_drivers() -> None:
    specs = build_specs(parse_inventory(tomllib.loads(BENCH)))

    assert isinstance(specs[0].scope, SiglentSDS6204L)
    assert isinstance(specs[1].scope, SpectrumM5i3367)
    assert specs[0].timeout == 8.0
    assert specs[1].direct_trigger is True


def test_the_address_maps_to_each_driver_own_keyword() -> None:
    # The M5i takes a device node, not an address; the config should not have
    # to know that.
    specs = build_specs(parse_inventory(tomllib.loads(BENCH)))
    siglent = cast(SiglentSDS6204L, specs[0].scope)
    m5i = cast(SpectrumM5i3367, specs[1].scope)

    assert m5i._device == "/dev/spcm0"
    assert siglent._address == "192.168.5.197"


def test_channels_reach_both_the_constructor_and_configure() -> None:
    specs = build_specs(parse_inventory(tomllib.loads(BENCH)))
    assert cast(SiglentSDS6204L, specs[0].scope).channels == ("C1", "C2")
    assert specs[0].settings["channels"] == ["C1", "C2"]


def test_a_disabled_instrument_is_left_out_of_the_run() -> None:
    inventory = parse_inventory(tomllib.loads(BENCH))
    inventory.instruments[1].enabled = False

    assert [spec.label for spec in build_specs(inventory)] == ["siglent"]


def test_an_inventory_with_nothing_enabled_is_rejected() -> None:
    inventory = demo_inventory(1)
    inventory.instruments[0].enabled = False

    with pytest.raises(ValueError, match="no instruments are enabled"):
        build_specs(inventory)


def test_an_unknown_driver_is_rejected_by_name() -> None:
    with pytest.raises(ValueError, match="unknown driver"):
        InstrumentConfig(label="x", driver="tektronix")


def test_duplicate_labels_are_rejected() -> None:
    with pytest.raises(ValueError, match="labels must be unique"):
        Inventory(
            instruments=[
                InstrumentConfig(label="a", driver="demo"),
                InstrumentConfig(label="a", driver="demo"),
            ]
        )


def test_an_empty_label_is_rejected() -> None:
    with pytest.raises(ValueError, match="label must be non-empty"):
        InstrumentConfig(label="   ", driver="demo")


def test_a_non_positive_timeout_is_rejected() -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        InstrumentConfig(label="a", driver="demo", timeout=0.0)


def test_an_unknown_key_is_rejected() -> None:
    with pytest.raises(ValueError):
        InstrumentConfig(label="a", driver="demo", nonsense=1)  # type: ignore[call-arg]


def test_both_spellings_of_the_instrument_list_cannot_be_mixed() -> None:
    with pytest.raises(ValueError, match="not both"):
        parse_inventory({"instrument": [], "instruments": []})


def test_the_demo_inventory_builds_demo_scopes_with_different_skews() -> None:
    specs = build_specs(demo_inventory(3))

    assert len(specs) == 3
    assert all(isinstance(spec.scope, DemoScope) for spec in specs)
    skews = {cast(DemoScope, spec.scope)._skew for spec in specs}
    assert len(skews) == 3  # so the shot genuinely needs aligning


def test_the_demo_inventory_needs_at_least_one_instrument() -> None:
    with pytest.raises(ValueError, match="at least one instrument"):
        demo_inventory(0)


def test_toml_round_trips_through_the_writer() -> None:
    original = parse_inventory(tomllib.loads(BENCH))
    assert parse_inventory(tomllib.loads(dumps_toml(original))) == original


def test_the_demo_inventory_round_trips_too() -> None:
    original = demo_inventory(2)
    assert parse_inventory(tomllib.loads(dumps_toml(original))) == original


def test_saving_and_loading_a_file(tmp_path: Path) -> None:
    original = parse_inventory(tomllib.loads(BENCH))
    path = save_inventory(original, tmp_path / "bench.toml")

    assert load_inventory(path) == original


def test_loading_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no inventory at"):
        load_inventory(tmp_path / "nope.toml")


def test_invalid_toml_is_reported_with_the_path(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("this is not = = toml")

    with pytest.raises(ValueError, match="is not valid TOML"):
        load_inventory(path)


# --- a blank field means "as high as this instrument goes" ------------------


def instrument(
    driver: str, channels: list[str], **settings: object
) -> InstrumentConfig:
    return InstrumentConfig(
        label="x", driver=driver, channels=channels, settings=dict(settings)
    )


def test_an_absent_sample_rate_resolves_to_the_drivers_ceiling() -> None:
    resolved = instrument("siglent_sds6204l", ["C1"]).resolved_settings()
    assert resolved["sample_rate"] == 5e9


def test_a_given_sample_rate_is_left_alone() -> None:
    resolved = instrument(
        "siglent_sds6204l", ["C1"], sample_rate=1e9
    ).resolved_settings()
    assert resolved["sample_rate"] == 1e9


def test_the_siglent_ceiling_does_not_drop_with_more_channels() -> None:
    """Four independent converters: using all of them costs no rate."""
    one = instrument("siglent_sds6204l", ["C1"]).resolved_settings()
    four = instrument("siglent_sds6204l", ["C1", "C2", "C3", "C4"]).resolved_settings()
    assert one["sample_rate"] == four["sample_rate"] == 5e9


def test_dropping_a_channel_raises_the_m5i_ceiling() -> None:
    """The card interleaves, so the second channel costs half the rate."""
    two = instrument("spectrum_m5i3367", ["CH0", "CH1"]).resolved_settings()
    one = instrument("spectrum_m5i3367", ["CH0"]).resolved_settings()
    assert two["sample_rate"] == 5e9
    assert one["sample_rate"] == 10e9


def test_an_absent_record_length_resolves_to_the_shortest_record() -> None:
    """Not the deepest: 1 Gpt is a 200 ms window and gigabytes of transfer."""
    resolved = instrument("siglent_sds6204l", ["C1"]).resolved_settings()
    assert resolved["record_length"] == 10_000


def test_a_window_becomes_a_record_length_at_the_resolved_rate() -> None:
    resolved = instrument("siglent_sds6204l", ["C1"], window=50e-6).resolved_settings()
    assert resolved["sample_rate"] == 5e9
    assert resolved["record_length"] == 250_000


def test_a_window_holds_while_a_dropped_channel_buys_samples() -> None:
    """The point of a window: a faster rate fills it more finely."""
    two = instrument("spectrum_m5i3367", ["CH0", "CH1"], window=50e-6)
    one = instrument("spectrum_m5i3367", ["CH0"], window=50e-6)
    assert two.resolved_settings()["record_length"] == 250_000
    assert one.resolved_settings()["record_length"] == 500_000


def test_window_is_not_passed_on_to_the_driver() -> None:
    """It is our vocabulary, not the driver's; configure() would reject it."""
    resolved = instrument("siglent_sds6204l", ["C1"], window=1e-6).resolved_settings()
    assert "window" not in resolved


def test_window_and_record_length_together_are_refused() -> None:
    config = instrument("siglent_sds6204l", ["C1"], window=1e-6, record_length=100)
    with pytest.raises(ValueError, match="not both"):
        config.resolved_settings()


def test_a_window_needs_a_rate_the_driver_can_report() -> None:
    config = instrument("demo", ["CH1"], window=1e-6)
    with pytest.raises(ValueError, match="cannot report its own ceiling"):
        config.resolved_settings()


def test_a_driver_that_knows_no_ceiling_leaves_the_keys_absent() -> None:
    """Better an untouched instrument than a guessed rate."""
    resolved = instrument("demo", ["CH1"]).resolved_settings()
    assert "sample_rate" not in resolved
    assert "record_length" not in resolved


def test_resolution_reaches_the_driver_through_build() -> None:
    spec = instrument("spectrum_m5i3367", ["CH0"]).build()
    assert spec.settings["sample_rate"] == 10e9


def test_pretrigger_without_a_window_names_the_instrument_and_the_fix() -> None:
    """A driver with no ceilings cannot fill the window in, so say so here."""
    config = InstrumentConfig(
        label="demo1", driver="demo", channels=["CH1"], settings={"pretrigger": 0.5}
    )
    with pytest.raises(ValueError, match="demo1: 'pretrigger' needs a window"):
        config.resolved_settings()


def test_pretrigger_is_fine_when_the_driver_fills_the_window_in() -> None:
    config = InstrumentConfig(
        label="siglent",
        driver="siglent_sds6204l",
        channels=["C1"],
        settings={"pretrigger": 0.5},
    )
    assert config.resolved_settings()["sample_rate"] == 5e9


# --- a record length written as a duration ---------------------------------


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("50us", 50e-6),
        ("50 us", 50e-6),
        ("50µs", 50e-6),
        ("50μs", 50e-6),
        ("1.5ms", 1.5e-3),
        ("200ns", 200e-9),
        ("2e-6 s", 2e-6),
        ("1S", 1.0),
    ],
)
def test_parse_duration_reads_the_usual_spellings(text: str, seconds: float) -> None:
    assert parse_duration(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", ["50 furlongs", "fifty us", "us", "", "50"])
def test_parse_duration_refuses_what_it_cannot_read(text: str) -> None:
    with pytest.raises(ValueError, match="as a duration"):
        parse_duration(text)


def test_a_suffixed_record_length_becomes_a_window() -> None:
    resolved = instrument(
        "siglent_sds6204l", ["C1"], record_length="50us"
    ).resolved_settings()
    assert resolved["record_length"] == 250_000


def test_a_bare_record_length_is_still_a_sample_count() -> None:
    resolved = instrument(
        "siglent_sds6204l", ["C1"], record_length=250_000
    ).resolved_settings()
    assert resolved["record_length"] == 250_000


def test_a_suffixed_record_length_holds_the_window_across_a_rate_change() -> None:
    two = instrument("spectrum_m5i3367", ["CH0", "CH1"], record_length="50us")
    one = instrument("spectrum_m5i3367", ["CH0"], record_length="50us")
    assert two.resolved_settings()["record_length"] == 250_000
    assert one.resolved_settings()["record_length"] == 500_000


def test_a_negative_duration_is_refused() -> None:
    config = instrument("siglent_sds6204l", ["C1"], record_length="-3us")
    with pytest.raises(ValueError, match="must be positive"):
        config.resolved_settings()
