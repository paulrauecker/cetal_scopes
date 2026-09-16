"""Driver for the Advantex SG8-HP(SS)01M RF signal generator (10 MHz-8 GHz).

The instrument speaks plain ASCII SCPI over a virtual COM port (RS-232 or
USB-CDC via a CP2102 bridge, 115200-8-N-1, no flow control). This driver talks
to it through PyVISA's ``ASRL`` (serial) resource class, imported lazily so
that importing :mod:`cetal_scopes` never requires PyVISA or hardware.

See ``docs/scopes/SG8-HPSS10M-C2U42HP315/`` for the full command reference
this driver implements.

Verified 2026-09-14 against a real unit (``*IDN?`` ->
``Advantex,SG8-HPSS10M-C2U42HP315,47954-7081-001,R2.2``): connect, and
frequency/power/output queries all work as documented. That unit runs
firmware R2.2, newer than the R1.4 manual this driver was written from -
no discrepancies seen yet, but keep it in mind if a command misbehaves.

The serial device (e.g. ``/dev/ttyUSB0``) is typically owned by the
``dialout`` group; either add your user to that group (``sudo usermod -aG
dialout $USER``, then re-login) or ``sudo chmod 666`` the device, which
only lasts until it's unplugged or the system reboots.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Protocol, Self

__all__ = ["SG8SignalGenerator"]

DEFAULT_BAUD_RATE = 115200
_MAX_COMMAND_LENGTH = 64

_FREQUENCY_UNIT = "HZ"
_POWER_UNIT = "DBM"
_PHASE_UNIT = "DEG"

_REFERENCE_SOURCES = frozenset({"INT", "EXT", "INTernal", "EXTernal"})


def _normalize_on_off(value: bool | str) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    text = value.strip().upper()
    if text in ("1", "ON"):
        return "1"
    if text in ("0", "OFF"):
        return "0"
    raise ValueError(f"expected a bool or 'ON'/'OFF', got {value!r}")


def _normalize_reference_source(source: str) -> str:
    text = source.strip()
    if text.upper() not in {s.upper() for s in _REFERENCE_SOURCES}:
        raise ValueError(f"reference source must be 'INT' or 'EXT', got {source!r}")
    return "INT" if text.upper().startswith("INT") else "EXT"


class _Transport(Protocol):
    """SCPI transport to the instrument (real serial link or test double)."""

    def open(self) -> None: ...

    def write(self, command: str) -> None: ...

    def query(self, command: str) -> str: ...

    def close(self) -> None: ...


class _VisaSerialTransport:
    """PyVISA ``ASRL`` transport, imported lazily so PyVISA stays optional."""

    def __init__(self, resource: str, baud_rate: int, timeout: float) -> None:
        self._resource = resource
        self._baud_rate = baud_rate
        self._timeout_ms = int(timeout * 1000)
        self._resource_manager: Any = None
        self._instrument: Any = None

    def open(self) -> None:
        if self._instrument is not None:
            return
        import pyvisa
        import pyvisa.constants as visa_constants

        self._resource_manager = pyvisa.ResourceManager()
        instrument = self._resource_manager.open_resource(self._resource)
        instrument.timeout = self._timeout_ms
        instrument.baud_rate = self._baud_rate
        instrument.data_bits = 8
        instrument.parity = visa_constants.Parity.none
        instrument.stop_bits = visa_constants.StopBits.one
        instrument.flow_control = visa_constants.VI_ASRL_FLOW_NONE
        instrument.write_termination = "\n"
        instrument.read_termination = "\n"
        self._instrument = instrument
        self._discard_stale_bytes(instrument)

    @staticmethod
    def _discard_stale_bytes(instrument: Any) -> None:
        """Drain leftover bytes queued by a previous session.

        A serial port keeps whatever the instrument already sent (e.g. a
        response an earlier, uncleanly-closed session never read), which
        desyncs every later read onto stale data -- symptoms include two
        concatenated numeric replies. There's no query-and-clear primitive
        for this, so briefly poll with a short timeout and discard whatever
        comes back until it times out.
        """
        import pyvisa

        original_timeout = instrument.timeout
        instrument.timeout = 50
        try:
            for _ in range(16):
                instrument.read_raw()
        except pyvisa.errors.VisaIOError:
            pass
        finally:
            instrument.timeout = original_timeout

    def write(self, command: str) -> None:
        self._require_instrument().write(command)

    def query(self, command: str) -> str:
        return str(self._require_instrument().query(command)).strip()

    def close(self) -> None:
        if self._instrument is not None:
            self._instrument.close()
            self._instrument = None
        if self._resource_manager is not None:
            self._resource_manager.close()
            self._resource_manager = None

    def _require_instrument(self) -> Any:
        if self._instrument is None:
            raise RuntimeError("transport is not open")
        return self._instrument


class SG8SignalGenerator:
    """Driver for an Advantex SG8-HP01M / SG8-HPSS01M RF signal generator.

    Covers both the plain (``HP01M``) and spur-suppression (``HPSS01M``)
    variants; the SS option only adds an internal reference mode that this
    driver doesn't need to distinguish, since it's controlled the same way
    over SCPI.

    Parameters
    ----------
    resource : str
        PyVISA ``ASRL`` resource string, e.g. ``"ASRL/dev/ttyUSB0::INSTR"``
        on Linux or ``"ASRL3::INSTR"`` (COM3) on Windows.
    baud_rate : int, optional
        Serial baud rate. Defaults to ``115200`` (the instrument's fixed
        rate; 8 data bits, no parity, 1 stop bit, no flow control).
    timeout : float, optional
        VISA I/O timeout in seconds. Defaults to ``5.0``.
    transport : _Transport, optional
        Injectable transport, primarily for tests.

    Notes
    -----
    The instrument accepts at most one command in flight ahead of the one
    currently executing (buffer depth 2) and each command string is capped
    at 64 characters; every setter here fits well within that limit. Use
    :meth:`operation_complete` between back-to-back commands instead of a
    fixed delay when timing matters.
    """

    def __init__(
        self,
        resource: str,
        *,
        baud_rate: int = DEFAULT_BAUD_RATE,
        timeout: float = 5.0,
        transport: _Transport | None = None,
    ) -> None:
        self._resource = resource
        self._baud_rate = baud_rate
        self._timeout = timeout
        self._transport = transport
        self._owns_transport = transport is None
        self._connected = False
        self._idn = ""

    @property
    def idn(self) -> str:
        """Instrument identification string from ``*IDN?``."""
        return self._idn

    def connect(self) -> None:
        """Open the serial connection and identify the instrument."""
        if self._connected:
            return
        if self._transport is None:
            self._transport = _VisaSerialTransport(
                self._resource, self._baud_rate, self._timeout
            )
        self._transport.open()
        self._connected = True
        try:
            self._idn = self._query("*IDN?")
        except Exception:
            self._connected = False
            raise

    def close(self) -> None:
        """Close the transport. Safe to call repeatedly."""
        if self._transport is not None:
            self._transport.close()
            if self._owns_transport:
                self._transport = None
        self._connected = False

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def reset(self) -> None:
        """Reset to defaults: CW, 1 GHz, 0 dBm, 0 deg phase, RF Out off."""
        self._write("*RST")

    def clear_errors(self) -> None:
        """Clear the error queue (``*CLS``)."""
        self._write("*CLS")

    def operation_complete(self) -> bool:
        """Block until all previously sent commands finish (``*OPC?``).

        Prefer this over a fixed delay between back-to-back commands, given
        the instrument's 2-deep command buffer and limited status reporting.
        """
        return self._query("*OPC?").strip() == "1"

    def system_error(self) -> tuple[int, str]:
        """Pop the oldest queued error as ``(code, description)``.

        ``(0, "No error")`` if the queue is empty. The queue is only 2 deep;
        a third unread error overwrites the last slot with
        ``(-350, "Queue overflow")``.
        """
        response = self._query("SYSTem:ERRor?")
        code_text, _, description = response.partition(",")
        return int(code_text), description.strip().strip('"')

    def set_frequency(self, hertz: float) -> None:
        """Set the CW output frequency in hertz.

        Out-of-range values silently clamp to the nearest limit (10 MHz to
        8 GHz); no error is raised in that case.
        """
        self._write(f"FREQuency {float(hertz):.4f}{_FREQUENCY_UNIT}")

    def frequency(self) -> float:
        """Return the current output frequency in hertz."""
        return float(self._query("FREQuency?"))

    def set_power(self, dbm: float) -> None:
        """Set the RF output level in dBm.

        Out-of-range values silently clamp to the nearest calibrated limit.
        """
        self._write(f"POWer {float(dbm):.2f}{_POWER_UNIT}")

    def power(self) -> float:
        """Return the current RF output level in dBm."""
        return float(self._query("POWer?"))

    def set_phase(self, degrees: float) -> None:
        """Set the RF phase offset in degrees."""
        self._write(f"PHASe {float(degrees):.2f}{_PHASE_UNIT}")

    def phase(self) -> float:
        """Return the current RF phase offset in degrees."""
        return float(self._query("PHASe?"))

    def set_output(self, state: bool | str) -> None:
        """Turn RF Out on or off.

        Turning it on causes a brief (~5 ms) level glitch as the APC has no
        input yet and runs at max gain momentarily; use an external
        attenuator ahead of sensitive-input devices.
        """
        self._write(f"OUTPut {_normalize_on_off(state)}")

    def output(self) -> bool:
        """Return whether RF Out is currently enabled."""
        return self._query("OUTPut?").strip() == "1"

    def set_reference_output(self, state: bool | str) -> None:
        """Turn the rear-panel REF Out on or off."""
        self._write(f"OUTPut:ROSCillator {_normalize_on_off(state)}")

    def reference_output(self) -> bool:
        """Return whether REF Out is currently enabled."""
        return self._query("OUTPut:ROSCillator?").strip() == "1"

    def set_reference_source(self, source: str) -> None:
        """Select the frequency reference: ``"INT"`` or ``"EXT"``."""
        self._write(f"ROSCillator:SOURce {_normalize_reference_source(source)}")

    def reference_source(self) -> str:
        """Return the active reference source, ``"INT"`` or ``"EXT"``."""
        return self._query("ROSCillator:SOURce?").strip().upper()

    def set_external_reference_frequency(self, hertz: float) -> None:
        """Set the expected external reference frequency in hertz.

        Must match the signal actually applied at REF In.
        """
        self._write(
            f"ROSCillator:EXTernal:FREQuency {float(hertz):.4f}{_FREQUENCY_UNIT}"
        )

    def external_reference_frequency(self) -> float:
        """Return the configured external reference frequency in hertz."""
        return float(self._query("ROSCillator:EXTernal:FREQuency?"))

    def temperature(self) -> float:
        """Return the internal RF-synthesizer block temperature in degC."""
        return float(self._query("MEASure:SCALar:TEMPerature?"))

    def questionable_condition(self) -> int:
        """Return the questionable-status bitmask; ``0`` means all OK.

        Bit 3 (value ``8``) means the currently set power level is outside
        the calibrated range for the current frequency.
        """
        return int(self._query("STATus:QUEStionable:CONDition?"))

    def _require_connected(self) -> _Transport:
        if not self._connected or self._transport is None:
            raise RuntimeError(
                "signal generator is not connected; call connect() first"
            )
        return self._transport

    def _write(self, command: str) -> None:
        if len(command) > _MAX_COMMAND_LENGTH:
            raise ValueError(
                f"command exceeds the instrument's {_MAX_COMMAND_LENGTH}-char "
                f"limit: {command!r}"
            )
        self._require_connected().write(command)

    def _query(self, command: str) -> str:
        return self._require_connected().query(command)
