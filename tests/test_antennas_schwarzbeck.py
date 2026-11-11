import math

import numpy as np
import pytest

from cetal_scopes.antennas import USLP9143_CALIBRATION, AntennaCatalog, uslp9143


def test_factory_builds_calibrated_antenna() -> None:
    antenna = uslp9143(axis=(0.0, 1.0, 0.0), delay=4.0e-9)
    assert antenna.name == "USLP-9143"
    assert antenna.kind == "e-field"
    assert antenna.delay == 4.0e-9
    transfer = antenna.transfer_function
    assert transfer is not None
    assert transfer.unit == "V/(V/m)"
    assert (transfer.f_min, transfer.f_max) == (0.3e9, 7.0e9)
    assert np.isnan(antenna.gain_at(0.2e9))
    assert np.isnan(antenna.gain_at(8.0e9))


@pytest.mark.parametrize(("freq", "gain_dbi", "af"), USLP9143_CALIBRATION)
def test_label_points_are_exact(freq: float, gain_dbi: float, af: float) -> None:
    gain = uslp9143().gain_at(freq)
    assert gain.imag == 0.0
    assert -20.0 * math.log10(gain.real) == pytest.approx(af, abs=1e-9)


@pytest.mark.parametrize(("freq", "gain_dbi", "af"), USLP9143_CALIBRATION)
def test_label_columns_agree(freq: float, gain_dbi: float, af: float) -> None:
    # Far-field AF of a 50 ohm antenna from its gain.
    assert 20.0 * math.log10(freq / 1.0e6) - gain_dbi - 29.79 == pytest.approx(
        af, abs=0.1
    )


def test_resampling_follows_the_far_field_slope() -> None:
    # Between label points the AF follows 20 log10(f), not a straight line in
    # linear gain: at 0.5 GHz with gi interpolated in log f, AF is ~18.1 dB/m.
    gain = uslp9143().gain_at(0.5e9).real
    assert -20.0 * math.log10(gain) == pytest.approx(18.0, abs=0.2)


def test_it_is_built_in() -> None:
    catalog = AntennaCatalog()
    assert catalog["USLP-9143"].kind == "e-field"
