# Code


!!! warning
    AI generated summary, be sure to double check the reference manual for critical applications.

Source: [*SG8-HP(SS)01M-C2U42HP315 RF Signal Generator 10MHz-8GHz Operating Manual*](http://advantex.ru/joom1/download/doc_download/8-sg8-hpss01m-operating-manual.html), Rev. 1.4, Advantex LLC, December 13, 2011.

Models covered: **SG8-HP01M** and **SG8-HPSS01M** (SS = additional spur-suppression / dual reference PLL option).


---

## 1. Physical Interface

- Protocol: **SCPI** (Standard Commands for Programmable Instruments) over **RS-232** and **USB**, both on the rear panel.
- Only **one port is active at a time**; the instrument auto-selects whichever port first receives a byte (not necessarily a valid command), and that choice holds until power-off.
- USB is bridged to an emulated COM port via a **CP2102** chip — requires the Silicon Labs CP2102 USB-to-UART driver (Win2K/XP/2K3/Vista/7, Mac OS, Linux 3.1).
- **Serial settings: 115200 bps, 8 data bits, no parity, 1 stop bit, no flow control.**

## 2. Quick Start

Tested with HyperTerminal (Start▸Programs▸Accessories▸Communications▸HyperTerminal): configure File▸Properties▸Settings▸ASCII Setup with "Echo typed characters locally" and "Send line ends with line feeds" checked.

Minimal example:
```
*rst
freq 100MHz
pow -1dBm
```
→ resets the instrument, then sets 100 MHz / −1 dBm output.

Fuller example transcript (from the manual):
```
*IDN?
Advantex,SG8-HP01M-C2U42HP255,57065-9090-001,R1.0

FREQ 3.5GHz
SYST:ERR?
0,"No error"

POW 10dBm
SYST:ERR?
0,"No error"

OUTP ON

PHAS 90deg

freq
syst:err?
-120,"Numeric data error"

*RST
```
Note: bare `freq` with no argument on a *setter* command triggers a numeric data error, code −120 — a good reminder that these commands don't default to query mode without the trailing `?`.

## 3. Command Processing Model & Limits

Input unit (RS-232/USB) → input buffer → command recognition → (a) data set / instrument hardware, and (b) status reporting system → output unit (RS-232/USB).

- Parser recognizes **one command per string**; max string length **64 characters**.
- Command **buffer depth = 2** — a second command may be sent immediately after the first without waiting for completion, but no more than that (don't queue a 3rd before the 1st completes).
- Status reporting is **not fully implemented** (partial SCPI status-system compliance).
- Not all SCPI data formats are supported; documentation does not claim full SCPI-1999.0 compliance.
- 💡 **If sending commands back-to-back with no delay, use `*OPC?` between them** to confirm completion instead of relying on fixed timing delays:
```
freq 100 mhz
*opc?
1
pow 1 dbm
*opc?
1
```

## 4. SCPI Syntax Notes

- Commands are **case-insensitive** (`*RST` = `*rst`).
- Each command has a long form and a short form; the **capitalized portion is the accepted short form** (e.g. `FREQuency` → `FREQ`, or the full `FREQuency` — both work).
- Bracketed command segments are optional — e.g. `[SOURce:]FREQuency[:CW]` can be written as `SOURce:FREQuency:CW`, `FREQuency:CW`, `SOURce:FREQuency`, or `FREQuency`.
- Numeric parameters may include a unit; if omitted, a **default unit** applies (e.g. `FREQ 1GHz`, `FREQ 1E9Hz`, and `FREQ 1000000000` are all equivalent — default unit Hz).
- Commands ending in `?` are **queries** and return a response (e.g. `*IDN?`, or `FREQ?` to read back the current set value).
- Numeric parameters can also accept the keywords **`MINimum`**, **`MAXimum`**, **`DEFault`** in place of a number (e.g. `FREQ MAX` sets 8 GHz; `FREQ DEF` sets 1 GHz default).
- If a command doesn't behave as expected, check `SYSTem:ERRor[:NEXT]?` — returns `0,"No error"` if nothing is queued, otherwise an error code + description.

## 5. Command Tree

(Excludes standard IEEE-488.2 commands `*CLS`, `*IDN?`, `*RST`, `*OPC?`.)
```
SOURce
 ├─ FREQuency  → [:CW]                       (float, GHZ|MHZ|MAHZ|KHZ|HZ)
 ├─ POWer      → [:LEVel][:IMMediate][:AMPLitude]   (float, DBM)
 ├─ PHASe      → [:ADJust]                   (float, DEGree)
 └─ ROSCillator
     ├─ :SOURce                              (INTernal|EXTernal)
     └─ :EXTernal:FREQuency                  (float, GHZ|MHZ|MAHZ|KHZ|HZ)
OUTPut
 ├─ [:STATe]                                 (ON|OFF|1|0)
 └─ ROSCillator
     └─ [:STATe]                             (ON|OFF|1|0)
STATus
 └─ QUEStionable
     └─ CONDition?                           (integer)
MEASure
 └─ SCALar
     └─ TEMPerature?                         (returns °C)
```

## 6. Full SCPI Command List

**`*CLS`** — clears the error buffer.

**`*IDN?`** — returns instrument ID string: `manufacturer,part number,serial number,firmware info`.

**`*RST`** — resets instrument to defaults (same as the Load Default menu command): mode **CW**, frequency **1 GHz**, level **0 dBm**, phase **0°**, RF output **OFF**. Good practice to start every remote session with `*RST`.

**`*OPC?`** — returns `1` once all previously sent commands have completed. Prefer this over fixed delays between back-to-back commands (see §3 example above).

**`SYSTem:ERRor[:NEXT]?`** — returns oldest queued error as `code,"description"`, or `0,"No error"` if empty. FIFO buffer, capacity **2 messages**; reading with `SYST:ERR?` pops one entry (or clear all at once with `*CLS`). If the buffer is full and a new error occurs, the newest overwrites the last slot with `-350,"Queue overflow"`.

**`OUTPut[:STATe]`** — turns RF Out on/off (same effect as the front-panel RF OUT ON/OFF button).
- Params: `1`/`ON` to enable, `0`/`OFF` to disable.
- Query returns `0` (off) or `1` (on).
- Examples: `output on`, `outp off`, `outp:state 1`, `OUTPUT 0`, `OUTP:STAT?`

**`OUTPut:ROSCillator[:STATe]`** — turns the **REF Out** rear-panel output on/off.
- Params: `1`/`ON`, `0`/`OFF`. Query returns `0`/`1`.
- Examples: `output:rosc on`, `outp:rosc off`, `outp:rosc:state 1`

**`[SOURce:]FREQuency[:CW]`** — sets RF output frequency.
- Param form: `[+|-]float_num[E[+|-]int_num][GHZ|MHZ|MAHZ|KHZ|HZ]`. Default unit **Hz**. Rounded to 10⁻⁴ Hz accuracy. Out-of-range values clamp to nearest limit (**no error raised** in that case).
- Query returns current frequency in Hz as `[+|-]float_num`.
- Examples: `freq 2.1GHZ`, `frequency 21e-1ghz`, `sour:freq:cw 21E8`, `freq max`

**`[SOURce:]POWer[:LEVel][:IMMediate][:AMPLitude]`** — sets RF output level.
- Param form: `[+|-]float_num[E[+|-]int_num][DBM]`. Default unit **dBm**. Rounded to 10⁻² dBm. Out-of-range clamps to nearest limit silently.
- Query returns current level in dBm.
- Examples: `pow 5.1dbm`, `source:power 1.23`, `POWER 123E-2DBM`, `POW MAX`

**`[SOURce:]PHASe[:ADJust]`** — sets RF phase offset.
- Param form: `[+|-]float_num[E[+|-]int_num][DEGree]`. Default unit **degree**. Rounded to 10⁻² degree. Out-of-range clamps silently.
- Query returns current phase offset in degrees.
- Examples: `phas 90deg`, `PHASE 90DEG`, `phase:adj 90.1e-1`

**`[SOURce:]ROSCillator:SOURce`** — selects reference source (equivalent to turning REF In use on/off).
- Params: `INTernal` or `EXTernal`.
- Query returns `INT` or `EXT`.
- Examples: `rosc:source INT`, `rocs:sour ext`

**`[SOURce:]ROSCillator:EXTernal:FREQuency`** — sets the expected external reference frequency (must match actual REF In signal).
- Param form: `[+|-]float_num[E[+|-]int_num][GHZ|MHZ|MAHZ|KHZ|HZ]`. Default unit Hz, rounded to 10⁻⁴ Hz, out-of-range clamps silently.
- Query returns current value in Hz.
- Examples: `rosc:ext:freq 100MHZ`, `SOURCE:ROSC:EXTERNAL:FREQUENCY 32MHz`, `rosc:ext:freq DEF`

**`MEASure[:SCALar]:TEMPerature?`** — reads internal RF-synthesizer block temperature in °C, returned as `[+|-]float_num`.
- Examples: `meas:scal:temp?`, `meas:temp?`

**`STATus:QUEStionable:CONDition?`** — returns instrument status as an integer bitmask; `0` = all OK. Bit 3 set (value `8`) = the currently set power level is outside the calibrated range.
- Example: `STAT:QUES:COND?` → `8`
- Revision note: firmware ≤ pre-R1.3 used `STATus:QUEStionable:[EVENt]?`; **R1.3 replaced it with `STATus:QUEStionable:CONDition?`**.
- Practical confirmation from the factory test program (see §8 below): this bit is specifically how their own automated calibration script flags "requested [power, frequency] point is outside the calibrated area" — a concrete use case beyond the generic manual description.

## 7. Error Codes Seen in Practice

- `-120,"Numeric data error"` — e.g., sending `freq` (a setter) with no numeric argument.
- `-350,"Queue overflow"` — a third error queued while the 2-deep error buffer is already full.

## 8. Worked Example: Automated Multi-Point SCPI Script

This is Advantex's own factory calibration script (from the Test Program, Fig. 5 — "SG8 Level Scan algorithm"), reproduced here because it's a solid template for driving the SG8 programmatically over many frequency/power points and reading back status:

```
*RST → (wait 1000 ms) → *CLS

P = [-20, -15, -12, -10, -9, ..., 26, 28.5] dBm      # 41 power levels
F = [12.5, 25, ..., 8000] MHz                         # 640 frequency points
                                                       # (grid: 1 MHz step 10-100MHz,
                                                       #  10 MHz step 100MHz-1GHz,
                                                       #  25 MHz step 1-8GHz)

OUTP ON                                               # turn RF Out on once, ~500 ms settle

for m in 1..41:
    FREQ F[1]
    POW P[m]                       # set power for this row
    for n in 1..640:
        FREQ F[n]                  # set frequency
        status[m][n] = STAT:QUES:COND?    # 0 = point is within calibration area
        # (external power meter is separately re-tuned to F[n] for correct
        #  detector calibration, then a measurement is taken there — not an
        #  SG8 command, shown here for completeness)
        # wait 100 ms
        # level[m][n] = <power-meter>:MEAS?

OUTP OFF
SYST:ERR?                          # check error buffer at the end
```

Practical takeaways for scripting against this instrument:
- `*RST` then `*CLS` is the standard init sequence; allow ~1 s after `*RST` before issuing further commands.
- Setting `POW` once per outer loop and `FREQ` once per inner loop (rather than re-sending both every iteration) is the pattern the manufacturer itself uses — minimizes traffic given the 64-char/2-deep command buffer limits.
- `STAT:QUES:COND?` is cheap enough to poll every point; use it to validate that a given (power, frequency) combination is actually inside spec before trusting a measurement taken there.
- A ~100 ms settle time between `FREQ` and reading downstream equipment is what the factory uses; shorter delays risk catching the ~5 ms RF Out level glitch or unsettled APC gain (see general manual §3).

## 9. Python / PyVISA Integration Note

There's no dedicated Python/PyVISA driver package published for the SG8 series, but since it's a plain ASCII-SCPI device over a virtual COM port (115200-8-N-1, no flow control, as above), it works directly with `pyvisa` via an `ASRL` (serial) resource string, or with plain `pyserial`, without any special driver beyond the CP2102 USB-UART driver noted in §1. A minimal pyvisa example:

```python
import pyvisa

rm = pyvisa.ResourceManager()
inst = rm.open_resource("ASRL/dev/ttyUSB0::INSTR")   # or "ASRL3::INSTR" on Windows (COM3)
inst.baud_rate = 115200
inst.data_bits = 8
inst.parity = pyvisa.constants.Parity.none
inst.stop_bits = pyvisa.constants.StopBits.one
inst.flow_control = pyvisa.constants.VI_ASRL_FLOW_NONE
inst.write_termination = "\n"
inst.read_termination = "\n"

print(inst.query("*IDN?"))
inst.write("*RST")
inst.write("FREQ 100MHz")
inst.write("POW -1dBm")
inst.write("OUTP ON")
```

Given the 2-deep command buffer and lack of full status-system support, prefer `*OPC?` after any command whose completion you need to confirm before issuing the next one (§3), rather than assuming instantaneous execution.

## 10. Revision History Relevant to Remote Control

| Firmware / Doc rev | Change |
|---|---|
| R1.0 (doc 1.0, Jan 2010) | Remote control (SCPI) operations added |
| Doc 1.1 (Nov 2010) | Revised block diagram / panel figures |
| Doc 1.2 (Jun 2011) | Added SG8-HPSS01M description, revised Settings▸Reference Freq and Info menu items, new SCPI commands |
| Doc 1.3 (Oct 2011) | Added Package Contents / Installation-Maintenance-Safety / Disposal sections, new SCPI command `STAT:QUES?` |
| R1.3 / Doc 1.4 (Dec 13, 2011) | `STATus:QUEStionable:[EVENt]?` replaced by `STATus:QUEStionable:CONDition?`; added `*OPC?` |

---

## Quick Reference

- Serial: 115200-8-N-1, no flow control, RS-232 or USB (CP2102 virtual COM)
- Max SCPI command string length: 64 characters
- Command buffer depth: 2 (send at most one command ahead of the currently-executing one)
- Error buffer depth: 2 (FIFO; `SYST:ERR?` pops, `*CLS` clears all)
- Always `*RST` at the start of a session; use `*OPC?` instead of fixed delays between back-to-back commands
- `STAT:QUES:COND?` bit 3 (`8`) = requested power is outside the calibrated area for that frequency
