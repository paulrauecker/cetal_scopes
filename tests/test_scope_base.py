from collections.abc import Mapping
from typing import Any

import numpy as np
import pytest

from cetal_scopes import Capture, Scope


class FakeScope(Scope):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.settings: dict[str, Any] = {}

    def connect(self) -> None:
        self.events.append("connect")

    def configure(self, settings: Mapping[str, Any]) -> None:
        self.settings = dict(settings)
        self.events.append("configure")

    def acquire(self) -> Capture:
        self.events.append("acquire")
        return Capture(volts=np.zeros((1, 2)), t0=0.0, dt=1.0)

    def close(self) -> None:
        self.events.append("close")


def test_scope_is_abstract() -> None:
    with pytest.raises(TypeError):
        Scope()  # type: ignore[abstract]


def test_incomplete_subclass_is_abstract() -> None:
    class Incomplete(Scope):
        def connect(self) -> None: ...

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


def test_context_manager_connects_and_closes() -> None:
    scope = FakeScope()
    with scope as entered:
        assert entered is scope
        assert scope.events == ["connect"]
    assert scope.events == ["connect", "close"]


def test_close_runs_when_body_raises() -> None:
    scope = FakeScope()
    with pytest.raises(RuntimeError), scope:
        raise RuntimeError("boom")
    assert scope.events == ["connect", "close"]


def test_acquire_returns_capture() -> None:
    scope = FakeScope()
    scope.connect()
    assert isinstance(scope.acquire(), Capture)


def test_configure_stores_settings() -> None:
    scope = FakeScope()
    scope.configure({"sample_rate": 1e9})
    assert scope.settings == {"sample_rate": 1e9}
