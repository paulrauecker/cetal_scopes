import pytest

from cetal_scopes import SG8SignalGenerator


class FakeTransport:
    def __init__(self, *, idn: str = "Advantex,SG8-HP01M-C2U42HP315,12345,R1.4") -> None:
        self.idn = idn
        self.opened = False
        self.closed = False
        self.commands: list[str] = []
        self.state = {
            "FREQuency?": "1000000000.0000",
            "POWer?": "0.00",
            "PHASe?": "0.00",
            "OUTPut?": "0",
            "OUTPut:ROSCillator?": "0",
            "ROSCillator:SOURce?": "INT",
            "ROSCillator:EXTernal:FREQuency?": "10000000.0000",
            "MEASure:SCALar:TEMPerature?": "34.5",
            "STATus:QUEStionable:CONDition?": "0",
            "SYSTem:ERRor?": '0,"No error"',
            "*OPC?": "1",
        }

    def open(self) -> None:
        self.opened = True

    def write(self, command: str) -> None:
        self.commands.append(command)
        name, _, value = command.partition(" ")
        if name.upper() == "FREQUENCY" or name.upper() == "FREQ":
            self.state["FREQuency?"] = value.rstrip("HZ")
        elif name.upper() in ("POWER", "POW"):
            self.state["POWer?"] = value.rstrip("DBM")
        elif name.upper() in ("PHASE", "PHAS"):
            self.state["PHASe?"] = value.rstrip("DEG")
        elif name.upper() == "OUTPUT":
            self.state["OUTPut?"] = value
        elif name.upper() == "OUTPUT:ROSCILLATOR":
            self.state["OUTPut:ROSCillator?"] = value
        elif name.upper() == "ROSCILLATOR:SOURCE":
            self.state["ROSCillator:SOURce?"] = value.upper()
        elif name.upper() == "ROSCILLATOR:EXTERNAL:FREQUENCY":
            self.state["ROSCillator:EXTernal:FREQuency?"] = value.rstrip("HZ")

    def query(self, command: str) -> str:
        self.commands.append(command)
        if command == "*IDN?":
            return self.idn
        return self.state[command]

    def close(self) -> None:
        self.closed = True


def test_connect_reads_idn() -> None:
    transport = FakeTransport()
    gen = SG8SignalGenerator("ASRL/dev/ttyUSB0::INSTR", transport=transport)
    gen.connect()
    assert transport.opened
    assert gen.idn == transport.idn
    gen.close()
    assert transport.closed


def test_context_manager_connects_and_closes() -> None:
    transport = FakeTransport()
    with SG8SignalGenerator("ASRL3::INSTR", transport=transport) as gen:
        assert gen.idn == transport.idn
    assert transport.closed


def test_connect_is_idempotent() -> None:
    transport = FakeTransport()
    gen = SG8SignalGenerator("ASRL3::INSTR", transport=transport)
    gen.connect()
    gen.connect()
    assert transport.commands.count("*IDN?") == 1


def test_operations_require_connection() -> None:
    gen = SG8SignalGenerator("ASRL3::INSTR", transport=FakeTransport())
    with pytest.raises(RuntimeError, match="not connected"):
        gen.set_frequency(1e9)


def test_frequency_round_trip() -> None:
    transport = FakeTransport()
    gen = SG8SignalGenerator("ASRL3::INSTR", transport=transport)
    gen.connect()
    gen.set_frequency(2.1e9)
    assert transport.commands[-1] == "FREQuency 2100000000.0000HZ"
    assert gen.frequency() == pytest.approx(2100000000.0)


def test_power_round_trip() -> None:
    transport = FakeTransport()
    gen = SG8SignalGenerator("ASRL3::INSTR", transport=transport)
    gen.connect()
    gen.set_power(-1.5)
    assert transport.commands[-1] == "POWer -1.50DBM"
    assert gen.power() == pytest.approx(-1.5)


def test_phase_round_trip() -> None:
    transport = FakeTransport()
    gen = SG8SignalGenerator("ASRL3::INSTR", transport=transport)
    gen.connect()
    gen.set_phase(90.0)
    assert transport.commands[-1] == "PHASe 90.00DEG"
    assert gen.phase() == pytest.approx(90.0)


def test_output_on_off() -> None:
    transport = FakeTransport()
    gen = SG8SignalGenerator("ASRL3::INSTR", transport=transport)
    gen.connect()
    gen.set_output(True)
    assert transport.commands[-1] == "OUTPut 1"
    assert gen.output() is True
    gen.set_output("off")
    assert transport.commands[-1] == "OUTPut 0"
    assert gen.output() is False


def test_reference_output_and_source() -> None:
    transport = FakeTransport()
    gen = SG8SignalGenerator("ASRL3::INSTR", transport=transport)
    gen.connect()
    gen.set_reference_output(True)
    assert gen.reference_output() is True
    gen.set_reference_source("ext")
    assert transport.commands[-1] == "ROSCillator:SOURce EXT"
    assert gen.reference_source() == "EXT"


def test_external_reference_frequency_round_trip() -> None:
    transport = FakeTransport()
    gen = SG8SignalGenerator("ASRL3::INSTR", transport=transport)
    gen.connect()
    gen.set_external_reference_frequency(100e6)
    assert transport.commands[-1] == "ROSCillator:EXTernal:FREQuency 100000000.0000HZ"
    assert gen.external_reference_frequency() == pytest.approx(100e6)


def test_reset_clear_and_opc() -> None:
    transport = FakeTransport()
    gen = SG8SignalGenerator("ASRL3::INSTR", transport=transport)
    gen.connect()
    gen.reset()
    gen.clear_errors()
    assert gen.operation_complete() is True
    assert transport.commands[-3:] == ["*RST", "*CLS", "*OPC?"]


def test_system_error_parses_code_and_description() -> None:
    transport = FakeTransport()
    gen = SG8SignalGenerator("ASRL3::INSTR", transport=transport)
    gen.connect()
    assert gen.system_error() == (0, "No error")

    transport.state["SYSTem:ERRor?"] = '-120,"Numeric data error"'
    assert gen.system_error() == (-120, "Numeric data error")


def test_temperature_and_questionable_condition() -> None:
    transport = FakeTransport()
    gen = SG8SignalGenerator("ASRL3::INSTR", transport=transport)
    gen.connect()
    assert gen.temperature() == pytest.approx(34.5)
    assert gen.questionable_condition() == 0

    transport.state["STATus:QUEStionable:CONDition?"] = "8"
    assert gen.questionable_condition() == 8


def test_invalid_reference_source_raises() -> None:
    transport = FakeTransport()
    gen = SG8SignalGenerator("ASRL3::INSTR", transport=transport)
    gen.connect()
    with pytest.raises(ValueError, match="reference source"):
        gen.set_reference_source("bogus")


def test_invalid_on_off_raises() -> None:
    transport = FakeTransport()
    gen = SG8SignalGenerator("ASRL3::INSTR", transport=transport)
    gen.connect()
    with pytest.raises(ValueError, match="ON.*OFF"):
        gen.set_output("bogus")
