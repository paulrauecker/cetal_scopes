"""The driver registry and the synthetic demo instrument."""

from __future__ import annotations

import numpy as np
import pytest

from cetal_scopes import DRIVERS, DemoScope, SiglentSDS6204L, create_scope, driver_class


def test_registry_lists_the_known_drivers() -> None:
    assert set(DRIVERS) == {"demo", "siglent_sds6204l", "spectrum_m5i3367"}


def test_driver_class_is_case_insensitive() -> None:
    assert driver_class("Siglent_SDS6204L") is SiglentSDS6204L


def test_driver_class_rejects_an_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown driver"):
        driver_class("tektronix")


def test_create_scope_passes_keyword_arguments_through() -> None:
    scope = create_scope("demo", label="x", channels=("A", "B"))
    assert isinstance(scope, DemoScope)
    assert scope.channels == ("A", "B")


def test_demo_scope_acquires_a_record() -> None:
    scope = DemoScope(channels=("A", "B"), record_length=256, trigger_delay=0.0)
    scope.connect()
    capture = scope.acquire()

    assert capture.n_channels == 2
    assert capture.n_samples == 256
    assert capture.metadata["synthetic"] is True
    assert np.isfinite(capture.volts).all()


def test_demo_scope_honours_the_physical_vocabulary() -> None:
    scope = DemoScope(trigger_delay=0.0)
    scope.connect()
    scope.configure(
        {
            "channels": ("A",),
            "sample_rate": 5e8,
            "record_length": 128,
            "pretrigger": 0.25,
        }
    )
    capture = scope.acquire()

    assert capture.dt == pytest.approx(2e-9)
    assert capture.n_samples == 128
    assert capture.t0 == pytest.approx(-32 * 2e-9)


def test_demo_scope_rejects_unknown_settings() -> None:
    scope = DemoScope()
    with pytest.raises(ValueError, match="unknown settings"):
        scope.configure({"nonsense": 1})


def test_demo_scope_without_a_trigger_delay_times_out() -> None:
    scope = DemoScope(trigger_delay=None, acquire_timeout=0.02)
    scope.connect()

    with pytest.raises(TimeoutError, match="did not complete"):
        scope.acquire()


def test_demo_scope_can_be_forced() -> None:
    scope = DemoScope(trigger_delay=None, record_length=32)
    scope.connect()
    scope.arm()
    assert scope.wait(timeout=0.01) is False

    scope.force_trigger()
    assert scope.wait(timeout=0.5) is True
    assert scope.fetch().n_samples == 32


def test_demo_scope_reports_its_trigger_state() -> None:
    scope = DemoScope(trigger_delay=None)
    scope.connect()
    assert scope.trigger_status() == "Stop"
    scope.arm()
    assert scope.trigger_status() == "Ready"


def test_demo_scope_skew_shifts_the_time_axis() -> None:
    scope = DemoScope(record_length=64, skew=1e-6, trigger_delay=0.0)
    scope.connect()
    other = DemoScope(record_length=64, trigger_delay=0.0)
    other.connect()

    assert scope.acquire().t0 - other.acquire().t0 == pytest.approx(1e-6)


def test_demo_scope_requires_a_connection() -> None:
    with pytest.raises(RuntimeError, match="not connected"):
        DemoScope().arm()


def test_demo_scope_requires_at_least_one_channel() -> None:
    with pytest.raises(ValueError, match="at least one channel"):
        DemoScope(channels=())
