"""The write-path probe: telling a rejected command from an ignored one."""

from __future__ import annotations

from probe_write_path import COMMAND_ERROR, Attempt, render, try_write

from cetal_scopes.scopes.siglent import SiglentSDS6204L


class ScriptedTransport:
    """A scope that honours some commands, rejects some, and ignores the rest."""

    def __init__(self, *, honours: set[str], rejects: set[str]) -> None:
        self.honours = honours
        self.rejects = rejects
        self.values = {
            ":TIMebase:SCALe?": "5.00E-06",
            ":ACQuire:MDEPth?": "2.5M",
            ":ACQuire:MMANagement?": "AUTO",
        }
        self.esr = 0

    def open(self) -> None: ...

    def close(self) -> None: ...

    def write(self, command: str) -> None:
        head, _, value = command.partition(" ")
        if command in self.rejects:
            self.esr |= COMMAND_ERROR
            return
        if command in self.honours:
            self.values[f"{head}?"] = value

    def query(self, command: str) -> str:
        if command == "*IDN?":
            return "Siglent,SDS6204L,TEST,1.0"
        if command == "*ESR?":
            latched, self.esr = self.esr, 0
            return str(latched)
        return self.values.get(command, "?")

    def query_block(self, command: str) -> bytes:  # pragma: no cover - unused
        raise AssertionError(command)


def scope_with(
    *, honours: set[str] | None = None, rejects: set[str] | None = None
) -> SiglentSDS6204L:
    transport = ScriptedTransport(honours=honours or set(), rejects=rejects or set())
    scope = SiglentSDS6204L(channels=("C1",), transport=transport)
    scope.connect()
    return scope


def test_a_write_that_sticks_is_reported_as_taking_effect() -> None:
    scope = scope_with(honours={":ACQuire:MDEPth 10k"})
    attempt = try_write(scope, ":ACQuire:MDEPth 10k", ":ACQuire:MDEPth?")
    assert attempt.changed
    assert not attempt.command_error
    assert attempt.verdict == "took effect"


def test_a_rejected_write_is_distinguished_by_the_error_bit() -> None:
    scope = scope_with(rejects={":ACQuire:MDEPth 10k"})
    attempt = try_write(scope, ":ACQuire:MDEPth 10k", ":ACQuire:MDEPth?")
    assert not attempt.changed
    assert attempt.command_error
    assert attempt.verdict == "REJECTED (command error)"


def test_a_silently_ignored_write_is_its_own_verdict() -> None:
    """What the real instrument is doing: understood, unacted on."""
    scope = scope_with()
    attempt = try_write(scope, ":ACQuire:MDEPth 10k", ":ACQuire:MDEPth?")
    assert not attempt.changed
    assert not attempt.command_error
    assert attempt.verdict == "accepted, ignored"


def test_an_earlier_error_does_not_leak_into_the_next_attempt() -> None:
    """Each write is bracketed by a read, because *ESR? latches."""
    scope = scope_with(rejects={":ACQuire:MDEPth 10k"})
    try_write(scope, ":ACQuire:MDEPth 10k", ":ACQuire:MDEPth?")
    clean = try_write(scope, ":ACQuire:MDEPth 1M", ":ACQuire:MDEPth?")
    assert not clean.command_error


def attempt(command: str, *, changed: bool, esr: int = 0) -> Attempt:
    return Attempt(
        command=command,
        query="q?",
        before="a",
        after="b" if changed else "a",
        esr=esr,
    )


def test_a_dead_control_says_no_write_is_landing() -> None:
    text = render([attempt("control", changed=False), attempt("x", changed=False)])
    assert "CONTROL write did not stick" in text


def test_a_live_control_with_nothing_else_points_at_the_instrument_state() -> None:
    text = render([attempt("control", changed=True), attempt("x", changed=False)])
    assert "declining to act" in text


def test_a_rejected_spelling_points_at_the_parameter_format() -> None:
    text = render(
        [
            attempt("control", changed=True),
            attempt("x", changed=False, esr=COMMAND_ERROR),
            attempt("y", changed=True),
        ]
    )
    assert "parameter format" in text
    assert "'y'" in text
