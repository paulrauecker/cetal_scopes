import numpy as np
import pytest

from cetal_scopes import Capture, Channel, Shot
from cetal_scopes.analysis import TimeOffset, align_shot, estimate_time_offset


def make_channel(volts: np.ndarray, *, dt: float = 1.0, name: str = "CH1") -> Channel:
    return Channel(name=name, volts=np.asarray(volts, dtype=np.float64), t0=0.0, dt=dt)


def fourier_shift(values: np.ndarray, shift: float) -> np.ndarray:
    spectrum = np.fft.rfft(values)
    freqs = np.fft.rfftfreq(values.size)
    return np.fft.irfft(spectrum * np.exp(-2j * np.pi * freqs * shift), n=values.size)


def test_recovers_integer_delay() -> None:
    rng = np.random.default_rng(0)
    reference = make_channel(rng.standard_normal(2000))
    signal = make_channel(np.roll(reference.volts, 37))
    result = estimate_time_offset(reference, signal)
    assert isinstance(result, TimeOffset)
    assert result.offset == pytest.approx(-37.0, abs=0.05)
    assert result.correlation > 0.9
    assert result.inverted is False


def test_recovers_negative_delay() -> None:
    rng = np.random.default_rng(1)
    reference = make_channel(rng.standard_normal(2000))
    signal = make_channel(np.roll(reference.volts, -20))
    result = estimate_time_offset(reference, signal)
    assert result.offset == pytest.approx(20.0, abs=0.05)


def test_recovers_fractional_delay() -> None:
    rng = np.random.default_rng(2)
    reference = make_channel(rng.standard_normal(4096))
    signal = make_channel(fourier_shift(reference.volts, 12.5))
    result = estimate_time_offset(reference, signal)
    assert result.offset == pytest.approx(-12.5, abs=0.2)


def test_detects_inversion() -> None:
    rng = np.random.default_rng(3)
    reference = make_channel(rng.standard_normal(2000))
    signal = make_channel(-np.roll(reference.volts, 10))
    result = estimate_time_offset(reference, signal)
    assert result.inverted is True
    assert result.correlation < 0
    assert result.offset == pytest.approx(-10.0, abs=0.05)


def test_mismatched_sample_rates_are_resampled() -> None:
    rng = np.random.default_rng(4)
    dt = 1.0e-9
    reference = make_channel(rng.standard_normal(4096), dt=dt)
    decimated = fourier_shift(reference.volts, 5.5)[::2]
    signal = make_channel(decimated, dt=2.0 * dt)
    result = estimate_time_offset(reference, signal)
    assert result.offset == pytest.approx(-5.5 * dt, abs=2.0 * dt)


def test_max_lag_limits_search() -> None:
    rng = np.random.default_rng(5)
    reference = make_channel(rng.standard_normal(4000))
    signal = make_channel(np.roll(reference.volts, 500))
    result = estimate_time_offset(reference, signal, max_lag=100.0)
    assert -100.0 <= result.offset <= 100.0
    assert result.max_lag == pytest.approx(100.0)


def test_max_lag_must_be_positive() -> None:
    channel = make_channel(np.arange(10.0))
    with pytest.raises(ValueError, match="max_lag must be positive"):
        estimate_time_offset(channel, channel, max_lag=0.0)


def test_too_short_raises() -> None:
    with pytest.raises(ValueError, match="at least two samples"):
        estimate_time_offset(make_channel(np.zeros(1)), make_channel(np.zeros(1)))


def test_constant_channel_raises() -> None:
    with pytest.raises(ValueError, match="no signal"):
        estimate_time_offset(make_channel(np.ones(10)), make_channel(np.ones(10)))


# ---------------------------------------------------------------------------
# align_shot
# ---------------------------------------------------------------------------


def make_shot_capture(
    shift: float, *, names: tuple[str, ...] = ("C1",), dt: float = 1e-9
) -> Capture:
    """A Gaussian pulse at 1 us, recorded ``shift`` seconds late."""
    n = 2000
    t = np.arange(n, dtype=np.float64) * dt + shift
    pulse = np.exp(-0.5 * ((t - 1e-6) / 2e-8) ** 2)
    rows = [pulse * (1.0 + index) for index in range(len(names))]
    return Capture(volts=np.vstack(rows), t0=0.0, dt=dt, channel_names=names)


def test_align_shot_fits_every_capture_against_the_reference() -> None:
    shot = Shot()
    shot.add("a", make_shot_capture(0.0))
    shot.add("b", make_shot_capture(3.5e-9, names=("CH0",)))

    offsets = align_shot(shot)

    assert set(offsets) == {"b"}  # the reference is omitted: its offset is 0
    assert offsets["b"].offset == pytest.approx(3.5e-9, abs=1e-9)
    assert offsets["b"].correlation > 0.99
    assert not offsets["b"].inverted


def test_align_shot_writes_the_offsets_into_the_shot() -> None:
    shot = Shot()
    shot.add("a", make_shot_capture(0.0))
    shot.add("b", make_shot_capture(3.5e-9, names=("CH0",)))
    align_shot(shot)

    reference = shot.aligned_channel("a", "C1")
    aligned = shot.aligned_channel("b", "CH0")
    peak_ref = reference.time[int(np.argmax(reference.volts))]
    peak_other = aligned.time[int(np.argmax(aligned.volts))]

    assert peak_other == pytest.approx(peak_ref, abs=1e-9)


def test_align_shot_can_measure_without_applying() -> None:
    shot = Shot()
    shot.add("a", make_shot_capture(0.0))
    shot.add("b", make_shot_capture(3.5e-9, names=("CH0",)))

    offsets = align_shot(shot, apply=False)

    assert offsets["b"].offset == pytest.approx(3.5e-9, abs=1e-9)
    assert shot.time_offset("b") == 0.0


def test_align_shot_detects_an_inversion() -> None:
    shot = Shot()
    shot.add("a", make_shot_capture(0.0))
    inverted = make_shot_capture(0.0, names=("CH0",))
    shot.add("b", Capture(volts=-inverted.volts, t0=0.0, dt=inverted.dt))

    assert align_shot(shot)["b"].inverted


def test_align_shot_honours_an_explicit_reference() -> None:
    shot = Shot()
    shot.add("a", make_shot_capture(0.0))
    shot.add("b", make_shot_capture(3.5e-9, names=("CH0",)))

    offsets = align_shot(shot, reference="b")

    assert set(offsets) == {"a"}
    assert offsets["a"].offset == pytest.approx(-3.5e-9, abs=1e-9)
    assert shot.reference == "b"


def test_align_shot_accepts_a_single_channel_name_for_every_capture() -> None:
    shot = Shot()
    shot.add("a", make_shot_capture(0.0, names=("C1", "C2")))
    shot.add("b", make_shot_capture(3.5e-9, names=("C1", "C2")))

    offsets = align_shot(shot, channels="C2")
    assert offsets["b"].offset == pytest.approx(3.5e-9, abs=1e-9)


def test_align_shot_accepts_a_per_capture_channel_mapping() -> None:
    shot = Shot()
    shot.add("a", make_shot_capture(0.0, names=("C1", "C2")))
    shot.add("b", make_shot_capture(3.5e-9, names=("CH0", "CH1")))

    offsets = align_shot(shot, channels={"a": "C2", "b": "CH1"})
    assert offsets["b"].offset == pytest.approx(3.5e-9, abs=1e-9)


def test_align_shot_reports_an_unknown_channel() -> None:
    shot = Shot()
    shot.add("a", make_shot_capture(0.0))
    shot.add("b", make_shot_capture(0.0))

    with pytest.raises(KeyError, match="C9"):
        align_shot(shot, channels="C9")


def test_align_shot_needs_at_least_two_captures() -> None:
    shot = Shot()
    shot.add("a", make_shot_capture(0.0))

    with pytest.raises(ValueError, match="at least two captures"):
        align_shot(shot)


def test_align_shot_rejects_an_unknown_reference() -> None:
    shot = Shot()
    shot.add("a", make_shot_capture(0.0))
    shot.add("b", make_shot_capture(0.0))

    with pytest.raises(ValueError, match="is not a capture of this shot"):
        align_shot(shot, reference="ghost")


def test_align_shot_handles_captures_at_different_sample_rates() -> None:
    shot = Shot()
    shot.add("fast", make_shot_capture(0.0, dt=1e-9))
    shot.add("slow", make_shot_capture(3.5e-9, names=("CH0",), dt=2e-9))

    offsets = align_shot(shot)
    assert offsets["slow"].offset == pytest.approx(3.5e-9, abs=2e-9)


def test_align_shot_accounts_for_differing_time_origins() -> None:
    # estimate_time_offset correlates by index and ignores t0, so a shot whose
    # captures have different pretriggers is mis-aligned by exactly that
    # difference unless align_shot adds it back. Two instruments set up with
    # different pretriggers is the normal case, not an exotic one.
    dt = 1e-9
    n = 2000
    local = (np.arange(n, dtype=np.float64) - 1000) * dt
    pulse = np.exp(-0.5 * (local / 2e-8) ** 2)

    shot = Shot()
    shot.add("a", Capture(volts=pulse[None, :], t0=-1000 * dt, dt=dt))
    # Same event, but this instrument kept a much longer pretrigger, so its
    # t0 is 500 ns earlier and its record is shifted in the array by 500.
    shifted = np.roll(pulse, 500)
    shot.add("b", Capture(volts=shifted[None, :], t0=-1500 * dt, dt=dt))

    offsets = align_shot(shot)
    assert offsets["b"].offset == pytest.approx(0.0, abs=1e-9)

    reference = shot.aligned_channel("a", "CH1")
    aligned = shot.aligned_channel("b", "CH1")
    peak_ref = reference.time[int(np.argmax(reference.volts))]
    peak_other = aligned.time[int(np.argmax(aligned.volts))]
    assert peak_other == pytest.approx(peak_ref, abs=1e-9)


def test_align_shot_searches_far_enough_for_mismatched_pretriggers() -> None:
    # The default search is half the shorter record. When two instruments hold
    # the event at very different positions -- different pretriggers, or very
    # different sample rates at the same record length -- the true lag falls
    # outside that window and the fit locks onto noise. align_shot widens the
    # search by the difference in recorded origins.
    dt = 1e-9
    n = 4000
    local = (np.arange(n, dtype=np.float64) - 400) * dt
    pulse = np.exp(-0.5 * (local / 2e-8) ** 2)

    shot = Shot()
    shot.add("early", Capture(volts=pulse[None, :], t0=-400 * dt, dt=dt))
    # The same event, but held 3400 samples in -- far beyond half the record.
    late = np.roll(pulse, 3000)
    shot.add("late", Capture(volts=late[None, :], t0=-3400 * dt, dt=dt))

    offsets = align_shot(shot)

    assert offsets["late"].correlation > 0.9
    assert offsets["late"].offset == pytest.approx(0.0, abs=1e-9)


def test_offset_uses_the_whole_of_a_longer_spanning_record() -> None:
    # Two instruments at the same record length but different sample rates
    # span different durations. Truncating both to a common sample count
    # discards the slower one's later samples -- and the feature often lives
    # there, because a pretrigger of 0.5 puts it at the midpoint of a record
    # that is twice as long in time.
    n = 4096
    fast_dt = 1e-9
    slow_dt = 2e-9

    def burst(dt: float, shift: float = 0.0) -> np.ndarray:
        t = (np.arange(n, dtype=np.float64) - n / 2) * dt - shift
        return np.exp(-0.5 * (t / 1e-8) ** 2)

    shot = Shot()
    shot.add(
        "fast",
        Capture(volts=burst(fast_dt)[None, :], t0=-n / 2 * fast_dt, dt=fast_dt),
    )
    shot.add(
        "slow",
        Capture(volts=burst(slow_dt)[None, :], t0=-n / 2 * slow_dt, dt=slow_dt),
    )

    offsets = align_shot(shot)

    assert offsets["slow"].correlation > 0.7
    assert not offsets["slow"].inverted
    assert offsets["slow"].offset == pytest.approx(0.0, abs=2e-9)


def test_offset_is_symmetric_for_unequal_length_records() -> None:
    dt = 1e-9
    long_n, short_n = 3000, 1000
    centre = 500

    def burst(n: int, index: int) -> np.ndarray:
        t = (np.arange(n, dtype=np.float64) - index) * dt
        return np.exp(-0.5 * (t / 1e-8) ** 2)

    long_channel = make_channel(burst(long_n, centre + 250), dt=dt)
    short_channel = make_channel(burst(short_n, centre), dt=dt, name="CH2")

    forward = estimate_time_offset(long_channel, short_channel)
    backward = estimate_time_offset(short_channel, long_channel)

    assert forward.offset == pytest.approx(250 * dt, abs=dt)
    assert backward.offset == pytest.approx(-250 * dt, abs=dt)
