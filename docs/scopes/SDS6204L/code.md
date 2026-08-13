# Native API examples

[Source Manual](https://www.siglenteu.com/wp-content/uploads/dlm_uploads/2024/09/SDS6000L_ProgrammingGuide_EN11G.pdf)

!!! warning 
    AI generated summary, be sure to double check the reference manual for critical applications.

---

## 1. Scope Compatibility & Firmware Matrix

*(Ref: Manual Section "Supported Models", Doc p. 18 / PDF p. 19)*

| Oscilloscope Series | Minimum Firmware Version for Tree SCPI Commands |
| :--- | :--- |
| **SDS5000X** | `0.9.0` and later |
| **SDS2000X Plus** | `1.3.5R3` and later |
| **SDS6000 Pro / SDS6000A** | `1.1.7.0` and later |
| **SHS800X / SHS1000X** (Handheld) | `1.1.9` and later |
| **SDS2000X HD** | `1.2.0.2` and later |
| **SDS6000L** (Low Profile) | `1.0.1.0` and later |
| **SDS1000X HD** | `1.1.0.2` and later |
| **SDS7000A** | `1.0.7.0` and later |
| **SDS800X HD** | `1.1.3.1` and later |
| **SDS3000X HD** | `1.0.3.0` and later |

---

## 2. Communication Interfaces & Connections

*(Ref: Manual Section "Programming Overview", Doc p. 19–24 / PDF p. 20–25)*

Users can remotely program the instrument through **USB** (USBTMC) or **LAN** (VXI-11, Raw Socket, and Telnet).

```
                      +-----------------------------+
                      |   Python Automation Host    |
                      +--------------+--------------+
                                     |
             +-----------------------+-----------------------+
             |                                               |
   USB Interface (USBTMC)                         LAN Interface (Ethernet)
   Resource: USB0::...::INSTR                     IP Address: e.g., 10.12.59.1
   Library: PyVISA + NI/Keysight VISA              +---------+----------+---------+
                                                   |         |          |         |
                                               VXI-11    Raw TCP     Telnet     VNC
                                               Port:    Port 5025   Port 5024  Port 5028
                                                VISA     Native      Terminal   Remote
                                               Driver    Socket      Prompt     Screen
```

### A. Connection Methods & Address Specifications

1. **USB (USBTMC)** *(Ref: Doc p. 19, 22 / PDF p. 20, 23)*
   * **VISA Resource String Format:** `USB0::0xF4EC::<PID>::<Serial_Number>::INSTR`
   * **Requirements:** National Instruments NI-VISA, Keysight VISA, or PyVISA with `libusb`.

2. **LAN (VXI-11 Protocol)** *(Ref: Doc p. 19 / PDF p. 20)*
   * **VISA Resource String Format:** `TCPIP0::<IP_Address>::inst0::INSTR`
   * Handles handshaking, buffers, and timeouts automatically via VISA layer.

3. **LAN (Raw Socket)** *(Ref: Doc p. 24 / PDF p. 25)*
   * **Address / Port:** IP Address + Port **`5025`**
   * **VISA Resource String Format:** `TCPIP0::<IP_Address>::5025::SOCKET`
   * **Native Python:** Connect directly via Python's built-in `socket` library. **Fastest performance.**
   * *Requirement:* Every command string **must** terminate with a newline character (`\n`).

4. **LAN (Telnet)** *(Ref: Doc p. 23 / PDF p. 24)*
   * **Address / Port:** IP Address + Port **`5024`**
   * Command line style interactive prompt (`SCPI>`).

---

## 3. SCPI Command Language Rules

*(Ref: Manual Section "Introduction to the SCPI Language", Doc p. 25–28 / PDF p. 26–29)*

### A. Structure and Case Rules
* SCPI commands are organized hierarchically in a tree structure separated by colons (`:`).
* Commands are case-insensitive. Capital letters denote the **Short Form** abbreviation.
  * **Long Form:** `:CHANnel1:SCALe 1.0`
  * **Short Form:** `:CHAN1:SCAL 1.0`
* A space separates the command header path from the parameter payload.
* Queries append a question mark (`?`) to the end of the keyword (e.g., `:CHANnel1:SCALe?`).

### B. Parameter Data Types
*(Ref: Doc p. 26–27 / PDF p. 27–28)*

1. **Enumeration:** Unquoted string keywords (e.g., `ON`, `OFF`, `FAST`, `SLOW`, `C1`).
2. **Numeric Arguments:**
   * `<NR1>`: Signed integer (e.g., `100`, `-5`).
   * `<NR2>`: Floating-point without exponent (e.g., `1.25`, `-0.05`).
   * `<NR3>`: Floating-point in scientific/exponential notation (e.g., `1.0E-03`, `2.5E+06`).
   * `<bin>`: IEEE 488.2 Arbitrary Block Binary Data (`#<digit_count><byte_count><raw_bytes>`).
3. **Quoted Strings (`<qstring>`):** Text enclosed in double quotes (e.g., `"C1"`, `"local/SIGLENT/test.bin"`).
   * Double quotes inside double quotes are invalid. Characters inside strings are automatically converted to uppercase by the scope.

---

## 4. Waveform Binary Architecture & Signal Reconstruction Math

*(Ref: Manual Section "WAVeform Commands", Doc p. 746–764 / PDF p. 747–765 & "Common Command Examples", Doc p. 837–853 / PDF p. 838–854)*

Waveform transfers consist of reading the descriptor block preamble (`:WAVeform:PREamble?`) first, followed by reading the binary payload (`:WAVeform:DATA?`).

---

### A. Preamble Descriptor Memory Map (`WAVEDESC`)
*(Ref: Table 1, Doc p. 754–755 / PDF p. 755–756)*

When `:WAVeform:PREamble?` is called, the scope returns a header `#9<9_digits>` followed by the binary memory block below:

| Offset Address (Dec) | Offset Address (Hex) | Struct Format | Length (Bytes) | Field Name | Technical Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0 ~ 15` | `0x00 ~ 0x0F` | `16s` | 16 | **Descriptor Name** | Always `"WAVEDESC\0\0\0\0\0\0\0\0"` |
| `16 ~ 31` | `0x10 ~ 0x1F` | `16s` | 16 | **Template Name** | Always `"WAVEACE\0\0\0\0\0\0\0\0\0"` |
| `32 ~ 33` | `0x20` | `h` (short) | 2 | **`comm_type`** | Data format: `0` = 8-bit (BYTE), `1` = 16-bit (WORD) |
| `34 ~ 35` | `0x22` | `h` (short) | 2 | **`comm_order`** | Byte order: `0` = LSB (Little Endian), `1` = MSB (Big Endian) |
| `36 ~ 39` | `0x24` | `i` (long) | 4 | **`wave_desc_length`** | Length of WAVEDESC header block (346 bytes) |
| `60 ~ 63` | `0x3C` | `i` (long) | 4 | **`wave_array_1`** | Number of bytes in transmitted payload |
| `76 ~ 91` | `0x4C` | `16s` | 16 | **Instrument Name** | Always `"Siglent SDS"` |
| `116 ~ 119`| `0x74` | `i` (long) | 4 | **`one_frame_pts`** | Sample points count in single sequence frame/screen |
| `132 ~ 135`| `0x84` | `i` (long) | 4 | **`first_point`** | Starting point offset (same as `:WAV:STAR`) |
| `136 ~ 139`| `0x88` | `i` (long) | 4 | **`data_interval`** | Interval setting step between points |
| `144 ~ 147`| `0x90` | `i` (long) | 4 | **`read_frames`** | Number of sequence frames returned in this query |
| `148 ~ 151`| `0x94` | `i` (long) | 4 | **`sum_frames`** | Total number of acquired sequence frames |
| `156 ~ 159`| `0x9C` | `f` (float) | 4 | **`vdiv`** | Vertical scale (V/div) **without** probe attenuation |
| `160 ~ 163`| `0xA0` | `f` (float) | 4 | **`voffset`** | Vertical offset (V) **without** probe attenuation |
| `164 ~ 167`| `0xA4` | `f` (float) | 4 | **`code_per_div`** | ADC raw counts per vertical grid division |
| `172 ~ 173`| `0xAC` | `h` (short) | 2 | **`adc_bit`** | Hardware ADC Resolution (`8`, `10`, or `12` bits) |
| `176 ~ 179`| `0xB0` | `f` (float) | 4 | **`interval`** | Sampling time interval ($T_{\text{sample}} = 1 / \text{Sample Rate}$) |
| `180 ~ 187`| `0xB4` | `d` (double)| 8 | **`delay`** | Horizontal trigger offset relative to center (seconds) |
| `324 ~ 325`| `0x144` | `h` (short) | 2 | **`tdiv`** | Timebase index (maps to `tdiv_enum`) |
| `328 ~ 331`| `0x148` | `f` (float) | 4 | **`probe`** | Probe attenuation multiplier (e.g. `1.0`, `10.0`) |
| `346 ~ ...` | `0x15A` | Block | 16/frame | **`time_stamp`** | Array of 16-byte timestamp structures for Sequence mode |

---

### B. Mathematical Waveform Reconstruction

#### 1. Analog Channel Voltage ($V$)
*(Ref: Doc p. 758 / PDF p. 759)*

$$V_i = \text{code\_value}_i \times \left( \frac{\text{vdiv} \times \text{probe}}{\text{code\_per\_div}} \right) - (\text{voffset} \times \text{probe})$$

* **`code_value`**: Signed integer (`b` for 8-bit, `h` for >8-bit left-aligned word).
* **`vdiv`**: Float at preamble address `0x9C`.
* **`voffset`**: Float at preamble address `0xA0`.
* **`code_per_div`**: Float at preamble address `0xA4` (typically `30` or model dependent).
* **`probe`**: Float at preamble address `0x148`.

#### 2. Horizontal Time Axis ($T$)
*(Ref: Doc p. 759 / PDF p. 760)*

$$T_i = \text{delay} - \left( \frac{\text{timebase} \times \text{GRID\_NUM}}{2} \right) + (i \times \text{interval})$$

* **`GRID_NUM`**: `10` for SDS benchtop models (`12` for SHS handheld models).
* **`timebase`**: Resolved from `tdiv` enum index at preamble address `0x144`.
* **`delay`**: Double float at preamble address `0xB4`.
* **`interval`**: Float at preamble address `0xB0`.
* **`i`**: Sample index ($0, 1, 2, \dots, N-1$).

#### 3. Digital Channels Bit-Unpacking
*(Ref: Doc p. 760–761 / PDF p. 761–762)*
Digital channels pack 8 sample points per byte (1 bit/sample). Bit $n$ ($0 \le n \le 7$) of byte $B$:
$$\text{Logic State}_n = (B \gg n) \ \& \ 1$$

#### 4. Math FFT Real & Imaginary Reconstruction
*(Ref: Doc p. 845–846 / PDF p. 846–847)*
FFT buffers return pairs of 32-bit floats: $[R_0, I_0, R_1, I_1, \dots, R_{N-1}, I_{N-1}]$.
* **Linear Amplitude:** $A_i = \sqrt{R_i^2 + I_i^2}$ (in Normal mode).
* **Decibel Conversion:**
  $$\text{dBVrms} = 20 \times \log_{10}(A_i)$$
  $$\text{dBm} = 10 \times \log_{10}\left( \frac{A_i^2}{\text{Load} \times 10^{-3}} \right)$$

#### 5. Sequence Mode Timestamp Parsing (16 Bytes/Frame)
*(Ref: Doc p. 848 / PDF p. 849)*
Located at byte offset `346` (`0x15A`) in the preamble block:

```python
# Each frame timestamp takes 16 bytes:
seconds = struct.unpack('d', time_block[0x00:0x08])[0]  # Double float
minutes = int.from_bytes(time_block[0x08:0x09], 'big')   # Unsigned char
hours   = int.from_bytes(time_block[0x09:0x0A], 'big')   # Unsigned char
days    = int.from_bytes(time_block[0x0A:0x0B], 'big')   # Unsigned char
months  = int.from_bytes(time_block[0x0B:0x0C], 'big')   # Unsigned char
year    = struct.unpack('h', time_block[0x0C:0x0E])[0]  # Signed short
```

---

## 5. Comprehensive SCPI Command Subsystem Directory

*(Ref: Manual Section "Commands & Queries", Doc p. 28–800 / PDF p. 29–801)*

Below is an alphabetical breakdown of all command subsystems, headers, short forms, parameters, and manual references.

---

### A. Common (`*`) Commands
*(Ref: Doc p. 29–31 / PDF p. 30–32)*

* `*IDN?`: Returns instrument identification (`Siglent Technologies,<model>,<serial_number>,<firmware>`).
* `*OPC?`: Returns `1` when all pending operations finish.
* `*RST`: Resets oscilloscope to factory default setup.

---

### B. Root (`:`) Commands
*(Ref: Doc p. 32–34 / PDF p. 33–35)*

* `:AUToset`: Automatically adjusts vertical, horizontal, and trigger controls.
* `:PRINt? <type>[,<format>]`: Captures display screenshot.
  * `<type>`: `BMP` | `PNG`
  * `<format>`: `NORMal` | `INVerted`
* `:FORMat:DATA <option>[,<digit>]`: Sets precision format for returned floating point values (`SINGle` [7 digits], `DOUBle` [14 digits], `CUSTom,<digit>` [1..64]).

---

### C. ACQUire Commands
*(Ref: Doc p. 35–47 / PDF p. 36–48)*

* `:ACQuire:AMODe <rate>`: Capture rate mode (`FAST` | `SLOW`).
* `:ACQuire:CSWeep`: Clears acquisition sweeps/history and restarts.
* `:ACQuire:INTerpolation <state>`: Interpolation mode (`ON` = $Sin(x)/x$, `OFF` = Linear).
* `:ACQuire:MMANagement <mem_mode>`: Memory management (`AUTO` | `FSRate` [Fixed Sampling Rate] | `FMDepth` [Fixed Memory Depth]).
* `:ACQuire:MODE <type>`: Display mode (`YT` | `XY` | `ROLL`).
* `:ACQuire:MDEPth <size>`: Sets maximum memory depth (e.g. `10k`, `1M`, `10M`, `100M`, `250M`, `1G`).
* `:ACQuire:NUMACq?`: Returns total acquisitions occurred since start.
* `:ACQuire:POINts?`: Returns sampled points count of current screen waveform.
* `:ACQuire:RESolution <bit>`: Sets ADC resolution for SDS2000X Plus (`8Bits` | `10Bits`).
* `:ACQuire:SEQuence <state>`: Enables/disables Sequence mode (`ON` | `OFF`).
* `:ACQuire:SEQuence:COUNt <count>`: Sets segment count for Sequence mode.
* `:ACQuire:SRATe?`: Returns current sampling rate in S/sec.
* `:ACQuire:TYPE <type>`: Acquisition mode (`NORMal` | `PEAK` | `AVERage,<times>` | `ERES,<bits>`).
  * `<times>`: `4|16|32|64|128|256|512|1024|2048|4096|8192`
  * `<bits>`: `0.5|1.0|1.5|2.0|2.5|3.0|3.5|4.0`

---

### D. CHANnel Commands
*(Ref: Doc p. 48–62 / PDF p. 49–63)*

* `:CHANnel:REFerence <type>`: Vertical reference expansion strategy (`OFFSet` | `POSition`).
* `:CHANnel<n>:BWLimit <bw>`: Low-pass bandwidth limit (`FULL` | `20M` | `200M`).
* `:CHANnel<n>:COUPling <type>`: Input coupling (`DC` | `AC` | `GND`).
* `:CHANnel<n>:IMPedance <ohm>`: Input impedance (`ONEMeg` | `FIFTy`).
* `:CHANnel<n>:INVert <state>`: Inverts signal trace mathematically (`ON` | `OFF`).
* `:CHANnel<n>:LABel <state>`: Turns channel label display `ON` or `OFF`.
* `:CHANnel<n>:LABel:TEXT <qstring>`: Sets label string (max 20 ASCII chars).
* `:CHANnel<n>:OFFSet <value>`: Sets vertical offset in Volts (`<NR3>`).
* `:CHANnel<n>:PROBe <attenuation>[,<value>]`: Sets probe attenuation (`DEFault` [1X] | `VALue,<ratio>` [1E-6 to 1E6]).
* `:CHANnel<n>:SCALe <scale>`: Sets vertical scale in Volts/div (`<NR3>`).
* `:CHANnel<n>:SKEW <skew_value>`: Sets deskew time in seconds (`[-1.00E-07, 1.00E-07]`).
* `:CHANnel<n>:SWITch <state>`: Enables/disables physical channel trace (`ON` | `OFF`).
* `:CHANnel<n>:UNIT <unit>`: Sets signal measurement units (`V` | `A`).
* `:CHANnel<n>:VISible <state>`: Sets display visibility (`ON` | `OFF`).

---

### E. COUNter Commands
*(Ref: Doc p. 63–76 / PDF p. 64–77)*

* `:COUNter <state>`: Enables frequency counter (`ON` | `OFF`).
* `:COUNter:CURRent?`: Returns current frequency counter measurement value.
* `:COUNter:LEVel <value>`: Sets counter trigger level voltage.
* `:COUNter:MODE <type>`: Counter mode (`FREQuency` | `PERiod` | `TOTalizer`).
* `:COUNter:SOURce <source>`: Sets source channel (`C1`..`C4`).
* `:COUNter:STATistics <state>`: Counter statistics switch (`ON` | `OFF`).
* `:COUNter:STATistics:RESet`: Resets counter statistics.
* `:COUNter:STATistics:VALue?`: Returns `<current>,<mean>,<min>,<max>,<stdev>,<count>`.
* `:COUNter:TOTalizer:GATE <state>`: Totalizer gate switch (`ON` | `OFF`).
* `:COUNter:TOTalizer:RESet`: Resets totalizer count.

---

### F. CURSor Commands
*(Ref: Doc p. 77–135 / PDF p. 78–136)*

* `:CURSor <state>`: Master cursor switch (`ON` | `OFF`).
* `:CURSor:TAGStyle <type>`: Cursor label tag type (`FIXed` | `FOLLowing`).
* `:CURSor:XREFerence <type>`: X-cursor expansion strategy (`DELay` | `POSition`).
* `:CURSor:YREFerence <type>`: Y-cursor expansion strategy (`OFFSet` | `POSition`).
* **Single Group Cursors:** *(Ref: Doc p. 82–92 / PDF p. 83–93)*
  * `:CURSor:MODE <type>`: Mode (`TRACk` | `MANual[,<mode>]` | `MEASure`), where `<mode>` is `X|Y|XY`.
  * `:CURSor:SOURce1 <src>` / `:SOURce2 <src>`: C1..C4, Z1..Z4, F1..F4, M1..M4, REF, DIGital, HISTOGram.
  * `:CURSor:X1 <val>` / `:X2 <val>` / `:Y1 <val>` / `:Y2 <val>`: Sets absolute cursor positions.
  * `:CURSor:XDELta?` / `:YDELta?` / `:IXDelta?`: Returns $\Delta X$, $\Delta Y$, or $1/\Delta X$.
* **Multiple Cursors:** *(Ref: Doc p. 93–135 / PDF p. 94–136)*
  * `:CURSor:MANual:X<n> <state>`, `:CURSor:MANual:Y<n> <state>`, `:CURSor:TRACk<n> <state>`, `:CURSor:XY:X<n> <state>`

---

### G. DECode Commands (Serial Protocol Decoding)
*(Ref: Doc p. 136–238 / PDF p. 137–239)*

* `:DECode <state>`: Master decode switch (`ON` | `OFF`).
* `:DECode:LIST <state>`: Displays decode list table (`OFF` | `D1` | `D2`).
* `:DECode:BUS<n> <state>`: Bus decode switch (`ON` | `OFF`).
* `:DECode:BUS<n>:FORMat <format>`: Display format (`BINary` | `DECimal` | `HEX` | `ASCii`).
* `:DECode:BUS<n>:PROTocol <proto>`: Protocols (`IIC` | `SPI` | `UART` | `CAN` | `LIN` | `FLEXray` | `CANFd` | `IIS` | `M1553` | `SENT` | `MANchester` | `A429` | `USB20`).
* `:DECode:BUS<n>:RESult?`: Queries displayed decoded result string.
* `:DECode:LIST<n>:RESult?`: Returns complete decoded list CSV text.

---

### H. DIGital Commands [MSO Option]
*(Ref: Doc p. 239–254 / PDF p. 240–255)*

* `:DIGital <state>`: Master digital channels switch (`ON` | `OFF`).
* `:DIGital:ACTive <digital>`: Sets active digital channel (`D0`..`D15`).
* `:DIGital:D<d> <state>`: Turns single channel D0..D15 `ON` or `OFF`.
* `:DIGital:BUS<n>:DISPlay <state>`: Displays digital bus (`ON` | `OFF`).
* `:DIGital:BUS<n>:FORMat <format>`: Bus format (`BINary` | `DECimal` | `UDECimal` | `HEX`).
* `:DIGital:BUS<n>:MAP <src1>[,<src2>...]`: Maps digital channels to bus.
* `:DIGital:HEIGht <val>`: Sets digital display height in divisions (`[4.0, 8.0]`).
* `:DIGital:POSition <val>`: Sets vertical position of digital channels.
* `:DIGital:THReshold<n> <type>`: Threshold for digital group (`1` = D0-D7, `2` = D8-D15; `<type>`: `TTL` | `CMOS` | `LVCMOS33` | `LVCMOS25` | `CUSTom,<voltage>`).

---

### I. DISPlay Commands
*(Ref: Doc p. 255–269 / PDF p. 256–270)*

* `:DISPlay:AXIS <state>`: Shows or hides grid axis labels (`ON` | `OFF`).
* `:DISPlay:AXIS:MODE <mode>`: Axis mode (`FIXed` | `MOVing`).
* `:DISPlay:AXIS:POSition <pos>`: Axis location (`LEFT` | `MIDDle` | `RIGHt`).
* `:DISPlay:BACKlight <val>`: Sets LCD backlight brightness (`0` to `100`%).
* `:DISPlay:CLEar`: Clears persistence and current display traces.
* `:DISPlay:COLor <state>`: Color grade mode (`ON` | `OFF`).
* `:DISPlay:GRATicule <val>`: Sets grid intensity (`0` to `100`%).
* `:DISPlay:GRIDstyle <type>`: Grid style (`FULL` | `LIGHt` | `NONE`).
* `:DISPlay:HIDemenu`: Hides side menu panel immediately.
* `:DISPlay:INTensity <val>`: Waveform trace intensity (`0` to `100`%).
* `:DISPlay:MENU <type>`: Menu style (`EMBedded` | `FLOating`).
* `:DISPlay:MENU:HIDE <time>`: Auto-hide menu timer (`OFF` | `3S` | `5S` | `10S` | `30S` | `60S`).
* `:DISPlay:PERSistence <time>`: Persistence duration (`OFF` | `INFinite` | `100MS` | `200MS` | `500MS` | `1S` | `5S` | `10S` | `30S`).
* `:DISPlay:TYPE <type>`: Draw mode (`VECTor` | `DOT`).

---

### J. DVM Commands (Digital Voltmeter)
*(Ref: Doc p. 270–277 / PDF p. 271–278)*

* `:DVM <state>`: DVM switch (`ON` | `OFF`).
* `:DVM:MODE <mode>`: Voltage mode (`DCavg` | `DCRMs` | `ACRMs` | `PKPK` | `AMPLitude`).
* `:DVM:SOURce <src>`: Measurement source channel (`C1`..`C4`).
* `:DVM:CURRent?`: Queries current 3-digit DVM reading.
* `:DVM:HOLD <state>`: Holds measurement display (`ON` | `OFF`).

---

### K. FUNCtion Commands (Math, FFT & Digital Filters)
*(Ref: Doc p. 278–326 / PDF p. 279–327)*

* `:FUNCtion<x> <state>`: Master math switch (`ON` | `OFF`).
* `:FUNCtion<x>:OPERation <op>`: Operators (`ADD` | `SUBTract` | `MULTiply` | `DIVision` | `INTegrate` | `DIFF` | `FFT` | `SQRT` | `ERES` | `AVERage` | `ABSolute` | `SIGN` | `IDENtity` | `NEGation` | `EXP` | `TEN` | `LN` | `LOG` | `INTErpolate` | `MAXHold` | `MINHold` | `FILTer`).
* `:FUNCtion<x>:SOURce1 <src>` / `:SOURce2 <src>`: Source channels (`C1`..`C4`, `Z1`..`Z4`, `F1`..`F4`, `M1`..`M4`).
* `:FUNCtion<x>:SCALe <val>` / `:POSition <val>`: Math vertical scale and position offset.
* `:FUNCtion<x>:AVERage:NUM <num>`: Sets math average count (`4` to `8192`).
* `:FUNCtion<x>:ERES:BITS <bits>`: Sets ERES enhanced resolution bits (`0.5` to `3.0`).
* **FFT Sub-commands:**
  * `:FUNCtion:FFTDisplay <mode>`: FFT view layout (`SPLit` | `FULL` | `EXCLusive`).
  * `:FUNCtion<x>:FFT:AUToset <mode>`: Auto-scale FFT (`SPAN` | `PEAK` | `NORMal`).
  * `:FUNCtion<x>:FFT:HCENter <freq>` / `:SPAN <freq>`: Center frequency & span in Hz.
  * `:FUNCtion<x>:FFT:LOAD <load>`: Impedance load for dBm calculation (`1` to `1000000` $\Omega$).
  * `:FUNCtion<x>:FFT:MODE <mode>`: Mode (`NORMal` | `MAXHold` | `AVERage[,<num>]`).
  * `:FUNCtion<x>:FFT:POINts <pts>`: Max points (`1k` up to `32M`).
  * `:FUNCtion<x>:FFT:RLEVel <val>` / `:SCALe <val>`: Reference level and vertical scale.
  * `:FUNCtion<x>:FFT:UNIT <unit>`: Vertical units (`DBVrms` | `Vrms` | `DBm`).
  * `:FUNCtion<x>:FFT:WINDow <win>`: Windowing (`RECTangle` | `BLACkman` | `HANNing` | `HAMMing` | `FLATtop`).
  * `:FUNCtion<x>:FFT:SEARch <type>`: Search mode (`OFF` | `PEAK` | `MARKer`).
  * `:FUNCtion<x>:FFT:SEARch:RESult?`: Returns peak list (`Peaks,<no>,<freq>,<ampl>;...`).
* **Digital Filter Sub-commands:**
  * `:FUNCtion<x>:FILTer:TYPe <type>`: Filter type (`LPASs` | `HPASs` | `BPASs` | `BREJect`).
  * `:FUNCtion<x>:FILTer:HFRequency <val>` / `:LFRequency <val>`: Cutoff frequencies.

---

### L. HISTORy Commands
*(Ref: Doc p. 327–333 / PDF p. 328–334)*

* `:HISTORy <state>`: Master history mode switch (`ON` | `OFF`).
* `:HISTORy:FRAMe <num>`: Selects active frame index to view.
* `:HISTORy:INTERval <value>`: Sets frame playback time interval in seconds (`[1E-6, 1]`).
* `:HISTORy:LIST <state>`: Displays history list table (`OFF` | `ON[,TIME|DELTa]`).
* `:HISTORy:PLAY <state>`: Playback control (`BACKWards` | `PAUSe` | `FORWards`).
* `:HISTORy:TIME?`: Returns acquisition timestamp string (`HH:MM:SS.us`).

---

### M. MEASure Commands
*(Ref: Doc p. 334–372 / PDF p. 335–373)*

* `:MEASure <state>`: Master measure switch (`ON` | `OFF`).
* `:MEASure:MODE <type>`: Measurement layout (`SIMPle` | `ADVanced`).
* `:MEASure:RDISplay <type>`: Result panel layout (`EMBedded` | `FLOating`).
* **Advanced Measurement Subsystem:**
  * `:MEASure:ADVanced:CLEar`: Clears all custom measurement slots.
  * `:MEASure:ADVanced:P<n> <state>`: Enables slot P1..P12 (`ON` | `OFF`).
  * `:MEASure:ADVanced:P<n>:TYPE <type>`: Parameters: `PKPK`, `MAX`, `MIN`, `AMPL`, `TOP`, `BASE`, `CMEAN`, `MEAN`, `STDEV`, `RMS`, `CRMS`, `OVSN`, `OVSP`, `PER`, `FREQ`, `PWID`, `NWID`, `DUTY`, `NDUTY`, `RISE`, `FALL`, `DELAY`, `PAREA`, `AREA`, `PHA`, `SKEW`, `FRR`, `FRF`, `FFR`, `FFF`, `LRR`, `LRF`, `LFR`, `LFF`, `PSLOPE`, `NSLOPE`, `TSR`, `TSF`, `THR`, `THF`, `DITMe1`..`4`.
  * `:MEASure:ADVanced:P<n>:SOURce1 <src>` / `:SOURce2 <src>`: Source channel(s).
  * `:MEASure:ADVanced:P<n>:VALue?`: Returns current measured scalar value.
  * `:MEASure:ADVanced:P<n>:STATistics? <type>`: Returns stats (`ALL` | `CURRent` | `MEAN` | `MAXimum` | `MINimum` | `STDev` | `COUNt`).
  * `:MEASure:ADVanced:P<n>:SHIStory? [<count>]`: Returns statistical history array.
  * `:MEASure:ADVanced:STATistics <state>`: Master statistics table switch (`ON` | `OFF`).
  * `:MEASure:ADVanced:STATistics:RESet`: Resets measurement statistical counts.
* **Simple Measurement Subsystem:**
  * `:MEASure:SIMPle:ITEM <parameter>,<state>`: Adds/removes simple item.
  * `:MEASure:SIMPle:VALue? <parameter>`: Queries simple measurement value.

---

### N. MEMory Commands (Stored Reference Waveform Memory Traces)
*(Ref: Doc p. 373–382 / PDF p. 374–383)*

* `:MEMory<m>:SWITch <state>`: Memory trace display switch M1..M4 (`ON` | `OFF`).
* `:MEMory<m>:IMPort <source>`: Imports waveform into memory trace (`C1`..`C4`, `F1`..`F4`, or path string `"local/test.bin"`).
* `:MEMory<m>:VERTical:SCALe <val>` / `:POSition <val>`: Vertical scale and position offset.
* `:MEMory<m>:HORizontal:SCALe <val>` / `:POSition <val>`: Horizontal scale and position offset.

---

### O. MTEst Commands (Mask Testing)
*(Ref: Doc p. 383–393 / PDF p. 384–394)*

* `:MTESt <state>`: Master mask test switch (`ON` | `OFF`).
* `:MTESt:SOURce <src>`: Source channel (`C1`..`C4`, `Z1`..`Z4`).
* `:MTESt:OPERate <state>`: Starts/Stops mask testing (`ON` | `OFF`).
* `:MTESt:COUNt?`: Returns pass/fail results (`FAIL,<num>,PASS,<num>,TOTAL,<num>`).
* `:MTESt:MASK:CREate <x_margin>,<y_margin>`: Generates mask with margins.
* `:MTESt:MASK:LOAD <location>`: Loads mask (`INTernal,<1..4>` or `EXTernal,"path"`).
* `:MTESt:FUNCtion:BUZZer <state>`: Beeper on failure (`ON` | `OFF`).
* `:MTESt:FUNCtion:SOF <state>`: Stop-on-fail switch (`ON` | `OFF`).

---

### P. SAVE & RECall Commands
*(Ref: Doc p. 394–413 / PDF p. 395–414)*

* `:SAVE:IMAGe <path>,<type>,<invert>[,<menu>]`: Saves screenshot.
  * `<path>`: `"U-disk0/SIGLENT/screen.png"`
  * `<type>`: `BMP` | `JPG` | `PNG`
  * `<invert>`: `OFF` (exact screen color) | `ON` (inverted white background)
  * `<menu>`: `MON` (includes menus) | `MOFf` (hides menus)
* `:SAVE:CSV <path>,<src>,<state>`: Exports CSV waveform file to storage.
* `:SAVE:BINary <path>,<src>`: Exports raw binary `.bin` waveform file.
* `:SAVE:MATLab <path>,<src>`: Exports MATLAB `.mat` waveform file.
* `:SAVE:SETup <setup_num>`: Saves scope setup (`INTernal,<1..10>` or `EXTernal,"path.xml"`).
* `:RECall:SETup <location>`: Recalls setup file.
* `:RECall:REFerence <loc>,<path>`: Recalls reference waveform file.
* `:RECall:FDEFault`: Recalls factory default settings.

---

### Q. SYSTem Commands
*(Ref: Doc p. 452–471 / PDF p. 453–472)*

* `:SYSTem:BUZZer <state>`: System beeper switch (`ON` | `OFF`).
* `:SYSTem:CLOCk <source>`: Clock source (`EXT` | `IN_ON` [10MHz out ON] | `IN_OFF`).
* `:SYSTem:COMMunicate:LAN:IPADdress <qstring>`: Sets static IP address.
* `:SYSTem:COMMunicate:LAN:SMASk <qstring>`: Sets subnet mask.
* `:SYSTem:COMMunicate:LAN:GATeway <qstring>`: Sets network gateway.
* `:SYSTem:COMMunicate:LAN:TYPE <state>`: LAN mode (`STATIC` | `DHCP`).
* `:SYSTem:COMMunicate:VNCPort <val>`: VNC server port (`5900` to `5999`).
* `:SYSTem:DATE <date>`: Sets scope system date (`YYYYMMDD`).
* `:SYSTem:TIME <time>`: Sets scope system time (`HHMMSS`).
* `:SYSTem:LANGuage <lang>`: Sets UI language.
* `:SYSTem:PON <state>`: Auto Power-On-Line recovery (`ON` | `OFF`).
* `:SYSTem:REBoot`: Reboots the instrument.
* `:SYSTem:SHUTdown`: Shuts down the instrument.
* `:SYSTem:REMote <state>`: Locks/unlocks front panel touchscreen (`ON` | `OFF`).
* `:SYSTem:SELFCal`: Runs internal self-calibration.
* `:SYSTem:SSAVer <time>`: Screensaver timeout (`OFF` | `1MIN` | `5MIN` | `10MIN` | `30MIN` | `60MIN`).
* `:SYSTem:TOUCh <state>`: Enables/disables touchscreen (`ON` | `OFF`).

---

### R. TIMebase Commands
*(Ref: Doc p. 472–479 / PDF p. 473–480)*

* `:TIMebase:SCALe <value>`: Main horizontal timebase scale in seconds/div.
* `:TIMebase:DELay <value>`: Main horizontal trigger delay in seconds.
* `:TIMebase:REFerence <type>`: Scale expansion center strategy (`DELay` | `POSition`).
* `:TIMebase:REFerence:POSition <val>`: Reference center offset percentage (`0` to `100`).
* `:TIMebase:WINDow <state>`: Enables/disables Zoomed window mode (`ON` | `OFF`).
* `:TIMebase:WINDow:DELay <value>`: Sets delay offset for Zoom window.
* `:TIMebase:WINDow:SCALe <value>`: Sets timebase scale for Zoom window.

---

### S. TRIGger Commands
*(Ref: Doc p. 480–745 / PDF p. 481–746)*

* `:TRIGger:MODE <mode>`: Sweep mode (`AUTO` | `NORMal` | `SINGle` | `FTRIG` [Force Trigger]).
* `:TRIGger:RUN`: Starts acquisition.
* `:TRIGger:STOP`: Stops acquisition.
* `:TRIGger:STATus?`: Returns trigger state (`Arm` | `Ready` | `Auto` | `Trig'd` | `Stop` | `Roll`).
* `:TRIGger:TYPE <type>`: Trigger type (`EDGE` | `PULSE` | `SLOPe` | `INTerval` | `PATTern` | `RUNT` | `WINDow` | `DROPout` | `VIDeo` | `QUALified` | `NEDGe` | `DELay` | `SHOLd` | `IIC` | `SPI` | `UART` | `LIN` | `CAN` | `FLEXray` | `CANFd` | `IIS` | `M1553` | `SENT` | `A429`).
* **Edge Trigger Commands:** *(Ref: Doc p. 485–495 / PDF p. 486–496)*
  * `:TRIGger:EDGE:SOURce <src>`: Channel source (`C1`..`C4`, `D0`..`D15`, `EX`, `EX5`, `LINE`).
  * `:TRIGger:EDGE:SLOPe <type>`: Slope (`RISing` | `FALLing` | `ALTernate`).
  * `:TRIGger:EDGE:LEVel <value>`: Threshold voltage level.
  * `:TRIGger:EDGE:COUPling <mode>`: Coupling (`DC` | `AC` | `LFREJect` | `HFREJect`).
  * `:TRIGger:EDGE:HOLDoff <type>`: Holdoff type (`OFF` | `EVENts` | `TIME`).
  * `:TRIGger:EDGE:HLDTime <val>` / `:HLDEVent <val>`: Holdoff duration or event count.

---

### T. WAVeform Commands (Data Extraction)
*(Ref: Doc p. 746–764 / PDF p. 747–765)*

* `:WAVeform:SOURce <source>`: Selects source waveform (`C1`..`C4`, `F1`..`F4`, `D0`..`D15`).
* `:WAVeform:WIDTh <type>`: Selects data width (`BYTE` [8-bit] | `WORD` [16-bit]).
* `:WAVeform:BYTeorder <order>`: Selects endianness for WORD transfers (`LSB` | `MSB`).
* `:WAVeform:STARt <val>`: Starting sample point index for buffer read.
* `:WAVeform:POINt <val>`: Requested point count.
* `:WAVeform:MAXPoint?`: Returns max allowed points transferred per segment read.
* `:WAVeform:PREamble?`: Queries the 346-byte descriptor block (`WAVEDESC`).
* `:WAVeform:DATA?`: Returns raw binary payload block `#9<9_digits><data>`.
* `:WAVeform:SEQuence <frame>,<start>`: Configures Sequence segment frame index to read.

---

### U. WGEN Commands (Built-in Waveform Generator)
*(Ref: Doc p. 764–773 / PDF p. 765–774)*

* `<channel>:OUTPut <state>,LOAD,<load>`: Output switch (`C1:OUTP ON,LOAD,50` or `C1:OUTP ON,LOAD,HZ`).
* `<channel>:BaSic_WaVe <param>,<val>`: Waveform configuration parameters (`WVTP`, `FRQ`, `PERI`, `AMP`, `OFST`, `SYM`, `DUTY`, `STDEV`, `MEAN`, `WIDTH`).
  * *Example:* `C1:BSWV WVTP,SINE,FRQ,1000,AMP,2.0,OFST,0.0`
* `<channel>:ARbWaVe INDEX,<idx>` / `NAME,<name>`: Selects arbitrary waveform pattern.
* `<channel>:SYNC <state>`: Enables/disables front panel sync output.

---

## 6. Complete, Production-Grade Python Automation Suite



### Snippet 1: Dual-Backend Connection Wrapper (PyVISA & Native Sockets)

#### Technical Context & Purpose
When communicating with Siglent oscilloscopes over Ethernet, you can choose between **PyVISA (VXI-11/USBTMC)** or **Native TCP Sockets (Port 5025)**. 
* **PyVISA** handles VISA abstraction, timeouts, and resource management automatically.
* **Native Sockets** bypass the VISA abstraction layer entirely, reducing software overhead and eliminating external driver dependencies.

A critical setting in PyVISA is `chunk_size`. The default chunk size (20 KB) creates severe bottlenecks when downloading multi-megabyte waveform dumps. Increasing `chunk_size` to **20 MB** drastically accelerates binary block transfers over Gigabit LAN.

```python
import visa
import socket
import time

class ScopeConnection:
    def __init__(self, target_address: str, timeout_ms: int = 5000):
        """
         Establishes connection using PyVISA or Native Sockets.
        
         :param target_address: VISA string or IP address.
            - USB VISA String:   'USB0::0xF4EC::0xEE38::0123456789::INSTR'
            - VXI-11 LAN String: 'TCPIP0::10.12.59.1::inst0::INSTR'
            - Raw Socket String: '10.12.59.1' (Connects directly to port 5025)
        """
        self.target = target_address
        self.timeout = timeout_ms / 1000.0  # Convert to seconds for socket
        self.is_raw_socket = not target_address.startswith(("USB", "TCPIP"))

        if self.is_raw_socket:
            # --- Native TCP Socket Backend ---
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect((target_address, 5025))
        else:
            # --- PyVISA Layer Backend ---
            self.rm = visa.ResourceManager()
            self.instr = self.rm.open_resource(target_address)
            self.instr.timeout = timeout_ms
            # Crucial performance boost: Increase buffer chunk size to 20MB
            self.instr.chunk_size = 20 * 1024 * 1024

            if "SOCKET" in target_address:
                # Raw VISA socket resource strings require explicit newlines
                self.instr.read_termination = '\n'
                self.instr.write_termination = '\n'

    def send_cmd(self, scpi_command: str):
        """Sends a SCPI command without expecting a response."""
        if self.is_raw_socket:
            # Raw sockets MUST terminate commands with newline '\n'
            full_cmd = (scpi_command + '\n').encode('utf-8')
            self.sock.sendall(full_cmd)
        else:
            self.instr.write(scpi_command)

    def ask_str(self, scpi_query: str) -> str:
        """Sends a SCPI query and returns the stripped ASCII string response."""
        if self.is_raw_socket:
            self.send_cmd(scpi_query)
            time.sleep(0.05) # Brief pause for buffer fill
            raw_resp = self.sock.recv(4096).decode('utf-8', errors='ignore')
            return raw_resp.strip()
        else:
            return self.instr.query(scpi_query).strip()

    def read_raw_bytes(self) -> bytes:
        """Reads raw binary response from the scope buffer."""
        if self.is_raw_socket:
            # Accumulate bytes until buffer is emptied
            chunks = []
            while True:
                try:
                    chunk = self.sock.recv(1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if len(chunk) < (1024 * 1024):
                        break
                except socket.timeout:
                    break
            return b''.join(chunks)
        else:
            return self.instr.read_raw()

    def close(self):
        """Cleanly releases network and driver handles."""
        if self.is_raw_socket:
            self.sock.close()
        else:
            self.instr.close()
            self.rm.close()
```

#### Commentary:
1. **`chunk_size = 20 * 1024 * 1024`**: PyVISA reads binary blocks in chunks. If `chunk_size` is left at its 20 KB default, reading a 10 Mpt (10 MB) waveform requires 500 internal read cycles, creating massive CPU bottlenecking. Setting `chunk_size` to 20 MB reduces read operations to a single internal buffer read.
2. **`self.sock.sendall(full_cmd)`**: Raw socket communication bypasses VISA's message framing layer. Siglent instruments on Port 5025 parse incoming TCP packets character-by-character; if a command string does not end in `\n`, the scope parser hangs waiting for command termination.
3. **`read_termination = '\n'`**: Instructs VISA to stop waiting for ASCII query responses as soon as a newline byte is received, preventing VISA timeout errors on string queries like `*IDN?`.

---

### Snippet 2: Binary Preamble (`WAVEDESC`) Parsing & Metadata Extraction

*(Ref: Manual Section "WAVeform:PREamble", Doc p. 754–756 / PDF p. 755–757)*

#### Technical Context & Purpose
Before reading binary waveform data, the host application queries `:WAVeform:PREamble?`. The oscilloscope returns a 346-byte descriptor header block (named `WAVEDESC`) containing critical scaling factors, sampling rates, offsets, and hardware resolution settings.

```python
import struct

# Lookup table mapping the timebase index from address 0x144 to seconds/div
# (Ref: Manual Table 2 Enum of Timebase, Doc p. 756 / PDF p. 757)
TDIV_ENUM = [
    200e-12, 500e-12, 1e-9, 2e-9, 5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3, 200e-3, 500e-3,
    1, 2, 5, 10, 20, 50, 100, 200, 500, 1000
]

def parse_wavedesc_preamble(raw_preamble_bytes: bytes) -> dict:
    """
    Parses binary WAVEDESC descriptor preamble into a structured Python dictionary.
    
    :param raw_preamble_bytes: Raw bytes returned by ':WAVeform:PREamble?'
    :return: Dictionary containing parsed scaling parameters and hardware metadata.
    """
    # 1. Locate the start of the binary block header ('#')
    header_start = raw_preamble_bytes.find(b'#')
    if header_start == -1:
        raise ValueError("Invalid preamble block: IEEE 488.2 header '#' not found.")

    # 2. Extract digit count and payload offset (e.g. #9000000346...)
    digit_cnt = int(chr(raw_preamble_bytes[header_start + 1]))
    data_start = header_start + 2 + digit_cnt
    
    # Slice the raw byte stream to isolate the WAVEDESC payload
    descriptor = raw_preamble_bytes[data_start:]

    # 3. Define offset address map for WAVEDESC fields
    # Format string key: 'h' = short (2B), 'i' = int/long (4B), 'f' = float (4B), 'd' = double (8B)
    offset_map = {
        "comm_type":     [0x20, "h"],  # 0 = BYTE (8-bit), 1 = WORD (16-bit)
        "comm_order":    [0x22, "h"],  # 0 = LSB (Little-Endian), 1 = MSB (Big-Endian)
        "payload_bytes": [0x3C, "i"],  # Length in bytes of simple data array
        "frame_points":  [0x74, "i"],  # Number of sample points per frame
        "first_point":   [0x84, "i"],  # First point offset index
        "data_interval": [0x88, "i"],  # Point interval step
        "read_frames":   [0x90, "i"],  # Sequence frames returned in current transfer
        "sum_frames":    [0x94, "i"],  # Total sequence frames acquired
        "vdiv":          [0x9C, "f"],  # Unscaled Vertical Gain (Volts/div)
        "voffset":       [0xA0, "f"],  # Unscaled Vertical Offset (Volts)
        "code_per_div":  [0xA4, "f"],  # ADC code counts per vertical division
        "adc_bit":       [0xAC, "h"],  # Hardware ADC Resolution (8, 10, or 12 bits)
        "interval":      [0xB0, "f"],  # Sampling Interval (seconds/sample)
        "delay":         [0xB4, "d"],  # Trigger Delay / Offset (seconds)
        "tdiv_idx":      [0x144, "h"], # Timebase Enum Index
        "probe":         [0x148, "f"]  # Probe Attenuation Multiplier (e.g. 1.0, 10.0)
    }

    type_byte_len = {"h": 2, "i": 4, "f": 4, "d": 8}
    parsed_preamble = {}

    # 4. Unpack binary struct fields from designated offset addresses
    for field_name, (offset, fmt_code) in offset_map.items():
        byte_len = type_byte_len[fmt_code]
        field_bytes = descriptor[offset : offset + byte_len]
        
        # Unpack Little-Endian Little-Endian ('<') binary values
        parsed_value = struct.unpack(f"<{fmt_code}", field_bytes)[0]
        parsed_preamble[field_name] = parsed_value

    # 5. Derive real-world physical values incorporating probe multipliers
    parsed_preamble["vdiv_scaled"] = parsed_preamble["vdiv"] * parsed_preamble["probe"]
    parsed_preamble["voffset_scaled"] = parsed_preamble["voffset"] * parsed_preamble["probe"]
    
    # Resolve horizontal timebase in seconds/div from enumeration
    t_idx = parsed_preamble["tdiv_idx"]
    parsed_preamble["timebase"] = TDIV_ENUM[t_idx] if t_idx < len(TDIV_ENUM) else 1e-3

    # Retain descriptor buffer for timestamp analysis
    parsed_preamble["raw_descriptor"] = descriptor

    return parsed_preamble
```

#### Commentary:
1. **IEEE 488.2 Header Parsing (`#9000000346`)**: Binary query responses always begin with an ASCII header format `#NXXXXXXXXX`.
   * `#`: Tells the host a binary block is starting.
   * `N` (`raw_preamble_bytes[header_start + 1]`): Indicates how many ASCII digits follow to declare the payload size (typically `9`).
   * `XXXXXXXXX`: The byte count of the payload. Slicing with `data_start = header_start + 2 + digit_cnt` strips this metadata block so struct offsets map cleanly starting at index `0`.
2. **`vdiv` and `voffset` vs. `probe`**: The raw values stored at offsets `0x9C` (`vdiv`) and `0xA0` (`voffset`) represent the oscilloscope's **internal BNC input hardware state** (unscaled by the probe). To calculate true signal voltage at the probe tip, these values must be multiplied by the probe attenuation factor stored at offset `0x148` (`probe`).
3. **`adc_bit` (Offset `0xAC`)**: Reports the physical ADC hardware operating bit-depth. For 8-bit scopes (or 8-bit mode), each sample is 1 byte. For 10-bit or 12-bit HD scopes, samples are packed into 2-byte (16-bit) words.

---

### Snippet 3: Analog Channel Waveform Chunking & Signal Reconstruction

*(Ref: Manual Section "WAVeform:DATA", Doc p. 757–760 / PDF p. 758–761)*

#### Technical Context & Purpose
When reading high-memory waveforms (e.g., 10 Mpts to 100 Mpts), the oscilloscope cannot transfer the entire buffer in a single network frame due to hardware buffer caps. `:WAVeform:MAXPoint?` queries the maximum slice size allowed per read. The Python client must configure data width (`BYTE` or `WORD`), loop through memory offsets using `:WAVeform:STARt`, slice raw binary chunks, and reconstruct physical time/voltage vectors.

```python
import math
import numpy as np

def fetch_and_reconstruct_analog_channel(conn: ScopeConnection, channel: str = "C1", grid_num: int = 10):
    """
    Downloads raw binary waveform data from an analog channel (C1..C4), handles memory
    chunking automatically, and converts integer ADC codes to Volts and Time arrays.
    
    :param conn: Instantiated ScopeConnection wrapper object.
    :param channel: Target channel ('C1', 'C2', 'C3', 'C4').
    :param grid_num: Number of horizontal grid divisions (10 for SDS, 12 for SHS).
    :return: Tuple of (numpy_time_array, numpy_voltage_array, preamble_dict)
    """
    # 1. Target the requested source channel
    conn.send_cmd(f":WAVeform:SOURce {channel}")
    conn.send_cmd(":WAVeform:STARt 0")
    conn.send_cmd(":WAVeform:POINt 0")  # '0' requests max available points

    # 2. Query and parse preamble descriptor
    conn.send_cmd(":WAVeform:PREamble?")
    raw_preamble = conn.read_raw_bytes()
    preamble = parse_wavedesc_preamble(raw_preamble)

    total_points = preamble["frame_points"]
    adc_resolution = preamble["adc_bit"]
    
    # 3. Query maximum hardware chunk slice size
    max_chunk_pts = float(conn.ask_str(":WAVeform:MAXPoint?"))
    read_iterations = math.ceil(total_points / max_chunk_pts)

    # 4. Set transfer word width based on ADC resolution
    if adc_resolution > 8:
        # High-definition mode (10-bit / 12-bit / 16-bit): Transfer 2 bytes per point
        conn.send_cmd(":WAVeform:WIDTh WORD")
        conn.send_cmd(":WAVeform:BYTeorder LSB") # Little-Endian 16-bit integer
        unpack_dtype = np.int16
    else:
        # Standard mode (8-bit): Transfer 1 byte per point
        conn.send_cmd(":WAVeform:WIDTh BYTE")
        unpack_dtype = np.int8

    # 5. Execute chunked read loop
    combined_raw_payload = b''
    
    for slice_idx in range(read_iterations):
        start_point_offset = int(slice_idx * max_chunk_pts)
        
        # Tell oscilloscope where to start reading in trace memory
        conn.send_cmd(f":WAVeform:STARt {start_point_offset}")
        if total_points > max_chunk_pts:
            conn.send_cmd(f":WAVeform:POINt {int(max_chunk_pts)}")

        # Request binary data block for current slice
        conn.send_cmd(":WAVeform:DATA?")
        slice_bytes = conn.read_raw_bytes()

        # Parse IEEE 488.2 binary block header
        h_start = slice_bytes.find(b'#')
        if h_start != -1:
            digits = int(chr(slice_bytes[h_start + 1]))
            payload_start = h_start + 2 + digits
            payload_len = int(slice_bytes[h_start + 2 : payload_start])
            
            # Append valid waveform bytes (stripping trailing termination bytes)
            combined_raw_payload += slice_bytes[payload_start : payload_start + payload_len]

    # 6. Convert binary buffer directly into NumPy integer array
    raw_adc_codes = np.frombuffer(combined_raw_payload, dtype=unpack_dtype)

    # 7. Apply Mathematical Signal Reconstruction Formulas
    # Voltage Math: V = code * (vdiv_scaled / code_per_div) - voffset_scaled
    voltage_vector = (raw_adc_codes / preamble["code_per_div"] * preamble["vdiv_scaled"]) - preamble["voffset_scaled"]

    # Time Math: T = delay - (timebase * grid_num / 2) + (index * interval)
    time_screen_span = preamble["timebase"] * grid_num
    time_start = preamble["delay"] - (time_screen_span / 2.0)
    time_vector = time_start + (np.arange(len(voltage_vector)) * preamble["interval"])

    return time_vector, voltage_vector, preamble
```

#### Commentary:
1. **`unpack_dtype = np.int16` vs. `np.int8`**:
   * For 8-bit oscilloscopes, raw data values are signed 8-bit integers ranging from `-128` to `+127`.
   * For High-Definition (HD) 10-bit or 12-bit oscilloscopes, the data is left-aligned into signed 16-bit integers (`np.int16`). Using `np.frombuffer` avoids slow Python `for` loops, completing millions of byte-to-float conversions instantly in C memory.
2. **Chunk Slicing Logic (`:WAVeform:STARt`)**:
   * Supposing an acquisition contains 50 Mpts (`total_points = 50,000,000`), but `:WAVeform:MAXPoint?` returns `10,000,000`.
   * The code calculates `read_iterations = 5`.
   * Iteration 0 sets `:WAV:STAR 0`, fetching points 0 to 9,999,999.
   * Iteration 1 sets `:WAV:STAR 10000000`, fetching points 10,000,000 to 19,999,999, and so on.
3. **`code_per_div` Scaling**: In Siglent oscilloscopes, `code_per_div` defines how many raw integer counts correspond to 1 vertical grid division (typically `30` counts/div for 8-bit models or `1920` / `3840` for HD models). Dividing `raw_adc_codes` by `code_per_div` converts ADC integer units into grid division units before multiplying by Volts/division (`vdiv_scaled`).

---

### Snippet 4: Digital Channel Logic Extraction (MSO Option)

*(Ref: Manual Section "WAVeform:DATA", Doc p. 760–762 / PDF p. 761–763)*

#### Technical Context & Purpose
Digital logic channels (`D0`..`D15`) do not store floating-point voltage values. Instead, they pack 8 time-samples into a single byte payload (1 bit per sample point). This snippet demonstrates how to query a digital channel, fetch the byte array, perform bitwise extraction across all bytes, and plot clean digital logic levels (0 and 1).

```python
import numpy as np

def fetch_digital_channel(conn: ScopeConnection, digital_ch: str = "D0", grid_num: int = 10):
    """
    Downloads raw packed binary data from a digital channel (D0..D15), extracts 
    single-bit logic levels (0 or 1), and computes the time axis.
    
    :param conn: Active ScopeConnection object.
    :param digital_ch: Digital channel string ('D0'..'D15').
    :param grid_num: Horizontal grid divisions (10 for SDS, 12 for SHS).
    :return: Tuple of (time_vector, binary_logic_vector, preamble_dict)
    """
    # 1. Target the digital source channel
    conn.send_cmd(f":WAVeform:SOURce {digital_ch}")
    conn.send_cmd(":WAVeform:PREamble?")
    
    raw_preamble = conn.read_raw_bytes()
    preamble = parse_wavedesc_preamble(raw_preamble)

    # 2. Query binary waveform data block
    conn.send_cmd(":WAVeform:DATA?")
    raw_response = conn.read_raw_bytes()

    # 3. Parse IEEE binary block header
    h_idx = raw_response.find(b'#')
    digit_cnt = int(chr(raw_response[h_idx + 1]))
    data_start = h_idx + 2 + digit_cnt
    payload_length = int(raw_response[h_idx + 2 : data_start])
    
    raw_bytes = raw_response[data_start : data_start + payload_length]

    # 4. Unpack 1-bit sample points from packed byte array
    # Convert raw bytes into NumPy uint8 array
    byte_array = np.frombuffer(raw_bytes, dtype=np.uint8)
    
    # Bitwise unpack: Expand each byte into 8 individual bit samples (LSB to MSB)
    # np.unpackbits with bitorder='little' extracts bit 0, bit 1, ..., bit 7
    logic_vector = np.unpackbits(byte_array, bitorder='little')

    # Truncate any trailing padding bits to match total frame points
    total_pts = preamble["frame_points"]
    logic_vector = logic_vector[:total_pts]

    # 5. Reconstruct Time Axis
    time_screen_span = preamble["timebase"] * grid_num
    time_start = preamble["delay"] - (time_screen_span / 2.0)
    time_vector = time_start + (np.arange(len(logic_vector)) * preamble["interval"])

    return time_vector, logic_vector, preamble
```

#### Commentary:
1. **Bit Packing Density**: Standard analog reads return 1 byte per point (or 2 bytes/point in 16-bit mode). Digital channels return 1 bit per point. A 2,500 point digital acquisition requires only $\lceil 2500 / 8 \rceil = 313$ bytes of network payload.
2. **`np.unpackbits(..., bitorder='little')`**: Siglent digital channels store consecutive time points from LSB (Bit 0) to MSB (Bit 7) within each byte:
   * Bit 0 = Sample Point $T_0$
   * Bit 1 = Sample Point $T_1$
   * ...
   * Bit 7 = Sample Point $T_7$
   Using `bitorder='little'` in NumPy unpacks bit 0 first, preserving correct chronological order across the time axis vector.
3. **Padding Bits Truncation (`logic_vector[:total_pts]`)**: Because digital data is byte-aligned, if a frame contains 1,002 sample points, the scope transmits $\lceil 1002 / 8 \rceil = 126$ bytes (1,008 bits). The extra 6 trailing bits are padding and must be truncated using `[:total_pts]`.

---

### Snippet 5: Math FFT Spectrum Retrieval & Complex Float Parsing

*(Ref: Manual Section "Read Waveform Data of FFT Example", Doc p. 844–846 / PDF p. 845–847)*

#### Technical Context & Purpose
When the source is set to a Math FFT function (`FUNC1` or `F1`), calling `:WAVeform:DATA?` does not return integer ADC counts. Instead, it returns an array of **32-bit floating point complex pairs** (`Real`, `Imaginary`). 

This snippet demonstrates how to parse these complex float pairs, convert them into linear magnitude or logarithmic decibels (`dBVrms` or `dBm`), and construct the frequency axis ($0 \text{ Hz}$ to Nyquist Frequency $f_{\text{sample}} / 2$).

```python
import numpy as np

def fetch_fft_spectrum(conn: ScopeConnection, math_func: str = "FUNC1"):
    """
    Downloads complex FFT float data from a Math function, unpacks Real/Imaginary
    pairs, converts to spectral magnitude (dBVrms, dBm, or Vrms), and builds frequency axis.
    
    :param conn: Active ScopeConnection object.
    :param math_func: Math function identifier ('FUNC1'..'FUNC4' or 'F1'..'F4').
    :return: Tuple of (frequency_vector, spectral_amplitude_vector, unit_string)
    """
    # 1. Target Math FFT Function Source
    conn.send_cmd(f":WAVeform:SOURce {math_func}")
    
    # Query unit configuration (DBVrms, DBm, Vrms)
    fft_unit = conn.ask_str(f"{math_func}:FFT:UNIT?")
    fft_mode = conn.ask_str(f"{math_func}:FFT:MODE?")  # NORMal, MAXHold, or AVERage
    
    load_ohm = 50.0
    if fft_unit == "DBm":
        load_ohm = float(conn.ask_str(f"{math_func}:FFT:LOAD?"))

    # 2. Get Preamble metadata
    conn.send_cmd(":WAVeform:PREamble?")
    raw_preamble = conn.read_raw_bytes()
    params = parse_wavedesc_preamble(raw_preamble)

    # 3. Query Binary FFT Data Payload
    conn.send_cmd(":WAVeform:DATA?")
    raw_response = conn.read_raw_bytes()

    h_idx = raw_response.find(b'#')
    digit_cnt = int(chr(raw_response[h_idx + 1]))
    data_start = h_idx + 2 + digit_cnt
    payload_length = int(raw_response[h_idx + 2 : data_start])
    
    payload_bytes = raw_response[data_start : data_start + payload_length]

    # 4. Unpack 32-bit Floats (4 bytes per float, Little-Endian '<f')
    # Raw payload consists of interlaced [Real_0, Imag_0, Real_1, Imag_1, ...]
    float_array = np.frombuffer(payload_bytes, dtype='<f4')

    # De-interleave Real and Imaginary components
    real_parts = float_array[0::2]
    imag_parts = float_array[1::2]

    # 5. Compute Magnitude Spectrum
    if "NORM" in fft_mode.upper():
        # Normal Mode: Compute magnitude from complex pairs sqrt(Real^2 + Imag^2)
        linear_magnitude = np.sqrt(np.square(real_parts) + np.square(imag_parts))
    else:
        # MaxHold or Average Mode: Real component already holds scalar magnitude
        linear_magnitude = real_parts

    # 6. Apply Unit Conversions (dBVrms / dBm)
    if fft_unit == "DBVrms":
        # dBVrms = 20 * log10(Linear_Vrms)
        amplitude_vector = 20.0 * np.log10(np.maximum(linear_magnitude, 1e-12))
    elif fft_unit == "DBm":
        # dBm = 10 * log10( (Vrms^2 / Load) / 1mW )
        power_watts = np.square(linear_magnitude) / load_ohm
        amplitude_vector = 10.0 * np.log10(np.maximum(power_watts / 1e-3, 1e-12))
    else:
        # Vrms
        amplitude_vector = linear_magnitude

    # 7. Construct Frequency Axis (Hz)
    # Frequency step delta = interval parameter from preamble
    num_bins = len(amplitude_vector)
    freq_step = params["interval"]
    frequency_vector = np.arange(num_bins) * freq_step

    return frequency_vector, amplitude_vector, fft_unit
```

#### Commentary:
1. **Complex Float Pair Interleaving (`float_array[0::2]` vs `float_array[1::2]`)**:
   FFT buffer outputs 8 bytes per frequency bin. Slicing with `[0::2]` extracts every even-indexed float (Real component $R_k$), while `[1::2]` extracts every odd-indexed float (Imaginary component $I_k$).
2. **Spectral Units Math**:
   * **`DBVrms`**: Computes standard voltage decibels relative to $1\text{ V}_{\text{rms}}$. The `np.maximum(..., 1e-12)` clamp prevents `log10(0)` divide-by-zero runtime exceptions on zero-amplitude bins.
   * **`DBm`**: Computes RF power decibels relative to $1\text{ mW}$ ($10^{-3}\text{ W}$). Incorporates the configurable load impedance (`load_ohm`, defaulting to $50\ \Omega$).
3. **Nyquist Frequency Limit**: The frequency step between bins equals the `interval` field from the preamble. The maximum frequency bin returned corresponds to the Nyquist boundary ($f_{\text{sample}} / 2$).

---

### Snippet 6: Sequence Mode (Segmented Memory) & 16-Byte Timestamp Parsing

*(Ref: Manual Section "WAVeform:SEQuence", Doc p. 763–764 / PDF p. 764–765 & "Read Sequence Waveform Data Example", Doc p. 847–853 / PDF p. 848–853)*

#### Technical Context & Purpose
In Sequence mode, the oscilloscope captures multiple rapid trigger events into segmented memory. To read back these segments, the host uses `:WAVeform:SEQuence <frame>,<start>`.
* `:WAVeform:SEQuence 0,0`: Requests **all** captured sequence frames at once.
* `:WAVeform:SEQuence 1,5`: Requests a single frame (Frame 5).

Additionally, Siglent scopes record an absolute timestamp for **every** segment frame inside the preamble descriptor at offset address `346` (`0x15A`).

```python
import struct
import numpy as np

def parse_sequence_timestamps(raw_descriptor_block: bytes, total_frames: int) -> list:
    """
    Parses the 16-byte binary timestamp structure for every sequence segment frame.
    
    :param raw_descriptor_block: Raw WAVEDESC descriptor block bytes starting after header.
    :param total_frames: Number of acquired sequence frames.
    :return: List of formatted timestamp strings [YYYY-MM-DD HH:MM:SS.uuuuuu]
    """
    timestamp_start_offset = 346  # Offset 0x15A in WAVEDESC
    timestamps = []

    for frame_idx in range(total_frames):
        offset = timestamp_start_offset + (frame_idx * 16)
        frame_time_bytes = raw_descriptor_block[offset : offset + 16]

        if len(frame_time_bytes) < 16:
            break

        # Unpack 16-byte time structure:
        # 0x00..0x07 (8B double): Seconds + fractional microseconds
        # 0x08 (1B char):         Minutes
        # 0x09 (1B char):         Hours
        # 0x0A (1B char):         Days
        # 0x0B (1B char):         Months
        # 0x0C..0x0D (2B short):  Year
        seconds = struct.unpack('<d', frame_time_bytes[0x00:0x08])[0]
        minutes = int.from_bytes(frame_time_bytes[0x08:0x09], byteorder='big')
        hours   = int.from_bytes(frame_time_bytes[0x09:0x0A], byteorder='big')
        days    = int.from_bytes(frame_time_bytes[0x0A:0x0B], byteorder='big')
        months  = int.from_bytes(frame_time_bytes[0x0B:0x0C], byteorder='big')
        year    = struct.unpack('<h', frame_time_bytes[0x0C:0x0E])[0]

        formatted_ts = f"{year:04d}-{months:02d}-{days:02d} {hours:02d}:{minutes:02d}:{seconds:09.6f}"
        timestamps.append(formatted_ts)

    return timestamps


def fetch_all_sequence_frames(conn: ScopeConnection, channel: str = "C1"):
    """
    Retrieves all sequence frames and their individual trigger timestamps.
    """
    conn.send_cmd(f":WAVeform:SOURce {channel}")
    conn.send_cmd(":WAVeform:STARt 0")
    conn.send_cmd(":WAVeform:POINt 0")
    
    # Enable transfer for ALL sequence frames starting at frame 1
    # Command syntax: :WAVeform:SEQuence <frame_count>,<start_frame>
    # Setting <frame_count> to 0 requests all available frames.
    conn.send_cmd(":WAVeform:SEQuence 0,0")

    # Fetch preamble to extract total frame counts and descriptor block
    conn.send_cmd(":WAVeform:PREamble?")
    preamble_raw = conn.read_raw_bytes()
    params = parse_wavedesc_preamble(preamble_raw)

    sum_frames = params["sum_frames"]
    read_frames = params["read_frames"]
    pts_per_frame = params["one_frame_pts"]

    print(f"Total Acquired Sequence Frames: {sum_frames}")
    
    # Parse individual frame timestamps from preamble memory offset 346
    frame_timestamps = parse_sequence_timestamps(params["raw_descriptor"], sum_frames)

    # Fetch raw sequence binary data payload
    if params["adc_bit"] > 8:
        conn.send_cmd(":WAVeform:WIDTh WORD")
        conn.send_cmd(":WAVeform:BYTeorder LSB")
        unpack_dtype = np.int16
        bytes_per_sample = 2
    else:
        conn.send_cmd(":WAVeform:WIDTh BYTE")
        unpack_dtype = np.int8
        bytes_per_sample = 1

    conn.send_cmd(":WAVeform:DATA?")
    raw_payload = conn.read_raw_bytes()

    h_start = raw_payload.find(b'#')
    digits = int(chr(raw_payload[h_start + 1]))
    data_offset = h_start + 2 + digits
    payload_len = int(raw_payload[h_start + 2 : data_offset])

    combined_bytes = raw_payload[data_offset : data_offset + payload_len]
    all_codes = np.frombuffer(combined_bytes, dtype=unpack_dtype)

    # Reshape 1D array into 2D matrix: [frame_index, sample_index]
    frames_matrix = all_codes.reshape((read_frames, pts_per_frame))

    # Convert 2D ADC codes matrix into 2D Voltage Matrix
    voltage_matrix = (frames_matrix / params["code_per_div"] * params["vdiv_scaled"]) - params["voffset_scaled"]

    return frame_timestamps, voltage_matrix, params
```

#### Commentary:
1. **`:WAVeform:SEQuence 0,0`**:
   * Parameter 1 (`0`): Specifies how many sequence frames to transfer at once (`0` = transfer as many as fit in memory).
   * Parameter 2 (`0` or `1`): Specifies the starting frame index.
2. **16-Byte Timestamp Offset (`346`)**: Every segment frame in sequence mode records a hardware timestamp. The preamble contains a contiguous array of these 16-byte structs starting at offset `346` (`0x15A`).
3. **2D NumPy Array Reshaping (`reshape((read_frames, pts_per_frame))`)**:
   When reading all frames simultaneously, the scope returns a continuous 1D binary buffer containing $N$ frames concatenated end-to-end. Reshaping the 1D NumPy array into a 2D matrix allows instantly accessing any frame's voltage array via `voltage_matrix[frame_index]`.

---

### Snippet 7: Display Screen Capture & Image File Generation

*(Ref: Manual Section ":SAVE:IMAGe", Doc p. 410 / PDF p. 411 & "Screen Dump Example", Doc p. 853 / PDF p. 854)*

#### Technical Context & Purpose
Captures the exact display state of the oscilloscope screen over network/USB and saves it directly to a local disk file (`.png` or `.bmp`). Supports optional color inversion (`ON` = white background to save printer ink, `OFF` = normal dark theme) and menu hiding.

```python
def capture_and_save_screenshot(conn: ScopeConnection, filepath: str = "scope_screen.png", 
                                image_format: str = "PNG", invert_colors: bool = False, 
                                hide_menus: bool = False):
    """
    Captures screen image from the oscilloscope display and writes it to a file.
    
    :param conn: Active ScopeConnection object.
    :param filepath: Local file save path (e.g. 'capture.png' or 'C:/images/test.bmp').
    :param image_format: 'PNG' or 'BMP'.
    :param invert_colors: True = White background (ink-saver), False = Dark screen background.
    :param hide_menus: True = Excludes side menus and timebar, False = Includes all UI menus.
    """
    # 1. Optionally hide side menu before capturing
    if hide_menus:
        conn.send_cmd(":DISPlay:HIDemenu")
        time.sleep(0.2) # Allow UI redraw

    # 2. Build SCPI Screen Query
    # Format: :PRINt? <type>[,<format>]
    # Or:     :SAVE:IMAGe <path>,<type>,<invert>[,<menu>]
    color_mode = "INVerted" if invert_colors else "NORMal"
    scpi_cmd = f"PRIN? {image_format.upper()},{color_mode}"

    # 3. Send Query and Read Binary Stream
    conn.send_cmd(scpi_cmd)
    raw_response = conn.read_raw_bytes()

    # 4. Strip IEEE 488.2 Binary Header block (#9XXXXXXXXX)
    h_start = raw_response.find(b'#')
    if h_start != -1:
        digit_count = int(chr(raw_response[h_start + 1]))
        payload_offset = h_start + 2 + digit_count
        image_bytes = raw_response[payload_offset:]
    else:
        image_bytes = raw_response

    # 5. Write Raw Image Bytes to File on Local Disk
    with open(filepath, "wb") as img_file:
        img_file.write(image_bytes)
        img_file.flush()

    print(f"Successfully saved {image_format} screenshot to: {filepath}")
```

#### Commentary:
1. **`open(filepath, "wb")`**: Image files are raw binary streams. Opening the file in binary write mode (`"wb"`) prevents Python from interpreting or translating newline bytes (`\r\n`), which would otherwise corrupt PNG/BMP image headers.
2. **`PRIN? PNG,INVerted`**: Passing `INVerted` automatically flips the dark scope display to a clean white background. This is useful for automated test report generation and printing.
3. **IEEE 488.2 Header Removal**: Like binary waveform blocks, image responses are prepended with an ASCII header declaring byte length. Slicing with `raw_response[payload_offset:]` strips the SCPI header so the file begins directly with standard PNG magic bytes (`0x89 50 4E 47`) or BMP magic bytes (`0x42 4D`).
