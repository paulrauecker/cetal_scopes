import pytest

from cetal_scopes.scopes._settings import (
    PHYSICAL_KEYS,
    PhysicalSettings,
    TriggerSettings,
    expand_per_channel,
    normalize_physical,
    normalize_pretrigger,
    normalize_trigger,
    pretrigger_samples,
)


def test_physical_keys_contents() -> None:
    assert PHYSICAL_KEYS == {
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


class TestNormalizePretrigger:
    def test_accepts_nonnegative_int_samples(self) -> None:
        assert normalize_pretrigger(0) == 0
        assert normalize_pretrigger(1024) == 1024

    def test_rejects_negative_int(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            normalize_pretrigger(-1)

    def test_accepts_fraction_in_bounds(self) -> None:
        assert normalize_pretrigger(0.0) == 0.0
        assert normalize_pretrigger(0.5) == 0.5
        assert normalize_pretrigger(1.0) == 1.0

    def test_rejects_fraction_out_of_bounds(self) -> None:
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            normalize_pretrigger(1.5)
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            normalize_pretrigger(-0.1)

    def test_rejects_wrong_type(self) -> None:
        with pytest.raises(TypeError, match="int.*or.*float"):
            normalize_pretrigger("0.5")  # type: ignore[arg-type]


class TestPretriggerSamples:
    def test_int_passes_through(self) -> None:
        assert pretrigger_samples(256, 4096) == 256

    def test_fraction_scales_and_rounds(self) -> None:
        assert pretrigger_samples(0.25, 4096) == 1024
        assert pretrigger_samples(0.5, 1000) == 500


class TestExpandPerChannel:
    def test_scalar_applies_to_all_channels(self) -> None:
        result = expand_per_channel(1.0, ("CH0", "CH1"), key="range")
        assert result == {"CH0": 1.0, "CH1": 1.0}

    def test_mapping_form_is_copied(self) -> None:
        given = {"CH0": 1.0}
        result = expand_per_channel(given, ("CH0", "CH1"), key="range")
        assert result == {"CH0": 1.0}
        assert result is not given

    def test_mapping_rejects_unknown_channel(self) -> None:
        with pytest.raises(ValueError, match="unknown channel"):
            expand_per_channel({"CH9": 1.0}, ("CH0", "CH1"), key="range")


class TestNormalizeTrigger:
    def test_full_mapping(self) -> None:
        result = normalize_trigger({"source": "CH0", "level": 1, "slope": "POS"})
        assert result == TriggerSettings(source="CH0", level=1.0, slope="POS")

    def test_partial_mapping_defaults_to_none(self) -> None:
        result = normalize_trigger({"source": "CH0"})
        assert result == TriggerSettings(source="CH0", level=None, slope=None)

    def test_rejects_non_mapping(self) -> None:
        with pytest.raises(TypeError, match="mapping"):
            normalize_trigger("CH0")  # type: ignore[arg-type]

    def test_rejects_unknown_key(self) -> None:
        with pytest.raises(ValueError, match="unsupported trigger key"):
            normalize_trigger({"nonsense": 1})


class TestNormalizePhysical:
    def test_empty_settings_yields_all_none(self) -> None:
        result = normalize_physical({}, channels=("CH0",))
        assert result == PhysicalSettings()

    def test_ignores_non_physical_keys(self) -> None:
        result = normalize_physical(
            {"timebase": 1e-3, "sample_rate": 1e9}, channels=("CH0",)
        )
        assert result.sample_rate == 1e9
        assert result.record_length is None

    def test_sample_rate_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            normalize_physical({"sample_rate": 0}, channels=("CH0",))

    def test_record_length_type_and_range(self) -> None:
        with pytest.raises(TypeError, match="int"):
            normalize_physical({"record_length": 1.5}, channels=("CH0",))
        with pytest.raises(ValueError, match="positive"):
            normalize_physical({"record_length": 0}, channels=("CH0",))
        result = normalize_physical({"record_length": 4096}, channels=("CH0",))
        assert result.record_length == 4096

    def test_channels_becomes_tuple(self) -> None:
        result = normalize_physical({"channels": ["CH0", "CH1"]}, channels=("CH0",))
        assert result.channels == ("CH0", "CH1")

    def test_range_offset_coupling_impedance_expand_per_channel(self) -> None:
        result = normalize_physical(
            {
                "range": 1.0,
                "offset": {"CH0": 0.1, "CH1": -0.1},
                "coupling": "DC",
                "impedance": 50,
            },
            channels=("CH0", "CH1"),
        )
        assert result.range == {"CH0": 1.0, "CH1": 1.0}
        assert result.offset == {"CH0": 0.1, "CH1": -0.1}
        assert result.coupling == {"CH0": "DC", "CH1": "DC"}
        assert result.impedance == {"CH0": 50, "CH1": 50}

    def test_pretrigger_and_trigger_are_delegated(self) -> None:
        result = normalize_physical(
            {"pretrigger": 0.25, "trigger": {"source": "CH0", "level": 0.1}},
            channels=("CH0",),
        )
        assert result.pretrigger == 0.25
        assert result.trigger == TriggerSettings(source="CH0", level=0.1, slope=None)
