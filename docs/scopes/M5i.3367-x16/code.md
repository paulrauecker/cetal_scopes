# Native API Examples

[Source Manual](https://spectrum-instrumentation.com/dl/m5i_33xx_manual_english.pdf) and [Examples](https://spectrum-instrumentation.com/dl/spcm_examples.tgz)

!!! warning
    AI generated summary, be sure to double check the reference manual for critical applications.


An introduction and working reference for the M5i.3367-x16 digitizer card,
condensed from the official *M5i.33xx-x16 Hardware Manual / Software Driver
Manual* (Spectrum Instrumentation GmbH, printed 23 April 2026) and from the
Python example set shipped with the driver.

The manual documents the whole M5i.33xx family; everything below has been
narrowed to the **M5i.3367-x16** (2 channels, 12 bit, 10 GS/s, 4.7 GHz) and
to the parts of the API relevant when driving the card from Python.

Page numbers given as "manual p. NN" refer to the printed page numbers of
that PDF (the numbers shown in the footer of each page, which match the
manual's own table of contents), so you can jump straight to the full
register tables, timing diagrams, and figures that are only summarized here.

**Contents**

| § | Topic | Manual pages |
|---|---|---|
| 1 | What the card is | 13–26 |
| 2 | Software stack and Python APIs | 39, 62–63 (install 32–37) |
| 3 | The driver function set | 49–56 |
| 4 | Opening the card, error handling, card information | 68–76 |
| 5 | Analog input configuration | 77–81 |
| 6 | Clock generation | 96–100 |
| 7 | Acquisition modes | 82–83, 88–91, 126–128 |
| 8 | Card commands, status, and the state machine | 83–85 |
| 9 | Data organization, sample formats, voltage conversion | 94–95, 129–134 |
| 10 | DMA buffer handling | 85–93 |
| 11 | Trigger system (complete) | 101–121 |
| 12 | Multi-purpose I/O lines X0–X3 | 122–125, 170–171 |
| 13 | Timestamps | 135–143 |
| 14 | Block Average (firmware option) | 160–165 |
| 15 | Star-Hub synchronization | 15, 30, 152–156 |
| 16 | Putting it together: a complete acquisition | 68–95 |
| 17 | Practical notes and common pitfalls | — |
| 18 | Guide to the bundled Python examples | 62–63 |
| 19 | Where to look in the manual for what isn't here | 144–176 |
| — | Appendix pointers (error codes, sensors, LEDs, continuous memory) | 166–176 |

---

## 1. What the card is

> *manual pp. 13–26*

The M5i.3367-x16 is a two-channel, 12-bit PCI Express x16 Gen3 digitizer
from Spectrum's M5i series — the company's fastest streaming platform. It is
designed for applications needing very high sample rates together with high
input bandwidth: fast transient capture, time-of-flight measurements,
ultrasonics, radar/sonar, and any single-shot or repetitive fast pulse
diagnostics.

### Analog inputs

> *manual p. 18*

| Property | Value |
|---|---|
| Channels | 2 (Ch0, Ch1), each with its own amplifier and converter |
| Resolution | 12 bit (optionally reduced to 8 bit in hardware) |
| Input ranges (software selectable, per channel) | ±200 mV, ±500 mV, ±1 V, ±2.5 V |
| Input type | Single-ended, fixed |
| Input impedance | 50 Ω, fixed |
| Coupling | DC, fixed |
| Input offset | Software programmable, ±100 % of the selected range, 1 % steps |
| −3 dB bandwidth (typ) | 4.7 GHz |
| Flatness within ±0.5 dB | 2.0 GHz |
| Anti-aliasing filter | Fixed at the specified bandwidth |
| Over-voltage protection | ±200 mV range: 1.4 Vrms (16 dBm), max ±2.0 V peak. ≥ ±500 mV ranges: 5 Vrms (27 dBm), max ±7.5 V peak |
| DNL / INL (ADC only, nom) | ±0.3 LSB / ±2.5 LSB |
| Offset / gain error (spec, after warm-up + calibration) | < 0.5 % of range / < 0.5 % of reading |
| Crosstalk (meas, 50 Ω) | < −110 dB at 10 MHz, < −103 dB at 100 MHz |

**Voltage per LSB and typical zero-noise (1-channel operation at 10 GS/s):**

| Range | 1 LSB | RMS noise (typ) |
|---|---|---|
| ±200 mV | 97 µV | 3.9 LSB ≈ 381 µV |
| ±500 mV | 244 µV | 3.8 LSB ≈ 928 µV |
| ±1 V | 488 µV | 4.3 LSB ≈ 2.1 mV |
| ±2.5 V | 1.22 mV | 4.3 LSB ≈ 5.3 mV |

In 2-channel operation at 5 GS/s the noise is comparable (3.4–4.1 LSB
depending on range). Dynamic performance at 10 GS/s, ±1 V, is typically
SNR ≈ 51 dB, THD ≈ −70 dB at 10 MHz degrading to ≈ −57 dB at 1.2 GHz,
ENOB ≈ 8.2 bits. Switching to the 8-bit storage mode gives SNR ≈ 47 dB and
ENOB ≈ 7.5 — note that because the ENOB is below 12 bit anyway, the 8-bit
mode costs less real resolution than the bit count suggests.

### Sample rates

> *manual pp. 19, 97*

Maximum sampling rate depends on which channels are active:

| Active channels | Max sample rate |
|---|---|
| Ch0 only | 10.0 GS/s |
| Ch1 only | 5.0 GS/s recommended (can run 10 GS/s, but with reduced signal quality) |
| Ch0 + Ch1 | 5.0 GS/s |

Ch0 is the channel optimized for full-speed acquisition; when you only need
one channel at top speed, use Ch0.

Available clock base frequencies are 10.0, 8.0, and 6.4 GS/s, each divisible
by any power of two up to 2 097 152, giving the sequence 10.0, 8.0, 6.4,
5.0, 4.0, 3.2, 2.5, 2.0, 1.6, 1.25, 1.0 GS/s … down to 5 kS/s. The driver
accepts an arbitrary frequency in Hz and rounds to the nearest achievable
divided clock — always read the register back to find out what you actually
got.

### Memory, transfer, and physical

> *manual pp. 20, 90*

| Property | Value |
|---|---|
| On-board memory | 2 GiSample (4 GiByte) standard; 8 GiSample (16 GiByte) with option `M5i.xxxx-MEM8GS` |
| Bus | PCIe x16 Gen3 (works electrically in x1…x16 slots, Gen1–Gen5) |
| Sustained streaming card→PC (meas) | > 13.9 GB/s with a 512-byte-TLP chipset; > 11.2 GB/s with 256-byte TLP |
| Power | 0.3 A @ 3.3 V from the slot plus 3.2 A @ 12 V from the auxiliary connector, ≈ 39 W total |
| Connectors | SMA female for both analog inputs, trigger in, clock in, clock out, and X0–X3 |
| Dimensions | 241 × 107 × 40 mm (double-slot; three slots with Star-Hub) |
| Operating temperature | 0 °C to 50 °C, humidity 10–90 % |
| Warm-up time | 30 minutes running acquisition at full speed |
| Connector lifetime | SMA 500 cycles, PCIe 50 cycles, PCIe power 30 cycles |

> **A separate PCIe 6-pin power connection to the card is mandatory.**
> The card cannot be powered from the slot alone.

Specifications are valid after 30 minutes of warm-up and after running an
on-board self-calibration. A yearly external calibration is recommended.

### Options that may be installed

> *manual pp. 15, 26, 73*

- **Star-Hub** — synchronizes up to 8 M5i cards with minimal clock skew.
- **Pulse Generator** — four internal pulse generators driving X0–X3.
- **Block Average** — hardware accumulation/averaging of repetitive segments.
- **Memory upgrade** to 8 GiSample.
- **SCAPP** — CUDA RDMA transfers straight into GPU memory (Linux only).
- **Remote Server** — access the card over Ethernet.

Whether a given option is present is readable at runtime — see §4.

---

## 2. Software stack and Python APIs

> *manual pp. 39, 62–63; driver installation pp. 32–37*

The card is programmed entirely through software registers. There is no
SCPI-style command language: every setting is a numeric register written or
read through a handful of driver functions.

Three layers exist:

1. **Kernel driver + library** (`libspcm_linux.so` on Linux,
   `spcm_win64.dll` on Windows). Installed from the vendor driver package.
   Local cards appear as `/dev/spcm0`, `/dev/spcm1`, … on Linux.
2. **`pyspcm` — the low-level Python interface.** A thin `ctypes` wrapper
   that mirrors the C API one-to-one. Consists of `pyspcm.py` (function
   bindings), `py_header/regs.py` (every register and constant, with the
   same names used throughout the manual), and `py_header/spcerr.py` (error
   codes). Optimized for speed and for people who are following the manual
   literally.
3. **`spcm` — the high-level object-oriented package.** Built on top of the
   low-level API, installed with `pip install spcm`, Python ≥ 3.9, MIT
   licensed. It handles opening and closing of cards (including
   Star-Hub-synchronized sets), raises exceptions instead of returning error
   codes, allocates DMA memory for you, gives a direct numpy interface, and
   supports physical units via `pint`.

Useful links:

- Package: https://github.com/SpectrumInstrumentation/spcm
- Examples: https://github.com/SpectrumInstrumentation/spcm/tree/master/src/examples
- PyPI: https://pypi.org/project/spcm/
- API reference: https://spectruminstrumentation.github.io/spcm/spcm.html

**Which to use.** For new lab code, `spcm` is the better default: far less
boilerplate, no manual buffer alignment, numpy arrays out of the box. Use
`pyspcm` when you want the code to map line-by-line onto the manual's
register tables, when you are porting existing C code, or when you need
control over exactly how DMA buffers are allocated. Both talk to the same
driver, and the register names are identical, so knowledge transfers.

The examples shipped with the driver (and discussed in §17) are all written
against the low-level `pyspcm` interface, so the rest of this document uses
that notation. The register names apply unchanged in either API.

Note that `pyspcm.py` raises a clear exception if the driver is missing, and
another if the installed driver is older than **V7.0** (it detects this by a
missing symbol). Driver and firmware updates are free for the lifetime of
the card.

---

## 3. The driver function set

> *manual pp. 49–56*

```python
from pyspcm import *
from spcm_tools import *
```

| Function | Purpose |
|---|---|
| `spcm_hOpen(name)` | Open a card, return a handle (falsy on failure) |
| `spcm_vClose(handle)` | Close a card |
| `spcm_dwSetParam_i32/_i64/_d64(h, reg, value)` | Write a register |
| `spcm_dwGetParam_i32/_i64/_d64(h, reg, byref(var))` | Read a register |
| `spcm_dwSetParam_ptr(h, reg, buf, len)` | Write a buffer-valued register |
| `spcm_dwGetParam_ptr(h, reg, buf, len)` | Read a buffer-valued register (e.g. the card name string) |
| `spcm_dwGetErrorInfo_i32(h, ®, &val, textbuf)` | Read and clear the last error |
| `spcm_dwDefTransfer_i64(h, buftype, dir, notifysize, buf, offset, len)` | Define a DMA transfer buffer |
| `spcm_dwInvalidateBuf(h, buftype)` | Invalidate a previously defined buffer |
| `spcm_dwGetContBuf_i64(h, buftype, &buf, &len)` | Request the driver's continuous (kernel-allocated) buffer |
| `spcm_dwDiscovery(...)` / `spcm_dwSendIDNRequest(...)` | LXI discovery of remote/NETBOX devices |

All `spcm_dw*` functions return a 32-bit error code; `ERR_OK` is 0.

---

## 4. Opening the card, error handling, card information

> *manual pp. 68–76*

### Opening

> *manual p. 68*

```python
hCard = spcm_hOpen(create_string_buffer(b'/dev/spcm0'))
if not hCard:
    sys.stdout.write("no card found...\n")
    exit(1)
...
spcm_vClose(hCard)
```

A device can only be opened once at a time; a second open returns an error.
Remote cards (Remote Server or a digitizerNETBOX) are opened with a VISA
string instead:

```python
hCard = spcm_hOpen(create_string_buffer(b'TCPIP::192.168.1.10::inst0::INSTR'))
# further cards on the same device: ::inst1::INSTR, ::inst2::INSTR, ...
```

Everything after the open is identical for local and remote cards.

### Error handling model

> *manual p. 69; full error list p. 166*

The driver **locks on the first error**. Once a register write or read fails,
every subsequent call returns `ERR_LASTERR` until the error is read out with
`spcm_dwGetErrorInfo_i32`. This is deliberate: it prevents a bad setup value
from silently propagating into an acquisition. Practically, it means you do
not need to test every single call — one check after a block of setup calls
is enough, and it will report the register and value that actually failed.

```python
szErrorTextBuffer = create_string_buffer(ERRORTEXTLEN)
...
if spcm_dwGetErrorInfo_i32(hCard, None, None, szErrorTextBuffer) != ERR_OK:
    sys.stdout.write("{0}\n".format(szErrorTextBuffer.value))
    spcm_vClose(hCard)
    exit(1)
```

Typical output: `Error occurred at register SPC_MEMSIZE with value -345: value not allowed`.

Error codes that report a *condition* rather than a programming mistake do
**not** lock the driver — most importantly `ERR_TIMEOUT`, which is a normal
outcome of a wait command and should be handled inline. Codes worth
recognizing (full list in `py_header/spcerr.py`):

| Code | Meaning |
|---|---|
| `ERR_OK` | Success |
| `ERR_TIMEOUT` | A wait command hit `SPC_TIMEOUT`. Not an error condition per se |
| `ERR_ABORT` | Wait aborted because another thread stopped/reset the card |
| `ERR_LASTERR` | An earlier error is pending and must be read out |
| `ERR_SEQUENCE` | Command not allowed in the current card state |
| `ERR_VALUE` / `ERR_REG` | Illegal value / unknown register |
| `ERR_CLOCKNOTLOCKED` | PLL could not lock to the external reference clock |
| `ERR_PRETRIGGERLEN` | Requested pretrigger exceeds the limit for the active setup |
| `ERR_NOTIFYSIZE` | Illegal notify size for this card series |
| `ERR_FIFOHWOVERRUN` / `ERR_FIFOBUFOVERRUN` | On-board FIFO or PC buffer overrun during streaming |
| `ERR_GOLDENIMAGE` | Card booted its recovery firmware; `M2CMD_CARD_START` refused |
| `ERR_TEMPERATURE` / `ERR_FAN` / `ERR_POWERSUPPLY` | Hardware condition |

### Writing to the driver debug log

> *manual p. 69*

Useful when correlating your own application steps with a driver log during
support cases or debugging (requires driver ≥ V7.00):

```python
szText = b"My custom log file entry"
spcm_dwSetParam_ptr(None, SPC_WRITE_TO_LOG, szText, len(szText))
```

### Reading card information

> *manual pp. 70–75*

Everything on the type plate is also readable by software. Read these at
startup and log them alongside your measurement data — they are the
provenance record for a capture.

| Register | Meaning |
|---|---|
| `SPC_PCITYP` | Card type. Numeric via `_i32` (`TYP_M5I3367_X16` = 0xA3367 = 668519), or the human-readable name via `spcm_dwGetParam_ptr` |
| `SPC_FNCTYPE` | Function type; for this card `SPCM_TYPE_AI` (analog input) |
| `SPC_PCISERIALNO` | Unique serial number — always quote it in support requests |
| `SPC_PCIMEMSIZE` | Installed memory in bytes (read with `_i64`; `_i32` fails above 1 GiByte with `ERR_EXCEEDINT32`) |
| `SPC_PCISAMPLERATE` | Maximum sampling rate in Hz (64-bit), ignoring channel-count restrictions |
| `SPC_MIINST_MODULES` / `SPC_MIINST_CHPERMODULE` | Front-end module count and channels per module; multiply for total channels |
| `SPC_MIINST_BYTESPERSAMPLE` | Bytes per sample in memory (2 for this card in native mode) |
| `SPC_MIINST_BITSPERSAMPLE` | ADC resolution in bits |
| `SPC_MIINST_MAXADCVALUE` | Decimal code of ADC full scale — **required** for voltage conversion |
| `SPC_MIINST_MINEXTREFCLOCK` / `_MAXEXTREFCLOCK` | Allowed external reference clock range |
| `SPC_MIINST_ISDEMOCARD` | Non-zero if this is a demo (software-simulated) card |
| `SPC_PCIFEATURES` | Bitmask of installed features/options |
| `SPC_PCIEXTFEATURES` | Bitmask of extended (firmware) options |
| `SPC_PCIDATE` | Production date: week in bits 31–16, year in bits 15–0 |
| `SPC_CALIBDATE` | Last factory calibration, same encoding |
| `SPC_CALIBDATEONBOARD` | UTC time of the last on-board self-calibration |
| `SPC_PCIVERSION`, `SPC_BASEPCBVERSION`, `SPC_PCIMODULEVERSION`, `SPC_MODULEPCBVERSION`, `SPC_PCIEXTVERSION` | Hardware/PCB/firmware revisions of base card, front-end module, and extension module |
| `SPCM_FW_CTRL`, `SPCM_FW_MODULEA`, `SPCM_FW_POWER`, `SPCM_FW_MODEXTRA` | Firmware versions of the individual programmable devices |
| `SPCM_FW_CTRL_ACTIVE` | Which firmware image is booted, encoded `TVVVCCUU` (T: 1 = standard, 2 = golden/recovery) |
| `SPC_GETDRVVERSION` / `SPC_GETKERNELVERSION` | Library and kernel driver version: major in bits 31–24, minor in 23–16, build in 15–0 |
| `SPC_GETDRVTYPE` | e.g. `DRVTYP_LINUX64` |

Feature flags worth testing before using an optional mode:

| Flag | Meaning |
|---|---|
| `SPCM_FEAT_MULTI` | Multiple Recording available |
| `SPCM_FEAT_TIMESTAMP` | Timestamp option available |
| `SPCM_FEAT_STARHUB8` (0x40) | This card carries the 8-card Star-Hub module |
| `SPCM_FEAT_SCAPP` | CUDA RDMA (GPU) transfers available |
| `SPCM_FEAT_NETBOX` | Card lives inside a NETBOX chassis |
| `SPCM_FEAT_REMOTESERVER` | Remote Server option installed |
| `SPCM_FEAT_EXTFW_SEGAVERAGE` (in `SPC_PCIEXTFEATURES`) | Block Average firmware installed |
| `SPCM_FEAT_EXTFW_PULSEGEN` | Pulse Generator firmware installed |

```python
lFeatures = int32(0)
spcm_dwGetParam_i32(hCard, SPC_PCIFEATURES, byref(lFeatures))
if lFeatures.value & SPCM_FEAT_TIMESTAMP:
    ...
```

### Reset

> *manual pp. 75–76*

```python
spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_CARD_RESET)
```

Equivalent to a power-on reset plus a reset of all internal driver settings
to defaults. On-board memory contents become invalid and all output signals
(trigger out, clock out) are disabled. A reset happens automatically when the
driver is first loaded. Starting every measurement script with an explicit
reset is good practice — it guarantees you are not inheriting settings from
whoever used the card last.

---

## 5. Analog input configuration

> *manual pp. 77–81*

### Channel selection

> *manual p. 77*

`SPC_CHENABLE` is a bitmask. Valid values on this card are `CHANNEL0` (1),
`CHANNEL1` (2), or both ORed (3). Any other mask is rejected with an error.

```python
spcm_dwSetParam_i32(hCard, SPC_CHENABLE, CHANNEL0 | CHANNEL1)

lSetChannels = int32(0)
spcm_dwGetParam_i32(hCard, SPC_CHCOUNT, byref(lSetChannels))   # -> 2
```

The channel selection is the setting that constrains everything else: it
determines the maximum sample rate (§1), the maximum memory per channel, and
the maximum pretrigger. Set it first.

### Input range and offset

> *manual pp. 78–79*

```python
spcm_dwSetParam_i32(hCard, SPC_AMP0, 1000)   # Ch0: ±1 V
spcm_dwSetParam_i32(hCard, SPC_AMP1,  200)   # Ch1: ±200 mV
spcm_dwSetParam_i32(hCard, SPC_OFFS0,   0)   # Ch0 offset, ±100 % in 1 % steps
```

Valid values for `SPC_AMPx` are `200`, `500`, `1000`, `2500` (the range in mV).
Rather than hard-coding them, you can enumerate what the card actually
supports:

```python
lNumRanges = int32(0)
spcm_dwGetParam_i32(hCard, SPC_READIRCOUNT, byref(lNumRanges))
for i in range(lNumRanges.value):
    lMin, lMax = int32(0), int32(0)
    spcm_dwGetParam_i32(hCard, SPC_READRANGEMIN0 + i, byref(lMin))
    spcm_dwGetParam_i32(hCard, SPC_READRANGEMAX0 + i, byref(lMax))
    print(f"Range {i}: {lMin.value} mV to {lMax.value} mV")
```

The offset shifts the input so an asymmetric signal can use the full
converter range. Because the offset is expressed as a *percentage of the
selected range*, it must be recomputed whenever you change the range.

`SPC_READAIFEATURES` returns a bitmask of what the input path supports —
e.g. `SPCM_AI_SE`, `SPCM_AI_DCCOUPLING`, `SPCM_AI_LOWIMP`,
`SPCM_AI_OFFSPERCENT`, `SPCM_AI_AUTOCALOFFS`, `SPCM_AI_AUTOCALGAIN`. Useful
if you want the same code to drive several different Spectrum cards.

### On-board calibration

> *manual pp. 80–81*

```python
spcm_dwSetParam_i32(hCard, SPC_ADJ_AUTOADJ, ADJ_ALL)      # calibrate all channels/ranges
spcm_dwSetParam_i32(hCard, SPC_ADJ_SAVE,    ADJ_DEFAULT)  # store to on-board EEPROM
```

> Disconnect all signals from the analog inputs before starting a
> self-calibration — leave the SMA connectors open. Self-calibration changes
> internal driver settings, so the acquisition setup must be (re)applied
> afterwards.

Calibration results are held in the driver and are lost when the program
exits unless explicitly saved with `SPC_ADJ_SAVE`. `SPC_ADJ_LOAD` restores a
stored set; the default set is loaded automatically at driver start.
Self-calibration corrects against the on-board references; an external
calibration (factory) is what calibrates those references, and is
recommended yearly.

---

## 6. Clock generation

> *manual pp. 96–100*

### Clock modes

> *manual p. 96*

`SPC_CLOCKMODE` selects the source; `SPC_AVAILCLOCKMODES` reports which
modes exist.

| Mode | Value | Description |
|---|---|---|
| `SPC_CM_INTPLL` | 1 | Internal programmable high-precision quartz + PLL. Default and most common. Accuracy ±1 ppm |
| `SPC_CM_EXTREFCLOCK` | 32 | Internal PLL locked to an external reference clock |
| (Star-Hub) | — | Clock distributed from the Star-Hub carrier card |

All three modes require the sample rate to be programmed explicitly.

### Setting the sample rate

> *manual p. 97*

```python
spcm_dwSetParam_i32(hCard, SPC_CLOCKMODE,  SPC_CM_INTPLL)
spcm_dwSetParam_i64(hCard, SPC_SAMPLERATE, int64(5000000000))   # ask for 5 GS/s

llRate = int64(0)
spcm_dwGetParam_i64(hCard, SPC_SAMPLERATE, byref(llRate))       # what you actually got
```

You write the desired rate in Hz and the driver works out the base frequency
and divider. Because only `[base]/2ⁿ` rates exist, anything in between is
rounded to the nearest valid divided clock — **always read the register back**
and store the returned value with your data, since it is what your time axis
depends on.

### Oversampling

> *manual p. 98*

Below the ADC's physical minimum clock, the driver runs the converter and
clock section within specification and clocks only the digital section
slower. This is transparent, but the factor is readable:

```python
lOS = int32(0)
spcm_dwGetParam_i32(hCard, SPC_OVERSAMPLINGFACTOR, byref(lOS))   # 1 if inactive
```

### Clock output

> *manual pp. 97, 99*

```python
spcm_dwSetParam_i32(hCard, SPC_CLOCKOUT, 1)   # 1 = enable, 0 = tristate
lFreq = int32(0)
spcm_dwGetParam_i32(hCard, SPC_CLOCKOUTFREQUENCY, byref(lFreq))
```

Works with any clock source. The internal reference clock output is
single-ended, AC-coupled LVPECL, ≈720 mVpp, at the clock setup base
frequency divided by 128 (e.g. base 10 GS/s → 78.125 MHz). Note that the
output frequency does **not** reflect the oversampling factor — read
`SPC_CLOCKOUTFREQUENCY` if you need the actual value.

### External reference clock

> *manual pp. 98–99; specification p. 20*

```python
spcm_dwSetParam_i32(hCard, SPC_CLOCKMODE,       SPC_CM_EXTREFCLOCK)
spcm_dwSetParam_i32(hCard, SPC_REFERENCECLOCK,  10000000)   # tell the driver: 10 MHz fed in
spcm_dwSetParam_i64(hCard, SPC_SAMPLERATE,      int64(5000000000))
```

Specification of the reference input: 2 MHz to 750 MHz in 2 MHz steps,
50 Ω fixed, AC-coupled, single-ended sine or square, rising edge, 200 mVpp
to 3 Vpp swing, 45–55 % duty cycle, max ±10 V DC.

You must set `SPC_REFERENCECLOCK` to the true fed-in frequency — the driver
uses it to compute the PLL settings. Note also:

- The external clock must be **present and stable when the card is started**.
  You cannot start with external clock selected and no clock connected.
- PLL locking normally takes 10–20 ms, occasionally up to 200 ms; the driver
  inserts this wait automatically at card start.
- If the PLL cannot lock, `M2CMD_CARD_START` returns `ERR_CLOCKNOTLOCKED`.
- Prefer a sampling clock that is a **multiple** of the reference. If the
  sampling clock is a division of the reference, the starting phase is
  undetermined and can change between resets.
- With Star-Hub, the reference goes to the clock input on the Star-Hub
  bracket, not to the individual card.

### Clock and channel delay (skew)

> *manual p. 100*

Channel-to-channel skew on a single card is < 12 ps (measured). For fine
adjustment:

| Register | Purpose |
|---|---|
| `SPC_CLOCK_DELAY` | Global additional clock delay, roughly −127 ps … +127 ps |
| `SPC_CLOCK_AVAILDELAY_MIN` / `_MAX` / `_STEP` | Readable limits and step size, in ps |
| `SPC_ADJ_TIMING_CH0` / `_CH1` | Per-channel delay, 8-bit value −127…+127, roughly ±4 ps |

Within a Star-Hub cluster the inter-card skew is adjustable up to 200 ps on
10 GS/s models.

---

## 7. Acquisition modes

> *manual pp. 82–91, 126–128*

`SPC_CARDMODE` is a bitmap but exactly one bit must be set.
`SPC_AVAILCARDMODES` lists what the card supports.

| Mode | Value | Description |
|---|---|---|
| `SPC_REC_STD_SINGLE` | 0x1 | One trigger event, data to on-board memory, full sample rate |
| `SPC_REC_STD_MULTI` | 0x2 | Multiple trigger events, equal-size segments in on-board memory |
| `SPC_REC_STD_AVERAGE` | 0x20000 | Standard acquisition with hardware Block Average (firmware option) |
| `SPC_REC_FIFO_SINGLE` | 0x10 | Continuous streaming to PC memory, one trigger event |
| `SPC_REC_FIFO_MULTI` | 0x20 | Continuous streaming, multiple trigger events |
| `SPC_REC_FIFO_AVERAGE` | 0x200000 | Streaming with hardware Block Average |

The fundamental split is **Standard** vs **FIFO**:

- *Standard* modes write into on-board memory and are therefore limited by
  installed memory, but can always run at full sample rate regardless of what
  the host is doing. Data is read out after the acquisition finishes.
- *FIFO* modes use the whole on-board memory as a ring buffer and stream
  continuously to PC RAM or disk. They can run indefinitely, but the
  sustained rate is bounded by what the host bus and storage can absorb. The
  on-board memory acts as elastic buffering against host hiccups — which is
  exactly why the large memory option matters for streaming.

Programming and buffer handling are nearly identical between the two.

### Standard Single

> *manual pp. 88–89*

Memory is used as a circular buffer; the card records continuously until a
trigger arrives, then records `SPC_POSTTRIGGER` more samples and stops. The
pretrigger is therefore whatever fits before it:

```
pretrigger = SPC_MEMSIZE - SPC_POSTTRIGGER
```

```python
lMemsize = int32(16384)
spcm_dwSetParam_i32(hCard, SPC_CARDMODE,    SPC_REC_STD_SINGLE)
spcm_dwSetParam_i64(hCard, SPC_MEMSIZE,     lMemsize)     # total samples per channel
spcm_dwSetParam_i64(hCard, SPC_POSTTRIGGER, 8192)         # after the trigger
```

> When the card is started, the pretrigger area is filled **before** the
> trigger detection is armed. With a large pretrigger and a slow sample rate
> this delay before the card will accept a trigger at all can be
> considerable.

### FIFO Single

> *manual pp. 89–90*

```python
spcm_dwSetParam_i32(hCard, SPC_CARDMODE,    SPC_REC_FIFO_SINGLE)
spcm_dwSetParam_i32(hCard, SPC_PRETRIGGER,  1024)
spcm_dwSetParam_i64(hCard, SPC_SEGMENTSIZE, 4096)   # optional length limiting
spcm_dwSetParam_i64(hCard, SPC_LOOPS,       0)      # 0 = run until stopped
```

Total samples per channel = `SPC_LOOPS × SPC_SEGMENTSIZE`; with `SPC_LOOPS = 0`
the acquisition runs until you stop it. Differences from Standard Single:
the pretrigger is served from a small dedicated pretrigger FIFO and is
therefore much shorter (max 32 kSamples at 1 channel / 16 kSamples at 2
channels), and the acquisition length is not bounded by on-board memory.

### Multiple Recording (`SPC_REC_STD_MULTI` / `SPC_REC_FIFO_MULTI`)

> *manual pp. 126–128*

Memory is divided into equal segments; each trigger event fills one segment.
The re-arm time between segments is very short because the whole mode is
handled in hardware: 176 samples plus the programmed pretrigger in 2-channel
mode (352 samples in 1-channel mode).

```python
# Standard Multiple Recording: four segments of 1024 samples
spcm_dwSetParam_i32(hCard, SPC_CARDMODE,    SPC_REC_STD_MULTI)
spcm_dwSetParam_i64(hCard, SPC_SEGMENTSIZE, 1024)   # per segment, per channel
spcm_dwSetParam_i64(hCard, SPC_POSTTRIGGER,  768)   # -> pretrigger = 256
spcm_dwSetParam_i64(hCard, SPC_MEMSIZE,     4096)   # must be a multiple of segment size

# FIFO Multiple Recording: stream 256 segments
spcm_dwSetParam_i32(hCard, SPC_CARDMODE,    SPC_REC_FIFO_MULTI)
spcm_dwSetParam_i64(hCard, SPC_SEGMENTSIZE, 2048)
spcm_dwSetParam_i64(hCard, SPC_POSTTRIGGER, 1920)   # -> pretrigger = 128
spcm_dwSetParam_i64(hCard, SPC_LOOPS,        256)   # 0 = infinite
```

Per segment, `pretrigger = segment size − posttrigger`, and the pretrigger is
capped much lower than in Single mode. Exceeding it returns
`ERR_PRETRIGGERLEN`. Multiple Recording is the mode to use for repetitive
pulsed experiments: it discards the dead time between events, which both
saves memory and lets you stream at effective rates that plain FIFO could
not sustain. It pairs naturally with timestamps (§13).

### Limits table (native 12-bit, 2 GiSample installed)

> *manual p. 90 (Table 49); Multiple Recording p. 127 (Table 98)*

All figures in samples, per channel. `Mem` = 2 GiSample (or 8 GiSample with
the memory option); `Mem/2` applies when both channels are active.

| Channels | Mode | Memsize min/max/step | Pretrigger min/max/step | Posttrigger min/max/step | Segment min/max/step | Loops |
|---|---|---|---|---|---|---|
| 1 | Standard Single | 64 / Mem / 32 | 32 / Mem−32 / 32 | 32 / 256Gi−32 / 32 | — | — |
| 1 | Standard Multi | 64 / Mem / 32 | 32 / 32Ki / 32 | 32 / Mem−32 / 32 | 64 / Mem / 32 | — |
| 1 | FIFO Single | — | 32 / 32Ki / 32 | — | 64 / 8Gi−32 / 32 | 0…4Gi−1 |
| 1 | FIFO Multi | — | 32 / 32Ki / 32 | 32 / 256Gi−32 / 32 | 64 / pre+post / 32 | 0…4Gi−1 |
| 2 | Standard Single | 64 / Mem/2 / 32 | 32 / Mem/2−32 / 32 | 32 / 256Gi−32 / 32 | — | — |
| 2 | Standard Multi | 64 / Mem/2 / 32 | 32 / 16Ki / 32 | 32 / Mem/2−32 / 32 | 64 / Mem/2 / 32 | — |
| 2 | FIFO Single | — | 32 / 16Ki / 32 | — | 64 / 8Gi−32 / 32 | 0…4Gi−1 |
| 2 | FIFO Multi | — | 32 / 16Ki / 32 | 32 / 256Gi−32 / 32 | 64 / pre+post / 32 | 0…4Gi−1 |

`Loops = 0` means infinite. `[8Gi − 32]` = 8 589 934 560 samples. The
step sizes are not advisory — they come from the internal memory
organization, and values off the grid are rejected. The 8-bit and 12-bit
packed modes have their own (coarser) limit tables; see §9.

---

## 8. Card commands, status, and the state machine

> *manual pp. 83–85*

Everything is driven through the single command register `SPC_M2CMD`
(write-only, bitmask — several commands can be combined in one call). Illegal
combinations return `ERR_SEQUENCE`.

### Execution commands

> *manual p. 84 (Table 39)*

| Command | Value | Effect |
|---|---|---|
| `M2CMD_CARD_RESET` | 0x1 | Hardware + software reset |
| `M2CMD_CARD_WRITESETUP` | 0x2 | Write current settings to the card without starting it |
| `M2CMD_CARD_START` | 0x4 | Start with current settings (writes any changed settings first) |
| `M2CMD_CARD_ENABLETRIGGER` | 0x8 | Arm the trigger engine |
| `M2CMD_CARD_FORCETRIGGER` | 0x10 | Force a single trigger event now |
| `M2CMD_CARD_DISABLETRIGGER` | 0x20 | Disarm the trigger engine |
| `M2CMD_CARD_STOP` | 0x40 | Stop the current run |

The trigger engine starts **disabled** when the card is started, so a typical
start is `M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER`. Separating them is
useful when external hardware must be brought up between arming the card and
allowing triggers.

### Wait commands

> *manual p. 84*

These block until the state is reached (signalled by a card interrupt) or the
timeout expires. They consume no CPU while waiting.

| Command | Value | Waits for |
|---|---|---|
| `M2CMD_CARD_WAITPREFULL` | 0x1000 | Pretrigger area filled once |
| `M2CMD_CARD_WAITTRIGGER` | 0x2000 | First trigger detected |
| `M2CMD_CARD_WAITREADY` | 0x4000 | Run complete (all data acquired) |

| Register | Purpose |
|---|---|
| `SPC_TIMEOUT` | Timeout for all following wait commands, in ms. 0 disables (wait forever). Default: disabled |

A timeout is **not** an error state: the card is still running and will
complete normally. Send `M2CMD_CARD_STOP` if you want to abort after a
timeout, or simply loop back into the wait — a short timeout is a clean way
to update a GUI or check an abort flag while waiting.

### DMA commands

> *manual pp. 87, 138*

| Command | Value | Effect |
|---|---|---|
| `M2CMD_DATA_STARTDMA` | 0x10000 | Start the DMA transfer for an already defined buffer |
| `M2CMD_DATA_WAITDMA` | 0x20000 | Wait until transfer ends or `notify size` bytes are available |
| `M2CMD_DATA_STOPDMA` | 0x40000 | Stop a running transfer (data invalid afterwards) |
| `M2CMD_EXTRA_STARTDMA` / `_WAITDMA` / `_STOPDMA` | 0x100000 / 0x200000 / 0x400000 | Same for the timestamp FIFO |
| `M2CMD_EXTRA_POLL` | 0x800000 | Poll the timestamp FIFO instead of using DMA |

### Status

> *manual pp. 85, 87, 139*

`SPC_M2STATUS` (read-only bitmask):

| Flag | Value | Meaning |
|---|---|---|
| `M2STAT_CARD_PRETRIGGER` | 0x1 | Pretrigger area has been filled once |
| `M2STAT_CARD_TRIGGER` | 0x2 | First trigger detected |
| `M2STAT_CARD_READY` | 0x4 | Run finished |
| `M2STAT_CARD_SEGMENT_PRETRG` | 0x8 | A segment's pretrigger area is filled |
| `M2STAT_DATA_BLOCKREADY` | 0x100 | At least one notify-size block of sample data is available |
| `M2STAT_DATA_END` | 0x200 | Sample data transfer complete |
| `M2STAT_DATA_OVERRUN` | 0x400 | FIFO overrun (acquisition) |
| `M2STAT_DATA_ERROR` | 0x800 | Internal transfer error |
| `M2STAT_EXTRA_BLOCKREADY` / `_END` / `_OVERRUN` / `_ERROR` | 0x1000 / 0x2000 / 0x4000 / 0x8000 | Same for the timestamp FIFO |

### Sequence of an acquisition

> *manual p. 85*

1. `M2CMD_CARD_START` — card begins filling the pretrigger area, trigger
   detection not yet armed.
2. Pretrigger full → `M2STAT_CARD_PRETRIGGER` set.
3. `M2CMD_CARD_ENABLETRIGGER` (or sent together with START) → trigger engine
   armed.
4. Trigger event → `M2STAT_CARD_TRIGGER` set, posttrigger data acquired.
5. All posttrigger data acquired → `M2STAT_CARD_READY` set, data can be read.

Complete minimal Standard Single acquisition:

```python
spcm_dwSetParam_i32(hCard, SPC_TIMEOUT, 5000)
dwError = spcm_dwSetParam_i32(hCard, SPC_M2CMD,
                              M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER)
if spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_CARD_WAITTRIGGER) == ERR_TIMEOUT:
    spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_CARD_FORCETRIGGER)
spcm_dwSetParam_i32(hCard, SPC_TIMEOUT, 0)
spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_CARD_WAITREADY)
```

> If a firmware update failed and the card booted its golden (recovery)
> image, every `M2CMD_CARD_START` returns `ERR_GOLDENIMAGE`. The golden image
> exists only to allow a safe re-flash of the standard firmware.

---

## 9. Data organization, sample formats, and voltage conversion

> *manual pp. 94–95, 129–134*

### Multiplexing

> *manual p. 94 (Table 53)*

Data in the transfer buffer is **interleaved** across active channels:

| Active channels | Buffer layout from offset 0 |
|---|---|
| Ch0 only | A0 A1 A2 A3 A4 A5 … |
| Ch1 only | B0 B1 B2 B3 B4 B5 … |
| Ch0 + Ch1 | A0 B0 A1 B1 A2 B2 A3 B3 … |

(Aₙ = sample n of channel 0, Bₙ = sample n of channel 1.) Deinterleaving to
per-channel arrays is a required step before analysis; with numpy this is a
strided slice, not a loop.

Data is stored **little-endian**, so it maps directly onto x86/x64 memory
with no byte swapping.

### Native sample format (12 bit in 16 bit)

> *manual p. 95 (Table 54)*

Each sample occupies 2 bytes, two's complement, sign-extended:

| Bit | Standard mode | With `SPCM_XMODE_DIGIN` enabled |
|---|---|---|
| D15…D12 | Sign extension of bit 11 | XIO digital input values |
| D11 | ADC bit 11 (MSB) | ADC bit 11 (MSB) |
| D10…D0 | ADC bits 10…0 | ADC bits 10…0 |

Values range −2048…+2047.

> If any synchronous digital input is enabled, the automatic sign extension
> is gone. Your software must mask off D15…D12 and re-sign-extend from bit 11
> before treating the value as a signed integer.

### Converting to volts

> *manual p. 95*

```
V_in = ADC_code × (InputRange_peak / ADC_max)
```

where `ADC_max` is read from `SPC_MIINST_MAXADCVALUE`. **Always read this
register** rather than assuming 2048 — it accounts for card-specific reserved
codes used for gain/offset compensation.

```python
lMaxADCValue = int32(0)
spcm_dwGetParam_i32(hCard, SPC_MIINST_MAXADCVALUE, byref(lMaxADCValue))

import numpy as np
samples = np.frombuffer(buf, dtype=np.int16)          # raw, interleaved
ch0 = samples[0::2]                                    # deinterleave, 2-channel case
ch1 = samples[1::2]
volts_ch0 = ch0.astype(np.float64) * (1000.0 / lMaxADCValue.value) / 1000.0   # ±1 V range
```

Mask out any extra bits (digital inputs, over-range flags) and sign-extend
*before* applying the formula.

### 8-bit storage mode (low resolution)

> *manual pp. 129–130*

From firmware V5 onward, the card can drop the samples to 8 bit in hardware,
halving memory use and bus bandwidth. Because the ENOB is below 8 bits at
high frequencies anyway, the cost in real resolution is modest.

```python
spcm_dwSetParam_i32(hCard, SPC_DATACONVERSION, SPCM_DC_12BIT_TO_8BIT)   # 0x80
```

- `SPC_AVAILDATACONVERSION` lists the supported conversions;
  `SPCM_DC_NONE` (0) turns conversion off.
- Compatible with `SPC_REC_STD_SINGLE`, `SPC_REC_STD_MULTI`,
  `SPC_REC_FIFO_SINGLE`, `SPC_REC_FIFO_MULTI`.
- The hardware simply shifts the sample down, discarding the low bits: the
  stored D7…D0 correspond to original D11…D4.
- 1 byte per sample; effective memory becomes 4 GiSample (or 16 GiSample with
  the upgrade), and all pre/post/segment step sizes double (64 instead of 32,
  minimum memsize 128, minimum segment 128).
- For conversion to volts, `ADC_max` becomes **128** (the 8-bit code uses the
  full range with no reserved bits).

### 12-bit packed mode

> *manual pp. 131–134*

From firmware V6, samples can be stored as a seamless 12-bit stream — two
samples in three bytes instead of four, saving 25 % of memory and bandwidth
with **no loss of resolution**.

```python
spcm_dwSetParam_i32(hCard, SPC_DATACONVERSION, SPCM_DC_12BIT_TO_12BITPACKED)  # 0x1000
```

- Compatible with `SPC_REC_STD_MULTI`, `SPC_REC_FIFO_SINGLE`,
  `SPC_REC_FIFO_MULTI`. **Not** compatible with `SPC_REC_STD_SINGLE`.
- No synchronous digital inputs can be used, since the packing consumes the
  four "redundant" bits.
- Step sizes become 256 samples for segment size; memsize step 256 (1 ch).
- Requires unpacking before analysis. Choose buffer and notify sizes that are
  **multiples of three** so that no sample is split across a block boundary.

Unpacking logic (per pair of samples, three bytes at `packed[i]`):

```
sample0:  high nibble = packed[i+1] & 0x0F
          mid  nibble = (packed[i+0] >> 4) & 0x0F
          low  nibble = packed[i+0] & 0x0F
sample1:  high nibble = (packed[i+2] >> 4) & 0x0F
          mid  nibble = packed[i+2] & 0x0F
          low  nibble = (packed[i+1] >> 4) & 0x0F

value = ((high << 12) >> 4) | (mid << 4) | low     # the shift pair sign-extends
```

In two-channel mode each iteration handles one sample from each channel, so
unpacking directly into two separate per-channel arrays is more efficient
than reproducing the interleaved layout and splitting afterwards.

---

## 10. DMA buffer handling

> *manual pp. 85–93*

Transfers use a two-stage buffer: the on-board memory acts as a hardware
FIFO, and a region of PC RAM acts as a software buffer. A busmaster
scatter-gather DMA engine on the card moves data between them without CPU
involvement, and continues even while the host is busy with other work.

### Defining the buffer

> *manual pp. 85–87*

```python
spcm_dwDefTransfer_i64(hCard,
                       SPCM_BUF_DATA,        # buffer type
                       SPCM_DIR_CARDTOPC,   # direction
                       lNotifySize,          # bytes per notification (0 = only at end)
                       pvBuffer,             # page-aligned buffer
                       uint64(0),            # offset into board memory
                       qwBufferSize)         # total bytes
```

Buffer types: `SPCM_BUF_DATA` (1000), `SPCM_BUF_ABA` (2000, not present on
M5i), `SPCM_BUF_TIMESTAMP` (3000).

Directions: `SPCM_DIR_CARDTOPC` (1) for acquisition, `SPCM_DIR_PCTOCARD` (0)
for replay cards, `SPCM_DIR_CARDTOGPU` (2) / `SPCM_DIR_GPUTOCARD` (3) for
RDMA into CUDA GPU memory (SCAPP option, Linux only).

**M5i-specific constraints — these differ from other Spectrum series:**

- The **notify size** must be a multiple of 4 KiByte, or one of the
  fractions 64, 128, 256, 512, 1 Ki, 2 Ki bytes. No other values are legal
  (`ERR_NOTIFYSIZE`). For timestamps the minimum is 2 Ki; below that, use
  polling mode.
- The **transfer length** (`qwTransferLen`) must be an integer multiple of
  **64 bytes**. This applies to the timestamp buffer too.
- The buffer pointer must be **page-aligned (4096 bytes)**. A misaligned
  buffer can corrupt data.

Each defined buffer is used once and is automatically invalidated when the
transfer completes. `spcm_dwInvalidateBuf` releases it early.

### Allocating an aligned buffer

> *manual p. 86; continuous memory pp. 173–176*

The shipped helper (`spcm_tools.py`) over-allocates and returns an aligned
view:

```python
pvBuffer = pvAllocMemPageAligned(qwBufferSize.value)
```

Alternatively, ask the driver for a kernel-allocated *continuous* buffer,
which gives the best transfer performance especially with small notify sizes:

```python
pvBuffer = ptr8()
qwContBufLen = uint64(0)
spcm_dwGetContBuf_i64(hCard, SPCM_BUF_DATA, byref(pvBuffer), byref(qwContBufLen))
if qwContBufLen.value < qwBufferSize.value:
    pvBuffer = cast(pvAllocMemPageAligned(qwBufferSize.value), ptr8)
```

For numpy-native work, allocate an over-sized numpy array and slice to the
next 4096-byte boundary (see the CUDA example, §17).

### The handshake registers

> *manual pp. 91–92 (Tables 51, 52)*

| Register | Direction | Meaning (card→PC) |
|---|---|---|
| `SPC_DATA_AVAIL_USER_LEN` | read | Bytes now filled with new data and available to you |
| `SPC_DATA_AVAIL_USER_POS` | read | Byte offset in the buffer where those bytes start |
| `SPC_DATA_AVAIL_CARD_LEN` | write | Bytes you are finished with and hand back to the driver |
| `SPC_FILLSIZEPROMILLE` | read | On-board FIFO fill level in ‰ (hardware reports in 1/16 steps, so ≈63 ‰ granularity) |

Immediately after start, `USER_LEN` is 0 and `CARD_LEN` equals the whole
buffer. `USER_LEN` respects the notify size: even if fewer bytes have
technically arrived, you are not notified until a full notify-size block is
there.

### Standard Single read-out

> *manual p. 89*

```python
qwBufferSize = uint64(lMemsize.value * 2 * lSetChannels.value)
pvBuffer = pvAllocMemPageAligned(qwBufferSize.value)

spcm_dwDefTransfer_i64(hCard, SPCM_BUF_DATA, SPCM_DIR_CARDTOPC,
                       int32(0), pvBuffer, uint64(0), qwBufferSize)
spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_DATA_STARTDMA | M2CMD_DATA_WAITDMA)
```

### FIFO streaming loop

> *manual pp. 90, 92–93*

```python
while qwTotalMem.value < qwToTransfer.value:
    dwError = spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_DATA_WAITDMA)
    if dwError != ERR_OK:
        break

    spcm_dwGetParam_i32(hCard, SPC_M2STATUS,            byref(lStatus))
    spcm_dwGetParam_i32(hCard, SPC_DATA_AVAIL_USER_LEN, byref(lAvailUser))
    spcm_dwGetParam_i32(hCard, SPC_DATA_AVAIL_USER_POS, byref(lPCPos))

    if lAvailUser.value >= lNotifySize.value:
        qwTotalMem.value += lNotifySize.value
        # process lNotifySize bytes starting at offset lPCPos.value
        spcm_dwSetParam_i32(hCard, SPC_DATA_AVAIL_CARD_LEN, lNotifySize)

spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_CARD_STOP | M2CMD_DATA_STOPDMA)
```

Points to keep in mind:

- Handing bytes back with `SPC_DATA_AVAIL_CARD_LEN` promptly is what keeps
  the stream alive. If the software buffer fills, the on-board FIFO keeps
  absorbing until it too is full — which is why a large on-board memory makes
  streaming robust against host dead time.
- On an overrun the transfer stops, but data already in on-board memory
  continues to be transferred, so you may still see valid data after the
  overrun status appears.
- Waiting via `M2CMD_DATA_WAITDMA` uses no CPU; polling the status registers
  instead is also legal, and in polling mode the available-byte count still
  respects the notify size.
- **Never free or delete a buffer while DMA is running.** Send
  `M2CMD_DATA_STOPDMA` explicitly before invalidating, particularly on remote
  (Ethernet) devices where higher latency makes the race more likely.
- A buffer declared with a length that doesn't match the real allocation can
  cause the DMA engine to write outside your memory.

---

## 11. Trigger system

> *manual pp. 101–121*

This is the most elaborate part of the card and the part most worth
understanding properly. The M5i trigger engine offers more than 10 external
trigger modes and up to 20 internal ones, combinable through OR and AND
masks, with programmable delay and holdoff, and OR-combination across cards
when the Star-Hub is used.

### 11.1 Architecture

> *manual p. 101*

Trigger sources on this card:

- **Channel triggers** — each analog input has **two** level comparators,
  which is what makes window, re-arm, and hysteresis modes possible.
- **Ext0 (Trig In)** — the main external analog trigger, one comparator,
  programmable ±5 V.
- **X0, X1, X2, X3** — the multi-purpose lines, usable as 3.3 V LVTTL logic
  triggers. In the trigger registers these appear as *Ext1…Ext4*.
- **Software trigger** — fires immediately after start.
- **Force trigger** — a one-shot software override.

The signal path is:

```
sources → OR mask ─┐
                   ├→ (combined) → Star-Hub OR → trigger delay → trigger event
sources → AND mask ┘
```

with a global enable/disable (arm) stage in front, and force trigger
bypassing the enable. Because the delay sits *after* the Star-Hub, each card
in a synchronized cluster can still apply its own delay to the common
trigger.

### 11.2 OR and AND masks

> *manual pp. 102–104*

The masks are split into a **general mask** (external and software sources)
and a **channel mask** (the analog channels).

**General OR mask — `SPC_TRIG_ORMASK`** (readable availability:
`SPC_TRIG_AVAILORMASK`):

| Constant | Value | Source |
|---|---|---|
| `SPC_TMASK_NONE` | 0x0 | No source |
| `SPC_TMASK_SOFTWARE` | 0x1 | Software trigger (fires immediately after start) |
| `SPC_TMASK_EXT0` | 0x2 | Main analog trigger input (Trig In) |
| `SPC_TMASK_EXT1` | 0x4 | X0 logic trigger |
| `SPC_TMASK_EXT2` | 0x8 | X1 logic trigger |
| `SPC_TMASK_EXT3` | 0x10 | X2 logic trigger |
| `SPC_TMASK_EXT4` | 0x20 | X3 logic trigger |

**General AND mask — `SPC_TRIG_ANDMASK`** (availability:
`SPC_TRIG_AVAILANDMASK`): the same constants except `SPC_TMASK_SOFTWARE`,
which cannot participate in an AND.

**Channel OR mask — `SPC_TRIG_CH_ORMASK0`** (availability:
`SPC_TRIG_CH_AVAILORMASK0`) and **channel AND mask —
`SPC_TRIG_CH_ANDMASK0`** (availability: `SPC_TRIG_CH_AVAILANDMASK0`):

| Constant | Value | Source |
|---|---|---|
| `SPC_TMASK0_CH0` | 0x1 | Channel 0 |
| `SPC_TMASK0_CH1` | 0x2 | Channel 1 |

If a mask has no input enabled, its output is logic *true*, so that an empty
mask does not block the other one.

> **The single most common mistake:** `SPC_TRIG_ORMASK` defaults to
> `SPC_TMASK_SOFTWARE`. If you set up a channel or external trigger without
> explicitly clearing the OR mask, the software trigger fires immediately and
> your real trigger is never seen. Always write
> `spcm_dwSetParam_i32(hCard, SPC_TRIG_ORMASK, SPC_TMASK_NONE)` first when
> you are not using the software trigger.

### 11.3 Combining sources

> *manual pp. 104–105*

The rule of thumb from the manual:

- Sources whose mode produces an **edge event** (`SPC_TM_POS`, `SPC_TM_NEG`,
  `SPC_TM_BOTH`, window modes) belong in the **OR mask**.
- Sources whose mode produces a **level/gate event** (`SPC_TM_HIGH`,
  `SPC_TM_LOW`, hysteresis modes) belong in the **AND mask**, where they act
  as gates or enables for the edge sources.

Channel 0 gates edges detected on Ext0:

```python
spcm_dwSetParam_i32(hCard, SPC_TRIG_EXT0_MODE,    SPC_TM_POS)   # edge on Ext0
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH0_MODE,     SPC_TM_HIGH)  # level on Ch0
spcm_dwSetParam_i32(hCard, SPC_TRIG_ORMASK,       SPC_TMASK_EXT0)   # also clears software trigger
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH_ANDMASK0,  SPC_TMASK0_CH0)
```

Ext0 gates edges on either channel:

```python
spcm_dwSetParam_i32(hCard, SPC_TRIG_EXT0_MODE,   SPC_TM_HIGH)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH0_MODE,    SPC_TM_POS)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH1_MODE,    SPC_TM_NEG)
spcm_dwSetParam_i32(hCard, SPC_TRIG_ORMASK,      SPC_TMASK_NONE)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH_ORMASK0,  SPC_TMASK0_CH0 | SPC_TMASK0_CH1)
spcm_dwSetParam_i32(hCard, SPC_TRIG_ANDMASK,     SPC_TMASK_EXT0)
```

### 11.4 Software, force, and enable trigger

> *manual p. 105*

**Software trigger** — acquisition starts as soon as the card is started and
the trigger engine is armed (after the pretrigger area has been filled):

```python
spcm_dwSetParam_i32(hCard, SPC_TRIG_ORMASK, SPC_TMASK_SOFTWARE)
```

**Force trigger** — generates exactly one trigger event, but only while the
card is actually waiting for a trigger. Afterwards the engine reverts to the
programmed mode. In Multiple Recording this fills exactly one segment. Force
overrides the enable state.

```python
spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_CARD_FORCETRIGGER)
```

**Enable / disable** — arms or disarms the entire trigger engine with one
command. The engine starts disabled:

```python
spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_CARD_ENABLETRIGGER)
spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_CARD_DISABLETRIGGER)
```

### 11.5 Trigger delay

> *manual p. 106*

Programmed in samples; the resulting time is `delay / sample rate`. It shifts
the trigger event itself and does **not** change the pre/post-trigger split.

| Register | Purpose |
|---|---|
| `SPC_TRIG_AVAILDELAY` | Maximum available delay |
| `SPC_TRIG_AVAILDELAY_STEP` | Step size in sample clocks |
| `SPC_TRIG_DELAY` | The delay itself; 0 = none |

Range: 0, then 32 … (256 GiSamples − 32) in steps of 32.

```python
spcm_dwSetParam_i64(hCard, SPC_TRIG_DELAY, 1984)
```

### 11.6 Trigger holdoff

> *manual p. 106*

An artificial dead time inserted after each posttrigger area, during which
all trigger events are rejected. Intended for segmented (Multiple Recording)
acquisitions of bursty signals, where you want one segment per burst rather
than one per pulse within the burst.

| Register | Purpose |
|---|---|
| `SPC_TRIG_AVAILHOLDOFF` / `_STEP` | Maximum and step size |
| `SPC_TRIG_HOLDOFF` | Holdoff in samples; 0 = none |

Same range and stepping as the delay: 32 … (256 GiSamples − 32), step 32.

### 11.7 Trigger counter

> *manual p. 107*

`SPC_TRIGGERCOUNTER` (read) returns the number of trigger events acquired
since the acquisition started, in both Standard and FIFO modes, and is
readable while running. The internal counter is 48 bits, so read it with
64-bit access (or two 32-bit accesses) if you expect more than 2³² events.

Requires firmware V1 or newer on M5i and driver ≥ V2.17; using it without
adequate firmware raises a driver error.

### 11.8 Main analog external trigger (Ext0)

> *manual pp. 107–110; electrical specs pp. 18–19*

The primary trigger input: an input stage with software-selectable 3 kΩ or
50 Ω termination and one comparator programmable across ±5000 mV.

**Specifications:**

| Property | Value |
|---|---|
| Input level | ±5 V |
| Trigger level | ±5 V, 10 mV step size |
| Sensitivity (min signal swing) | 200 mVpp |
| Termination | 50 Ω or 3 kΩ (software) |
| Bandwidth (typ) | DC–2 GHz at 50 Ω; DC–750 MHz at 3 kΩ |
| Over-voltage protection | ±20 V at 50 Ω; 7 Vrms at 3 kΩ |
| Min pulse width / min pause | 2 samples each |
| Max detectable trigger frequency | current sample rate / 4 |
| Trigger accuracy | 1 sample |

**Registers:**

| Register | Purpose |
|---|---|
| `SPC_TRIG_EXT0_AVAILMODES` | Bitmask of supported modes |
| `SPC_TRIG_EXT0_MODE` | The mode |
| `SPC_TRIG_EXT0_LEVEL0` | Trigger level in **mV**, −5000 … +5000 |
| `SPC_TRIG_EXT_AVAIL0_MIN` / `_MAX` / `_STEP` | Readable level limits and step, in mV |
| `SPC_TRIG_TERM` | 1 = 50 Ω termination, 0 = high impedance (3 kΩ) |

**Modes:**

| Mode | Value | Behaviour |
|---|---|---|
| `SPC_TM_NONE` | 0x0 | Input not used for triggering |
| `SPC_TM_POS` | 0x1 | Positive (rising) edge across the level |
| `SPC_TM_NEG` | 0x2 | Negative (falling) edge across the level |
| `SPC_TM_BOTH` | 0x4 | Any crossing of the level |
| `SPC_TM_HIGH` | 0x8 | Signal above the level (level/gate) |
| `SPC_TM_LOW` | 0x10 | Signal below the level (level/gate) |

The edge modes behave exactly like a conventional oscilloscope trigger. The
level modes generate an internal gate, primarily intended to gate a second
trigger source via the AND mask. If a level mode is used as the *only*
trigger source, the card triggers on entering the level — and triggers
immediately at start if the condition is already satisfied.

```python
spcm_dwSetParam_i32(hCard, SPC_TRIG_ORMASK,       SPC_TMASK_EXT0)
spcm_dwSetParam_i32(hCard, SPC_TRIG_EXT0_MODE,    SPC_TM_POS)
spcm_dwSetParam_i32(hCard, SPC_TRIG_EXT0_LEVEL0,  1500)   # 1.5 V
spcm_dwSetParam_i32(hCard, SPC_TRIG_TERM,         1)      # 50 Ω
```

> With 50 Ω termination and a 50 Ω source, the two terminations form a 1:2
> divider and your signal level at the input halves. Re-check (and usually
> halve) the programmed trigger level when switching termination. "Trigger
> never fires after enabling 50 Ω" is almost always this.

### 11.9 External logic triggers (X0–X3)

> *manual pp. 111–113; electrical specs p. 19*

Each multi-purpose line can serve as a 3.3 V LVTTL trigger input. In the
register naming, X0 → Ext1, X1 → Ext2, X2 → Ext3, X3 → Ext4.

| Line | Mode register | Availability register | OR/AND mask constant |
|---|---|---|---|
| X0 | `SPC_TRIG_EXT1_MODE` | `SPC_TRIG_EXT1_AVAILMODES` | `SPC_TMASK_EXT1` |
| X1 | `SPC_TRIG_EXT2_MODE` | `SPC_TRIG_EXT2_AVAILMODES` | `SPC_TMASK_EXT2` |
| X2 | `SPC_TRIG_EXT3_MODE` | `SPC_TRIG_EXT3_AVAILMODES` | `SPC_TMASK_EXT3` |
| X3 | `SPC_TRIG_EXT4_MODE` | `SPC_TRIG_EXT4_AVAILMODES` | `SPC_TMASK_EXT4` |

Modes are the same set as Ext0 — `SPC_TM_NONE`, `SPC_TM_POS`, `SPC_TM_NEG`,
`SPC_TM_BOTH`, `SPC_TM_HIGH`, `SPC_TM_LOW` — but with no programmable level:
the threshold is the LVTTL logic threshold.

Behaviour: the card triggers on the first matching edge (or level) detected
after the card is started. For the level modes, if the condition is already
true at start, that counts as a trigger. The next trigger event is only
detected once the current recording has finished and the card is armed again.

**Electrical:** high-impedance 10 kΩ to 3.3 V by default, or 50 Ω to GND when
enabled via `SPC_X0_TERM` … `SPC_X3_TERM` (1 = 50 Ω, 0 = high-Z). Levels:
Low ≤ 0.8 V, High ≥ 2.0 V; absolute maximum −0.5 V to +4.0 V; bandwidth
≈125 MHz; min pulse width 2 samples.

### 11.10 Channel (analog) triggers

> *manual pp. 114–121*

The richest set of modes, made possible by the two comparators per channel.

| Register | Purpose |
|---|---|
| `SPC_TRIG_CH_AVAILMODES` | Bitmask of supported channel trigger modes |
| `SPC_TRIG_CH0_MODE` / `SPC_TRIG_CH1_MODE` | Mode for Ch0 / Ch1 |
| `SPC_TRIG_CH0_LEVEL0` / `SPC_TRIG_CH1_LEVEL0` | Main level (or upper level when two are used), −2047 … +2047 |
| `SPC_TRIG_CH0_LEVEL1` / `SPC_TRIG_CH1_LEVEL1` | Auxiliary level (lower / re-arm / hysteresis level), −2047 … +2047 |
| `SPC_READTRGLVLCOUNT` | Number of distinct trigger levels available (± that value) |

The channel that is to trigger must also be enabled in
`SPC_TRIG_CH_ORMASK0` (or the AND mask).

**Available modes:**

| Mode | Value | Description |
|---|---|---|
| `SPC_TM_NONE` | 0x0 | Channel not used for triggering |
| `SPC_TM_POS` | 0x1 | Rising edge across level 0 |
| `SPC_TM_NEG` | 0x2 | Falling edge across level 0 |
| `SPC_TM_BOTH` | 0x4 | Either edge across level 0 |
| `SPC_TM_HIGH` | 0x8 | Signal above level 0 (gate) |
| `SPC_TM_LOW` | 0x10 | Signal below level 0 (gate) |
| `SPC_TM_POS \| SPC_TM_REARM` | 0x01000001 | Rising-edge trigger on level 0, armed by crossing level 1 |
| `SPC_TM_NEG \| SPC_TM_REARM` | 0x01000002 | Falling-edge trigger on level 1, armed by crossing level 0 |
| `SPC_TM_WINENTER` | 0x20 | Signal enters the window between the two levels |
| `SPC_TM_WINLEAVE` | 0x40 | Signal leaves the window |
| `SPC_TM_INWIN` | 0x80 | Signal inside the window (gate) |
| `SPC_TM_OUTSIDEWIN` | 0x100 | Signal outside the window (gate) |
| `SPC_TM_POS \| SPC_TM_HYSTERESIS` | 0x20000001 | Rising-edge gate with hysteresis |
| `SPC_TM_NEG \| SPC_TM_HYSTERESIS` | 0x20000002 | Falling-edge gate with hysteresis |
| `SPC_TM_HIGH \| SPC_TM_HYSTERESIS` | 0x20000008 | High-level gate with hysteresis |
| `SPC_TM_LOW \| SPC_TM_HYSTERESIS` | 0x20000010 | Low-level gate with hysteresis |
| `SPC_TM_POS \| SPC_TM_REARM \| SPC_TM_HYSTERESIS` | 0x21000001 | Re-armed rising-edge gate with hysteresis |
| `SPC_TM_NEG \| SPC_TM_REARM \| SPC_TM_HYSTERESIS` | 0x21000002 | Re-armed falling-edge gate with hysteresis |

**What the mode families actually do:**

*Edge modes* (`POS`, `NEG`, `BOTH`) — the classic oscilloscope trigger. The
channel is sampled continuously; when the signal crosses level 0 in the
specified direction, a trigger event is generated.

*Level modes* (`HIGH`, `LOW`) — generate an internal gate that is true while
the signal is above (or below) level 0. As a standalone trigger this fires on
entering the level, or immediately at start if already there. Their real
purpose is gating another source through the AND mask.

*Re-arm modes* — the answer to false triggering on noisy signals. Two levels
are used: the trigger engine only becomes armed once the signal has crossed
the **re-arm level**, and only then does a crossing of the **trigger level**
count. After a trigger, the engine disarms until the re-arm level is crossed
again. For `POS | REARM`, level 0 is the trigger level and level 1 the re-arm
level; for `NEG | REARM` the roles swap (level 1 triggers, level 0 arms). Set
the re-arm level well away from the noise band and the trigger level where
you actually want it, and noise excursions can no longer produce spurious
triggers.

*Window modes* — the two levels define a voltage window.
`SPC_TM_WINENTER` triggers each time the signal enters the window from
outside; `SPC_TM_WINLEAVE` triggers each time it leaves from inside;
`SPC_TM_INWIN` and `SPC_TM_OUTSIDEWIN` are the corresponding gate (level)
versions. These are how you catch a signal *deviating* from an expected band —
useful for fault or glitch capture where you don't know the polarity of the
excursion.

*Hysteresis modes* — generate a gate with separate start and stop
thresholds, so a noisy signal cannot chatter the gate. For
`POS | HYSTERESIS`, the gate starts when the signal rises through the trigger
level and stops when it falls back through the hysteresis level (level 1).
`NEG | HYSTERESIS` is the mirror image. These are edge-triggered, so a signal
already high at card start does not open the gate. The `HIGH | HYSTERESIS`
and `LOW | HYSTERESIS` variants are level-triggered, so an already-satisfied
condition at start *does* open the gate. The re-arm+hysteresis combinations
add the arming behaviour on top.

**Trigger levels.** Channel trigger levels are expressed in **ADC codes**
(12-bit resolution, −2047…+2047), not in millivolts, and are therefore
relative to the currently selected input range. Changing the range changes
the voltage a given code corresponds to, so the level must be recomputed.

```
level_code = round(desired_voltage / (InputRange_peak / 2048))
```

Resulting step size per range:

| Range | Step per code |
|---|---|
| ±200 mV | 97.66 µV |
| ±500 mV | 244.14 µV |
| ±1 V | 488.28 µV |
| ±2.5 V | 1.2207 mV |

```python
# Rising-edge trigger at +5.0 mV on Ch0, ±200 mV range
spcm_dwSetParam_i32(hCard, SPC_TRIG_ORMASK,      SPC_TMASK_NONE)   # kill the default software trigger
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH_ORMASK0,  SPC_TMASK0_CH0)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH0_MODE,    SPC_TM_POS)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH0_LEVEL0,  51)               # 51 × 97.66 µV ≈ 5.0 mV
```

Rising edge on Ch0 **or** falling edge on Ch1:

```python
spcm_dwSetParam_i32(hCard, SPC_TRIG_ORMASK,     SPC_TMASK_NONE)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH_ORMASK0, SPC_TMASK0_CH0 | SPC_TMASK0_CH1)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH0_MODE,   SPC_TM_POS)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH1_MODE,   SPC_TM_NEG)
```

Window trigger catching any excursion outside ±100 mV on Ch0 (±1 V range,
488.28 µV/code → 100 mV ≈ code 205):

```python
spcm_dwSetParam_i32(hCard, SPC_TRIG_ORMASK,      SPC_TMASK_NONE)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH_ORMASK0,  SPC_TMASK0_CH0)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH0_MODE,    SPC_TM_WINLEAVE)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH0_LEVEL0,  205)    # upper
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH0_LEVEL1, -205)    # lower
```

Re-arm trigger on a noisy rising pulse (±1 V range, trigger at ≈50 mV,
re-arm at ≈−100 mV, so the signal must return well below baseline before the
next trigger can occur):

```python
spcm_dwSetParam_i32(hCard, SPC_TRIG_ORMASK,      SPC_TMASK_NONE)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH_ORMASK0,  SPC_TMASK0_CH0)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH0_MODE,    SPC_TM_POS | SPC_TM_REARM)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH0_LEVEL0,  102)    # trigger level
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH0_LEVEL1, -205)    # re-arm level
```

---

## 12. Multi-purpose I/O lines X0–X3

> *manual pp. 122–125; electrical details pp. 19, 170–171*

Four SMA lines on the front plate, each individually software-configurable as
input or output. **They are switched off (tristate) after power-on and after
reset**, held to logic HIGH by on-board 10 kΩ pull-ups; if you need a defined
LOW, add an external pull-down (≈1 kΩ).

> Be careful programming a line as an output while an external source is
> still connected to it — that can damage the external equipment or the card.

| Register | Purpose |
|---|---|
| `SPCM_X0_MODE` … `SPCM_X3_MODE` | Mode of each line (one mode at a time) |
| `SPCM_X0_AVAILMODES` … `SPCM_X3_AVAILMODES` | Bitmask of supported modes — read this first, as it can change with firmware |
| `SPC_X0_TERM` … `SPC_X3_TERM` | 1 = 50 Ω to GND, 0 = high-Z (10 kΩ to 3.3 V) |

| Mode | Value | Description |
|---|---|---|
| `SPCM_XMODE_DISABLE` | 0x0 | Off, tristate (default) |
| `SPCM_XMODE_ASYNCIN` | 0x1 | Asynchronous digital input, read via `SPCM_XX_ASYNCIO` |
| `SPCM_XMODE_ASYNCOUT` | 0x2 | Asynchronous digital output |
| `SPCM_XMODE_DIGIN` | 0x4 | Synchronous digital input, merged into the ADC sample stream (see §9 — sign extension is lost) |
| `SPCM_XMODE_TRIGOUT` | 0x20 | Trigger output: HIGH on trigger recognition, LOW after acquisition ends (per segment in Multi mode; HIGH for the whole run in FIFO single) |
| `SPCM_XMODE_RUNSTATE` | 0x100 | HIGH while the card is running |
| `SPCM_XMODE_ARMSTATE` | 0x200 | HIGH while armed and waiting for a trigger; LOW while acquiring pretrigger data or after the trigger |
| `SPCM_XMODE_PULSEGEN` | 0x80000 | Output of the same-index pulse generator (Pulse Generator option) |

A line can also be used as a logic trigger input (§11.9). Asynchronous input
reads work even when the line is configured as trigger input or digital input.

> Changes to `SPCM_Xn_MODE` only take effect on the next
> `M2CMD_CARD_START` or `M2CMD_CARD_WRITESETUP`.

**Electrical summary.** Input: 3.3 V LVTTL (Low ≤ 0.8 V, High ≥ 2.0 V),
absolute max −0.5 V to +4.0 V, bandwidth ≈125 MHz, termination 10 kΩ to
3.3 V or 50 Ω to GND. Output: 50 Ω, 3.3 V LVTTL, drive up to ±48 mA, min
high/low time 4 ns, max frequency ≈125 MHz. Internal update rate for outputs
on this model: 1/4 of the sampling clock below 5 GS/s, 1/8 above.

---

## 13. Timestamps

> *manual pp. 135–143*

Requires the Timestamp option (`SPCM_FEAT_TIMESTAMP`). A wide counter runs at
the sampling rate; on every trigger event its value is latched into a
separate hardware FIFO with its own DMA engine, so timestamps and sample data
transfer simultaneously and independently.

```
t = timestamp / sampling_rate
Δt(n → n+1) = (timestamp[n+1] − timestamp[n]) / sampling_rate
```

Note that the *trigger events* are timestamped, not the start of each
segment: the first available sample of a segment sits at
`timestamp − pretrigger`.

### Configuration

> *manual pp. 135–137*

`SPC_TIMESTAMP_CMD` is a bitfield combining a mode, a counter source, and
optional data-format flags. `SPC_TIMESTAMP_AVAILMODES` reports what is
supported.

| Constant | Value | Meaning |
|---|---|---|
| `SPC_TSMODE_DISABLE` | 0 | Timestamps off |
| `SPC_TSMODE_STANDARD` | 0x2 | Counter reset only by explicit reset command |
| `SPC_TSMODE_STARTRESET` | 0x4 | Counter reset on every card start — all stamps relative to start |
| `SPC_TS_RESET` | 0x1 | Reset counters now and store the local PC time/date |
| `SPC_TS_RESET_WAITREFCLK` | 0x8 | Reset, then wait for a reference clock edge before storing PC time |
| `SPC_TSCNT_INTERNAL` | 0x100 | Counter runs at full width on the sampling clock |
| `SPC_TSCNT_REFCLOCKPOS` | 0x200 | Split counter: upper part on external reference rising edge, lower on sampling clock |
| `SPC_TSCNT_REFCLOCKNEG` | 0x400 | Same, falling edge |
| `SPC_TSXIOACQ_ENABLE` | 0x1000 | Also latch X0–X3 states with each timestamp |
| `SPC_TSFEAT_TRGSRC` | 0x80000 | Also store which trigger source fired |

| Register | Purpose |
|---|---|
| `SPC_TIMESTAMP_STARTTIME` | Reset time (UTC): hours in bits 23–16, minutes 15–8, seconds 7–0 |
| `SPC_TIMESTAMP_STARTDATE` | Reset date: year in bits 31–16, month 15–8, day 7–0 |
| `SPC_TIMESTAMP_TIMEOUT` | Timeout (10–10000 ms) waiting for a reference clock edge; 0 disables |

```python
spcm_dwSetParam_i32(hCard, SPC_TIMESTAMP_CMD,
                    SPC_TSMODE_STARTRESET | SPC_TSCNT_INTERNAL)
```

Reference-clock mode (e.g. a GPS 1 PPS or IRIG-B seconds signal) lets you tie
the acquisition to absolute time:

```python
spcm_dwSetParam_i32(hCard, SPC_TIMESTAMP_CMD, SPC_TSMODE_STANDARD | SPC_TSCNT_REFCLOCKPOS)
spcm_dwSetParam_i32(hCard, SPC_TIMESTAMP_TIMEOUT, 1500)
if spcm_dwSetParam_i32(hCard, SPC_TIMESTAMP_CMD, SPC_TS_RESET_WAITREFCLK) == ERR_TIMESTAMP_SYNC:
    print("Synchronization with external clock signal failed")
```

> `SPC_TS_RESET` / `SPC_TS_RESET_WAITREFCLK` only affect the counters once
> the clock generation is already active and the timestamp mode has actually
> reached the hardware — i.e. after an acquisition with timestamps enabled,
> or after an explicit `M2CMD_CARD_WRITESETUP`.

### Data format

> *manual pp. 141–142*

Each timestamp is **128 bit (16 bytes)**, mapped to two consecutive 64-bit
values.

| Mode | Bytes 1–8 | Bytes 9–16 |
|---|---|---|
| Standard / StartReset | 64-bit sample counter | zeros, or the extra data word |
| Refclock | 40-bit sample counter + 24-bit reference-edge (seconds) counter | zeros, or the extra data word |

With `SPC_TSXIOACQ_ENABLE` the upper 64 bits carry the X3…X0 input states
(bits 15–12 of the extra word); with `SPC_TSFEAT_TRGSRC` they carry a trigger
source bitmask (`SPC_TRGSRC_MASK_CH0` = 0x1, `_CH1` = 0x2,
`_EXT0` = 0x100, `_FORCE` = 0x400, `_X0`…`_X3` = 0x10000000…0x80000000).
Both flags can be combined. The trigger-source field is what tells you, in an
OR-combined setup, *which* source actually produced a given event.

The latched values represent the moment the trigger is detected internally,
which is delayed relative to the physical event by a fixed amount — constant
for a given setup, so it can be ignored or calibrated out once.

### Reading timestamps out

> *manual pp. 138–141*

Same handshake as sample data, but with its own registers:

| Register | Purpose |
|---|---|
| `SPC_TS_AVAIL_USER_LEN` | Bytes of new timestamp data available |
| `SPC_TS_AVAIL_USER_POS` | Byte offset where they start |
| `SPC_TS_AVAIL_CARD_LEN` | Bytes handed back to the driver |

Buffer definition uses `SPCM_BUF_TIMESTAMP`; direction is always
card→PC and there is no board offset. On M5i the buffer length must again be
a multiple of 64 bytes, and the notify size a minimum of 2 KiByte.

**DMA vs polling.** DMA mode uses `M2CMD_EXTRA_STARTDMA` /
`M2CMD_EXTRA_WAITDMA` and delivers data in notify-size chunks. Polling mode
uses `M2CMD_EXTRA_POLL` instead, has no wait step, and makes data available
in as little as 4 bytes — much more convenient when timestamps arrive slowly
and you don't want to wait for a full 2 KiByte block. The two cannot be
mixed.

```python
spcm_dwDefTransfer_i64(hCard, SPCM_BUF_TIMESTAMP, SPCM_DIR_CARDTOPC,
                       lNotifySizeTS, pvBufferTS, uint64(0), qwBufferSizeTS)
spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_EXTRA_POLL)
...
spcm_dwGetParam_i32(hCard, SPC_TS_AVAIL_USER_LEN, byref(lAvailUserTS))
if lAvailUserTS.value >= 16:                      # 16 bytes per stamp on M5i
    spcm_dwGetParam_i32(hCard, SPC_TS_AVAIL_USER_POS, byref(lPCPosTS))
    # clamp against the buffer end to avoid reading across the wrap
    if (lPCPosTS.value + lAvailUserTS.value) >= qwBufferSizeTS.value:
        lAvailUserTS.value = qwBufferSizeTS.value - lPCPosTS.value
    # ... read stamps ...
    spcm_dwSetParam_i32(hCard, SPC_TS_AVAIL_CARD_LEN, lAvailUserTS.value)
```

---

## 14. Block Average (firmware option)

> *manual pp. 160–165; specification p. 20*

Hardware accumulation of repetitive triggered segments, entirely inside the
FPGA — no CPU load, and a large reduction in transferred data. Requires
`SPCM_FEAT_EXTFW_SEGAVERAGE` in `SPC_PCIEXTFEATURES`.

Operation resembles Multiple Recording: each trigger acquires one RAW
segment, and a programmable number of consecutive segments are summed
sample-by-sample. Only the accumulated result is written to memory, as
**32-bit signed integers**. This requires a genuinely repetitive signal and a
stable trigger condition.

| Register | Purpose |
|---|---|
| `SPC_CARDMODE` = `SPC_REC_STD_AVERAGE` (0x20000) | Standard block averaging |
| `SPC_CARDMODE` = `SPC_REC_FIFO_AVERAGE` (0x200000) | Streaming block averaging |
| `SPC_SEGMENTSIZE` | Segment length (RAW samples in, 32-bit samples out) |
| `SPC_POSTTRIGGER` | Posttrigger per segment; pretrigger = segment − posttrigger |
| `SPC_AVERAGES` | Number of segments averaged together: 2 … 1024 |
| `SPC_MEMSIZE` | Standard mode: total samples per channel (multiple of segment size) |
| `SPC_LOOPS` | FIFO mode: number of averaged segments; 0 = infinite |
| `SPC_AVRGMODE_CH0` / `_CH1` | Averaging mode per channel |
| `SPC_AVAILAVRGMODES` | Supported averaging modes |

Averaging modes:

| Mode | Value | Description |
|---|---|---|
| `AVRGMODE_NORMAL` | 0x0 | Unconditional: every RAW sample is accumulated |
| `AVRGMODE_TDA_HIGH` | 0x1 | Threshold Defined Averaging: only samples **above** a threshold accumulate; others are replaced by a programmed replacement value (threshold must be more positive than the replacement) |
| `AVRGMODE_TDA_LOW` | 0x2 | Only samples **below** the threshold accumulate (threshold must be more negative than the replacement) |

TDA exists for applications with a rare low-level signal riding on a noisy
baseline — time-of-flight mass spectrometry is the canonical case: the
baseline is discarded rather than accumulated, so it does not grow with the
number of averages.

**Limits:** minimum waveform 64 samples, step 32; maximum 1 MiSample (1
channel) or 512 KiSamples (2 channels); averages 2…1024. Re-arm between
waveforms is 176 samples + pretrigger (2 channels). Because each result
sample is 32 bit, the effective memory in averaged samples is half the RAW
sample capacity.

---

## 15. Star-Hub synchronization

> *manual pp. 15, 30, 152–156*

The Star-Hub module synchronizes up to 8 M5i cards with minimal clock skew.
It mounts on the back of one card (the "carrier"), which becomes the clock
master and occupies a third slot width. Every card connects to the hub by an
identical-length cable, which is what keeps the skew small; the master card
must use its dedicated (leftmost) connector, while slave cable order is
detected automatically by the driver.

The Star-Hub is addressed as an additional device:

```python
hSync = spcm_hOpen(create_string_buffer(b'sync0'))
if not hSync:
    ...
spcm_dwSetParam_i32(hSync, SPC_SYNC_ENABLEMASK, (1 << nCardCount) - 1)
spcm_dwSetParam_i32(hSync, SPC_SYNC_CLKMASK,    (1 << lCarrierIdx))
```

Find the carrier by testing `SPC_PCIFEATURES` for the Star-Hub flag — on M5i
this is `SPCM_FEAT_STARHUB8` (0x40).

Cards are then started through the sync handle rather than individually:

```python
spcm_dwSetParam_i32(hSync, SPC_M2CMD, M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER)
```

DMA transfers are still defined and started per card. All trigger modes
available on the master remain available; the Star-Hub OR-combines the
trigger events, and each card's own trigger delay is applied afterwards, so
per-card delays still work.

Inter-card skew is adjustable up to 200 ps on 10 GS/s models.

---

## 16. Putting it together: a complete acquisition

> *manual pp. 68–95 (the full sequence), 89 (Standard Single example), 90 (FIFO example)*

The examples in §17 are each written to demonstrate one feature. In practice
almost every measurement script follows the same eight-phase skeleton, and it
is worth internalizing the order — most setup errors are ordering errors.

### The canonical order

1. **Open** the card and verify it is what you expect (`SPC_FNCTYPE`,
   `SPC_PCITYP`, serial number).
2. **Reset** so you inherit nothing from the previous user.
3. **Channel enable** — this constrains everything downstream.
4. **Card mode**, then memory/segment/pretrigger/posttrigger sizes.
5. **Clock** mode and sample rate; read the rate back.
6. **Input ranges** and offsets per channel.
7. **Trigger** — clear the OR mask first, then configure sources, levels,
   delay, holdoff.
8. **Buffer** definition, then start, wait, read, convert, close.

### Worked example

A complete, self-contained Standard Single acquisition on both channels at
5 GS/s with a rising-edge channel trigger, producing calibrated numpy arrays.
This is the template to copy for a new measurement.

```python
import sys
import numpy as np
from pyspcm import *
from spcm_tools import *

szErr = create_string_buffer(ERRORTEXTLEN)

# ---- 1. open and identify -------------------------------------------------
hCard = spcm_hOpen(create_string_buffer(b'/dev/spcm0'))
if not hCard:
    sys.exit("no card found")

lFncType = int32(0)
spcm_dwGetParam_i32(hCard, SPC_FNCTYPE, byref(lFncType))
if lFncType.value != SPCM_TYPE_AI:
    spcm_vClose(hCard); sys.exit("not an A/D card")

lSerial = int32(0)
spcm_dwGetParam_i32(hCard, SPC_PCISERIALNO, byref(lSerial))

acName = pvAllocMemPageAligned(20)
spcm_dwGetParam_ptr(hCard, SPC_PCITYP, acName, 20)
print(f"Using {acName.value.decode()} sn {lSerial.value:05d}")

# ---- 2. reset -------------------------------------------------------------
spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_CARD_RESET)

# ---- 3. channels ----------------------------------------------------------
spcm_dwSetParam_i32(hCard, SPC_CHENABLE, CHANNEL0 | CHANNEL1)
lChCount = int32(0)
spcm_dwGetParam_i32(hCard, SPC_CHCOUNT, byref(lChCount))

# ---- 4. mode and memory ---------------------------------------------------
lMemsize = 65536                                    # samples per channel, step 32
spcm_dwSetParam_i32(hCard, SPC_CARDMODE,    SPC_REC_STD_SINGLE)
spcm_dwSetParam_i64(hCard, SPC_MEMSIZE,     lMemsize)
spcm_dwSetParam_i64(hCard, SPC_POSTTRIGGER, 49152)  # -> 16384 samples pretrigger

# ---- 5. clock -------------------------------------------------------------
spcm_dwSetParam_i32(hCard, SPC_CLOCKMODE,  SPC_CM_INTPLL)
spcm_dwSetParam_i64(hCard, SPC_SAMPLERATE, int64(5000000000))
spcm_dwSetParam_i32(hCard, SPC_CLOCKOUT,   0)
llRate = int64(0)
spcm_dwGetParam_i64(hCard, SPC_SAMPLERATE, byref(llRate))
print(f"Actual sample rate: {llRate.value/1e9:.4f} GS/s")

# ---- 6. input ranges ------------------------------------------------------
aRange_mV = [1000, 1000]
for ch, rng in enumerate(aRange_mV[:lChCount.value]):
    spcm_dwSetParam_i32(hCard, SPC_AMP0  + ch * (SPC_AMP1  - SPC_AMP0),  rng)
    spcm_dwSetParam_i32(hCard, SPC_OFFS0 + ch * (SPC_OFFS1 - SPC_OFFS0), 0)

# ---- 7. trigger: rising edge on Ch0 at +100 mV ----------------------------
lMaxADC = int32(0)
spcm_dwGetParam_i32(hCard, SPC_MIINST_MAXADCVALUE, byref(lMaxADC))
lLevel = int(round(100.0 / (aRange_mV[0] / float(lMaxADC.value))))   # mV -> ADC code

spcm_dwSetParam_i32(hCard, SPC_TRIG_ORMASK,      SPC_TMASK_NONE)     # kill software trigger
spcm_dwSetParam_i32(hCard, SPC_TRIG_ANDMASK,     0)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH_ORMASK0,  SPC_TMASK0_CH0)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH_ANDMASK0, 0)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH0_MODE,    SPC_TM_POS)
spcm_dwSetParam_i32(hCard, SPC_TRIG_CH0_LEVEL0,  lLevel)
spcm_dwSetParam_i64(hCard, SPC_TRIG_DELAY,       0)

# ---- 8. buffer, start, read ----------------------------------------------
lBytesPerSample = int32(0)
spcm_dwGetParam_i32(hCard, SPC_MIINST_BYTESPERSAMPLE, byref(lBytesPerSample))
qwBufferSize = lMemsize * lBytesPerSample.value * lChCount.value

# page-aligned numpy buffer (see §17, CUDA example)
raw_unaligned = np.zeros((4095 + qwBufferSize) // 2, dtype=np.int16)
offset = (4096 - (raw_unaligned.__array_interface__['data'][0] & 0xFFF)) // 2
raw = raw_unaligned[offset : offset + qwBufferSize // 2]
pvBuffer = raw.ctypes.data_as(c_void_p)

spcm_dwDefTransfer_i64(hCard, SPCM_BUF_DATA, SPCM_DIR_CARDTOPC,
                       int32(0), pvBuffer, uint64(0), uint64(qwBufferSize))

spcm_dwSetParam_i32(hCard, SPC_TIMEOUT, 10000)
dwError = spcm_dwSetParam_i32(hCard, SPC_M2CMD,
                              M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER |
                              M2CMD_DATA_STARTDMA)
if dwError == ERR_OK:
    dwError = spcm_dwSetParam_i32(hCard, SPC_M2CMD,
                                  M2CMD_CARD_WAITREADY | M2CMD_DATA_WAITDMA)

if dwError == ERR_TIMEOUT:
    print("no trigger within timeout")
    spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_CARD_STOP)
elif dwError != ERR_OK:
    spcm_dwGetErrorInfo_i32(hCard, None, None, szErr)
    print(szErr.value.decode())
else:
    # ---- deinterleave and scale ------------------------------------------
    data = raw.reshape(-1, lChCount.value)                 # rows = samples
    volts = [data[:, ch].astype(np.float64) *
             (aRange_mV[ch] / 1000.0) / lMaxADC.value
             for ch in range(lChCount.value)]
    t = (np.arange(lMemsize) - (lMemsize - 49152)) / llRate.value   # t=0 at trigger
    print(f"Ch0: {volts[0].min()*1e3:.2f} mV .. {volts[0].max()*1e3:.2f} mV")

spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_DATA_STOPDMA)
spcm_vClose(hCard)
```

Note the time axis construction: sample index 0 is the *oldest* pretrigger
sample, so the trigger event sits at index `memsize − posttrigger`. Getting
this offset wrong is a silent error that shifts every measured delay.

### Efficient data handling with numpy

The examples cast the DMA buffer to a ctypes pointer and index it
sample-by-sample. That is fine for illustration and useless in practice — at
5 GS/s a 64 kSample capture is nothing, but a streaming run produces
gigabytes per second and a Python loop manages a few million samples per
second at best.

Wrap the buffer once and work on arrays:

```python
# from a ctypes buffer created by pvAllocMemPageAligned
samples = np.frombuffer(pvBuffer, dtype=np.int16, count=nsamples_total)

# deinterleave: rows = time, columns = channel
data = samples.reshape(-1, nchannels)
ch0, ch1 = data[:, 0], data[:, 1]

# scale to volts (do this once, on the slice you actually need)
volts = ch0.astype(np.float64) * (range_mV / 1000.0) / maxADC
```

For the FIFO loop, build the view over just the notify-size window rather
than copying:

```python
# inside the streaming loop, after reading lPCPos and lAvailUser
block = np.frombuffer(pvBuffer, dtype=np.int16,
                      count=lNotifySize.value // 2,
                      offset=lPCPos.value)
# process `block` in place; do NOT keep a reference to it after
# writing SPC_DATA_AVAIL_CARD_LEN — the driver may overwrite that region
```

That last point matters: the region described by `SPC_DATA_AVAIL_USER_POS`
and `..._USER_LEN` is only yours until you hand it back with
`SPC_DATA_AVAIL_CARD_LEN`. If you need the data beyond that, copy it
(`block.copy()`) or write it to disk before releasing.

For Multiple Recording, reshape into segments once per block:

```python
# one notify-size block containing whole segments, 2 channels
seg = block.reshape(-1, segment_size, nchannels)   # (nsegments, samples, channels)
peaks = seg[:, :, 0].max(axis=1)                   # per-segment peak on Ch0
```

Choosing the notify size as an integer multiple of
`segment_size × nchannels × bytes_per_sample` makes this reshape exact and
avoids tracking partial segments across block boundaries — well worth the
arithmetic up front.

### Streaming to disk

The usual reason for FIFO mode is to record longer than memory allows. The
minimal pattern:

```python
with open('capture.bin', 'wb') as f:
    while running:
        if spcm_dwSetParam_i32(hCard, SPC_M2CMD, M2CMD_DATA_WAITDMA) != ERR_OK:
            break
        spcm_dwGetParam_i32(hCard, SPC_M2STATUS,            byref(lStatus))
        spcm_dwGetParam_i32(hCard, SPC_DATA_AVAIL_USER_LEN, byref(lAvailUser))
        spcm_dwGetParam_i32(hCard, SPC_DATA_AVAIL_USER_POS, byref(lPCPos))

        if lStatus.value & M2STAT_DATA_OVERRUN:
            print("FIFO overrun — host too slow")
            break

        if lAvailUser.value >= lNotifySize.value:
            f.write(np.frombuffer(pvBuffer, dtype=np.int16,
                                  count=lNotifySize.value // 2,
                                  offset=lPCPos.value).tobytes())
            spcm_dwSetParam_i32(hCard, SPC_DATA_AVAIL_CARD_LEN, lNotifySize)
```

Practical guidance for sustained streaming:

- **Check `M2STAT_DATA_OVERRUN` every iteration.** Silent data loss is worse
  than a stopped run.
- **Watch `SPC_FILLSIZEPROMILLE`.** A fill level that climbs steadily means
  the host is not keeping up and an overrun is coming; a level that hovers
  low means you have headroom. Log it.
- **Larger software buffer, larger notify size** both reduce per-block
  overhead. A notify size of 128 KiByte–1 MiByte is a reasonable starting
  point for high-rate streaming; small notify sizes maximize latency
  responsiveness but cost CPU.
- **Consider the data-reducing modes before throwing bandwidth at the
  problem**: 8-bit storage halves the rate, 12-bit packing saves 25 %,
  Multiple Recording discards dead time entirely, and Block Average reduces
  many segments to one. Any of these may turn an impossible stream into a
  comfortable one.
- **Raw disk throughput is usually the limit**, not the card. Write raw
  binary and convert offline; do not format text in the loop.

### Recording the metadata

A raw sample buffer is meaningless without the settings that produced it.
Capture at minimum: card type and serial, driver and firmware versions,
actual (read-back) sample rate, oversampling factor, active channel mask,
per-channel input range and offset, memsize/segment/pretrigger/posttrigger,
card mode and any data-conversion mode, complete trigger configuration
(masks, modes, levels, delay, holdoff), `SPC_MIINST_MAXADCVALUE`,
`SPC_MIINST_BYTESPERSAMPLE`, and the last calibration date
(`SPC_CALIBDATE`, `SPC_CALIBDATEONBOARD`). All of these are readable
registers — write a single function that dumps them to JSON alongside the
binary and use it in every script.

### Diagnostics: when nothing triggers

Work down this list before suspecting the hardware.

| Symptom | Likely cause |
|---|---|
| Acquisition completes instantly, data looks like free-running noise | `SPC_TRIG_ORMASK` still contains `SPC_TMASK_SOFTWARE` (the default) |
| Never triggers, times out | Trigger source not in any mask; or level out of range; or wrong polarity |
| Never triggers after enabling 50 Ω on Ext0 | Signal halved by the 1:2 divider — halve the programmed level |
| Never triggers on a channel trigger | Level is in ADC codes, not mV, and is relative to the current input range |
| `ERR_PRETRIGGERLEN` | Pretrigger exceeds the limit for the active mode and channel count (§7) |
| `ERR_VALUE` on a size register | Value is off the 32-sample (or 64/256) grid |
| `ERR_CLOCKNOTLOCKED` | External reference missing, unstable, or below the required amplitude at card start |
| `ERR_SEQUENCE` | Command not legal in the current state (e.g. changing setup while running) |
| Triggers far too often on a noisy signal | Use a re-arm mode, or add trigger holdoff |
| Data saturates / clips | Input range too small, or offset pushing the signal out of range |
| `ERR_FIFOHWOVERRUN` while streaming | Host cannot sustain the rate — reduce it, or use a data-reduction mode |
| `ERR_TIMEOUT` from a wait | Not an error: the timeout simply expired. Retry, force a trigger, or stop |

Two hardware aids for finding the card physically and checking its health:

- **Card identification LED** — the card has a status LED that can be turned
  on by software to identify which slot holds which card in a multi-card
  system (manual p. 172).
- **Temperature and fan sensors** — base card and front-end module
  temperatures and fan speeds are readable registers, with documented limits
  for the 33xx series (manual p. 168). Worth logging in long unattended runs;
  `ERR_TEMPERATURE` and `ERR_FAN` are real error codes.

The **Card Control Center** shipped with the driver (manual pp. 39–47) is
also worth knowing: it performs card identification, memory tests, transfer
speed tests, calibration, firmware upgrades, and — most usefully — enables
detailed **debug logging** for support cases. If a problem resists the table
above, reproduce it with debug logging on.

---

## 17. Practical notes and common pitfalls

**Setup order matters.** Channel enable first (it constrains sample rate,
memory, and pretrigger limits), then card mode, then memory/segment sizes,
then clock, then input ranges, then trigger. Setting the trigger before
clearing the default software trigger in the OR mask is the classic mistake.

**Always read back what you set.** Sample rate in particular: you get the
nearest achievable divided clock, not what you asked for. Store the read-back
value with your data.

**Never hard-code the full-scale code.** Read `SPC_MIINST_MAXADCVALUE`.

**Step sizes are enforced.** Memsize, pretrigger, posttrigger, and segment
size all move in steps of 32 samples in native mode (64 in 8-bit mode, 256
for segments in packed mode). Off-grid values are rejected outright.

**Notify size and transfer length are stricter on M5i** than on other
Spectrum series: notify size a multiple of 4 KiByte or one of {64, 128, 256,
512, 1 Ki, 2 Ki} bytes, transfer length a multiple of 64 bytes.

**Page-align every DMA buffer.** Use `pvAllocMemPageAligned`, the driver's
continuous buffer, or an explicitly aligned numpy slice.

**Don't loop over samples in Python.** The examples do this for clarity, but
at 10 GS/s a per-sample Python loop is hopeless. Wrap the buffer in numpy
(`np.frombuffer(..., dtype=np.int16)`) and work on arrays.

**Warm up and calibrate.** Specifications hold after 30 minutes of running
acquisition and an on-board self-calibration with inputs left open.

**Watch the trigger termination.** Switching Ext0 to 50 Ω halves the
observed level of a 50 Ω source; the trigger level must be adjusted to match.

**Respect the input protection limits.** The ±200 mV range tolerates only
±2.0 V peak. Confirm the source amplitude before selecting the most
sensitive range.

**The card needs its auxiliary power cable.** It cannot run from the slot.

**Provide adequate cooling.** The card draws ≈39 W and has rear fans;
operating range is 0–50 °C.

---

## 18. Guide to the bundled Python examples

> *manual pp. 62–63*

The example set uses the **low-level `pyspcm` interface** throughout. They
are a good starting point but were written to be generic across the whole
Spectrum range, so several branches in them are irrelevant to this card and a
few would need adjusting. Notes below flag both.

### Support files

**`pyspcm.py`** — the `ctypes` binding layer. Detects OS and word size, loads
`libspcm_linux.so` (Linux, cdecl) or `spcm_win64.dll`/`spcm_win32.dll`
(Windows, stdcall), and binds every driver entry point with argument and
return types. Also defines the type aliases used everywhere else (`int32`,
`int64`, `ptr8`, `ptr16`, `ptr64`, `uint64`, …) and the buffer/direction
constants (`SPCM_BUF_DATA`, `SPCM_DIR_CARDTOPC`, `SPCM_DIR_CARDTOGPU`, …).
Two wrappers, `spcm_dwSetParam_i64` and `spcm_dwDefTransfer_i64`, accept
either plain Python integers or ctypes objects, which is why the examples can
pass both interchangeably. If the driver is missing it raises a clear
exception; if a symbol is absent it reports that driver **V7.0 or newer** is
required.

**`py_header/regs.py`** — every register and constant used in this document,
with names identical to those in the manual. Import it rather than using
numeric values; it is regenerated with each driver release and should never
be edited.

**`py_header/spcerr.py`** — all driver error codes (`ERR_OK`, `ERR_TIMEOUT`,
`ERR_CLOCKNOTLOCKED`, `ERR_FIFOHWOVERRUN`, `ERR_PRETRIGGERLEN`, …). Useful
for turning a numeric return into something readable in logs.

**`py_header/__init__.py`** — package marker.

**`spcm_tools.py`** — currently just `pvAllocMemPageAligned(qwBytes)`, which
over-allocates by 4095 bytes and returns a `c_char` view starting at the next
4096-byte boundary. Every DMA buffer in the examples comes from here (or from
`spcm_dwGetContBuf_i64`).

### Acquisition examples — directly applicable

**`simple_rec_single.py`** — the canonical Standard Single acquisition and
the best first read. It opens `/dev/spcm0`, reads the card name via
`spcm_dwGetParam_ptr(SPC_PCITYP, ...)`, verifies `SPC_FNCTYPE == SPCM_TYPE_AI`,
derives the channel count from `SPC_MIINST_MODULES × SPC_MIINST_CHPERMODULE`,
enables all channels, sets 16 kSample memsize with 8 kSample posttrigger,
software trigger, internal PLL, ±1 V on every channel, allocates a DMA buffer
(preferring the driver's continuous buffer), starts card and DMA together,
waits with `M2CMD_CARD_WAITREADY | M2CMD_DATA_WAITDMA`, then computes min/max
per channel.

Two things in it do not apply here. The `TYP_M2ISERIES` branch that sets
100 kHz instead of 20 MHz is for the older M2i family — this card takes the
`MEGA(20)` path. More importantly, the `alSamplePos` construction handles M2i
cards, where channels are multiplexed across modules; from M3i onward,
including M5i, channels are laid out linearly, so the else-branch
(`alSamplePos[i] = i`) is the one that runs. The per-sample Python loop at the
end is illustrative only.

Note also that 20 MS/s is far below this card's minimum native clock — it
works, but only because the driver silently engages oversampling (§6). For a
realistic test, set something in the GS/s range.

**`simple_rec_multi.py`** — Multiple Recording in Standard mode: 1 kSample
total in 256-sample segments, posttrigger 128, external trigger on Ext0
(`SPC_TM_POS`, level 1500 mV). Shows the segment-indexed read-out loop
(`lDataPos = lSegment * lSegmentSize + i * lSetChannels + alSamplePos[ch]`).
The same M2i caveats apply.

**`simple_rec_fifo.py`** — the FIFO Single streaming template: 1 MiByte
software buffer, 16 KiByte notify size, `SPC_PRETRIGGER` 1024, software
trigger, and the standard
`WAITDMA → read USER_LEN/USER_POS → process → write CARD_LEN` loop, stopping
after 8 MiByte with `M2CMD_CARD_STOP | M2CMD_DATA_STOPDMA`. The notify size
of 16 KiByte is a legal multiple of 4 KiByte for M5i. This is the loop to
copy when building any streaming acquisition; replace the min/max computation
with a numpy view over the notify-size window.

**`simple_rec_fifo_multi_ts_poll.py`** — the most instructive example for
this card, combining FIFO Multiple Recording with timestamps read in polling
mode. Worth reading closely:

- It gates on `SPCM_FEAT_MULTI` and `SPCM_FEAT_TIMESTAMP` before proceeding.
- It sets `lBytesPerTS = 16` for M4i/M4x/M2p/**M5i** (8 for the older M2i/M3i)
  — the 128-bit stamp discussed in §13.
- It reads the sample rate back from the driver before using it to convert
  timestamps to seconds.
- Two independent DMA buffers are defined: `SPCM_BUF_DATA` (4 MiByte, 8 KiByte
  notify) and `SPCM_BUF_TIMESTAMP` (1 MiByte, 4 KiByte notify), and the
  timestamp path is switched to polling with `M2CMD_EXTRA_POLL`.
- The timestamp read-out clamps `lAvailUserTS` against the end of the buffer
  before indexing — the buffer-wrap handling you need in any real
  implementation.
- Segment boundaries are tracked by counting samples modulo the segment size,
  giving per-segment min/max.

Its trigger setup (`SPC_TRIG_EXT0_MODE = SPC_TM_POS`, `SPC_TRIG_TERM = 0`,
`SPC_TRIG_EXT0_LEVEL0 = 1500`) is a good, complete Ext0 example. Note it also
writes `SPC_TRIG_EXT0_ACDC`, which is an AC/DC coupling selection not
applicable to this card's fixed-DC trigger input.

**`simple_rec_single-cudafft.py`** — Standard Single acquisition followed by
an FFT computed on a CUDA GPU with `cupy`. Two parts are valuable regardless
of whether you use CUDA:

1. **The numpy-native page-aligned buffer.** Instead of
   `pvAllocMemPageAligned`, it allocates an over-sized `np.int16` array and
   slices to the next 4096-byte boundary, then passes
   `array.ctypes.data_as(c_void_p)` to `spcm_dwDefTransfer_i64`. The acquired
   data then lives in a real numpy array — no casting, no per-sample loops.
   This is the pattern to adopt for any serious analysis code.
2. **Correct voltage scaling**, reading `SPC_MIINST_MAXADCVALUE` and applying
   `(range_mV / 1000) / maxADC` as the conversion factor.

It also uses `SPC_MIINST_MAXADCLOCK` to run at the card's maximum rate, which
is more appropriate for this card than the fixed 20 MS/s of the other
examples. On-GPU processing here uses the ordinary card→PC→GPU path; true
card→GPU RDMA requires the SCAPP option and `SPCM_DIR_CARDTOGPU`.

**`simple_sync_rec_fifo.py`** — two synchronized acquisition cards streaming
in FIFO mode, one Python thread per card. Shows the Star-Hub pattern: open
`/dev/spcm0` and `/dev/spcm1`, open the hub as `sync0`, set
`SPC_SYNC_ENABLEMASK` to cover all cards, locate the hub carrier via
`SPC_PCIFEATURES` and set `SPC_SYNC_CLKMASK` accordingly, define each card's
DMA buffer individually, then start everything through the sync handle. Each
thread runs its own `WAITDMA` loop against its own card handle.

One correction if you adapt it: the carrier-card test uses
`SPCM_FEAT_STARHUB5 | SPCM_FEAT_STARHUB16`, which are the M2i flags. For M5i
the correct flag is **`SPCM_FEAT_STARHUB8`** (0x40).

**`netbox_discovery.py`** — LXI discovery of remote Spectrum devices. Calls
`spcm_dwDiscovery` to collect VISA strings, `spcm_dwSendIDNRequest` to filter
for Spectrum devices, then opens each and reads `SPC_PCITYP`,
`SPC_PCISERIALNO`, `SPC_NETBOX_TYPE`, and `SPC_NETBOX_SERIALNO`. Only
relevant if the card is accessed remotely through the Remote Server option or
lives in a NETBOX; for a locally installed card, ignore it.

### Examples for other card types

These target arbitrary waveform generators / digital output cards and are not
applicable to this digitizer, but they are worth knowing about because they
demonstrate the *replay* half of the same API (`SPCM_DIR_PCTOCARD`,
`SPC_REP_STD_CONTINUOUS`, `SPC_REP_FIFO_SINGLE`, `SPC_ENABLEOUT0`,
`SPC_FILLSIZEPROMILLE`):

- **`simple_rep_single.py`** — continuous replay of a sine (or a walking-bit
  pattern for digital cards) from on-board memory.
- **`simple_rep_fifo.py`** — FIFO replay with pre-calculated waveform data,
  starting the card only once `SPC_FILLSIZEPROMILLE` reaches 1000.
- **`simple_rep_sequence.py`** — sequence replay mode.
- **`simple_sync_rep_single.py`** — two synchronized replay cards via
  Star-Hub.
- **`simple_sync_rep_rec.py`** — one replay card and one acquisition card
  synchronized through a Star-Hub; the closest thing in the set to a
  stimulus-and-response measurement. Note it imports `msvcrt` for keyboard
  handling, so it is **Windows-only as written**; the keyboard polling needs
  replacing on Linux.
- **`simple_rec_single_digital.py`** — Standard Single for digital I/O cards
  (`SPCM_TYPE_DIO` / `SPCM_TYPE_DI`); it will exit immediately on an analog
  card. Useful only as a reference for how digital cards pack multiple
  channels into each 16-bit word.

### Adapting the examples

A realistic starting point for this card, distilled from the above:

1. Take `simple_rec_single.py` or `simple_rec_fifo.py` as the skeleton.
2. Drop the `TYP_M2ISERIES` branches and the `alSamplePos` multiplexing logic
   — this card is linear.
3. Set a sample rate in the card's real range (e.g. `int64(5000000000)`) and
   read it back.
4. Replace `pvAllocMemPageAligned` with the aligned-numpy-array pattern from
   `simple_rec_single-cudafft.py`.
5. Replace all per-sample loops with numpy slicing, deinterleaving channels
   with `samples[0::2]` / `samples[1::2]`.
6. Scale to volts using `SPC_MIINST_MAXADCVALUE` and the active `SPC_AMPx`.
7. Set the trigger explicitly, clearing `SPC_TRIG_ORMASK` to
   `SPC_TMASK_NONE` first unless you genuinely want the software trigger.
8. Log card serial, firmware versions, actual sample rate, input ranges,
   trigger configuration, and calibration date alongside the data.

If you are writing new code rather than adapting these, the high-level
`spcm` package (§2) does steps 4–6 for you and raises exceptions instead of
requiring the error-code checks.

