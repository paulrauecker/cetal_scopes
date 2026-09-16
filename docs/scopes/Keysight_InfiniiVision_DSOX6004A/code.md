# Keysight InfiniiVision 6000 X-Series — Programming Reference for `cetal_scopes`

Source: *Programmer's Guide for InfiniiVision 6000 X-Series Oscilloscopes*, Keysight Technologies, Version 07.66.0000 (Oct 31, 2025), 1888 pages. This document distills the sections relevant to building a `cetal_scopes` backend for a 6000 X-Series scope (DSO-X 6002A/6004A, MSO-X 6002A/6004A — 1/4 GHz–6 GHz BW, up to 20 GSa/s interleaved).

Full guide: https://www.keysight.com/find/6000X-Series-manual

---

## 1. Connectivity & Setup

- **Interfaces**: USB (device port, USB 2.0 high-speed) and LAN — both always active. GPIB requires an interface card (e.g. Keysight 82350B).
- **LAN setup on the scope**: `[Utility] → I/O → Configure`, then configure DHCP/AutoIP or static IP/hostname via the front panel.
- **Host-side stack**: Keysight IO Libraries Suite (VISA/SICL) — provides the Connection Expert (auto-discovers USB/LAN/GPIB instruments) and Interactive IO (for manually sending test commands).
- **Raw socket access** (bypasses VISA):
  - `telnet <hostname> 5024` — command line **with** prompt.
  - `telnet <hostname> 5025` — command line **without** prompt (cleaner for scripted ASCII I/O). **Do not** use port 5024 for binary/IEEE-block transfers — the prompt characters corrupt binary data, producing spurious zero data points.
- **Browser Web Control**: connect via LAN, open the scope's welcome page, select "Browser Web Control" → "Remote Programming" for a SCPI console in-browser.
- VISA resource strings look like `USB0::0x0957::0x17A6::<serial>::0::INSTR` or `TCPIP0::<hostname>::inst0::INSTR`.

## 2. SCPI Syntax Conventions

- `<NL>` = line terminator (ASCII 10, linefeed). A leading colon or the line terminator resets the parser to the command tree root.
- `[ ]` optional term; `{ }` choose exactly one, `|` = "or"; `::=` = "defined as"; `< >` wraps a parameter placeholder; `...` = preceding element repeats; `n,..,p` = inclusive integer range; `d` = single ASCII digit.
- Number formats: **NR1** = integer; **NR3** = floating point exponential (e.g. `-1.0E-3`).
- **Definite-length block data** (IEEE 488.2 arbitrary block, used for all binary transfers — waveform data, screen images, setup blobs):
  `#<n><n digits giving byte count><raw bytes><NL>`
  e.g. for 1000 bytes: `#800001000<1000 bytes><NL>` — `8` = number of length digits that follow, `00001000` = byte count. PyVISA's `query_binary_values` / `write_binary_values` handle this format automatically.
- Command short forms: only the capitalized letters are required (e.g. `:AUToscale` ≡ `:AUT`).

## 3. Basic Program Structure

Every program follows: **Initialize → Capture → Analyze/Transfer**.

1. **Initialize**: clear the interface (`Device Clear`), set an I/O timeout, send `*CLS` and `*RST` (or restore a saved setup — §12).
2. **Configure**: set channel scale/offset/coupling, timebase, trigger, and `:ACQuire` parameters (or just send `:AUToscale` for unknown signals).
3. **Capture**: use `:DIGitize` (blocking) or `:RUN`/`:SINGle` + synchronization polling (§15).
4. **Transfer/measure**: read built-in `:MEASure` results and/or pull raw samples via `:WAVeform:DATA?`.

### Key command classes for automation
- **Sequential (blocking)** — finish before the next command starts: `:AUToscale`, `:DIGitize`, `:WMEMory<r>:SAVE`, `:WAVeform:DATA?`.
- **Overlapped** — run concurrently with following commands (e.g. save/recall, some analysis). Use `*OPC?` after an overlapped command to force a barrier, e.g.:
  `:RECall:SETup "setup.scp";*OPC?;:RECall:ARBitrary "arb_wfm.csv"`
  or poll `*ESR?` bit 0 (OPC) in the Standard Event Status Register.

## 4. Root-Level Acquisition Commands

| Command | Effect |
|---|---|
| `:AUToscale [<source>,..]` | Auto-configures vertical/timebase/trigger for the given (or all displayed) sources. Blocking. Turns off cursors, measurements, math, ref waveforms, zoomed timebase. |
| `:DIGitize [<source>,..]` | Specialized `:RUN` — acquires per `:ACQuire` settings, **stops when complete** (blocking/sequential). If no source given, acquires all displayed channels (or all channels if none displayed). Only runs when `:TIMebase:MODE` is `MAIN` or `WINDow` — errors ("Settings conflict") if `ROLL`/`XY`. Abort via a bus Device Clear. Max 5 repeated `<source>` args. |
| `:RUN` | Starts free-running (repetitive) acquisition — like front-panel Run. |
| `:SINGle` | Arms for **one** triggered acquisition — like front-panel Single. Non-blocking (returns immediately; poll for completion, §15). |
| `:STOP` | Halts acquisition — like front-panel Stop. |
| `:TER?` | Reads & **clears** the Trigger Event Register (1 = a trigger occurred since last read). Summarized in STB bit TRG. |
| `:AER?` | Arm Event Register — 1 once the trigger system is armed (essential for single-shot DUT synchronization, §15). |

`*RST` resets `:TIMebase:MODE` to `MAIN` (a prerequisite for `:DIGitize`/`:WAVeform` queries).

## 5. `:ACQuire` Subsystem (acquisition parameters)

| Command | Meaning |
|---|---|
| `:ACQuire:TYPE {NORMal\|AVERage\|HRESolution\|PEAK}` | Acquisition mode. **AVERage**: averages `:ACQuire:COUNt` (2–65536) triggers per time bucket — not available in segmented mode. **HRESolution** (smoothing): averages oversampled points into each display point to cut noise at slow sweeps — no count needed; `:ACQuire:COUNt 1` (deprecated) is equivalent — prefer `HRESolution` explicitly. **PEAK**: captures min/max per bucket to catch glitches at ≥500 µs/div — `:ACQuire:COUNt` has no meaning here. For AVERage/HRESolution, use `WORD` or `ASCii` waveform format to get the extra vertical bits. |
| `:ACQuire:COUNt <n>` | Averages per bucket (2–65536) in AVERage mode. |
| `:ACQuire:COMPlete <n>` | Minimum % of buckets that must be "full" before acquisition is complete. Only legal value is `100`. |
| `:ACQuire:MODE {RTIMe\|MRTime\|ETIMe\|SEGMented}` | RTIMe = real-time single-trigger capture with reconstruction filter at fast sweeps (need ≥2.5× oversampling vs. signal BW to avoid aliasing/jitter). MRTime = max update rate, sample rate drops to 2 GSa/s at fast sweeps (not usable with single/roll/segmented/mask/zone trigger). ETIMe = equivalent-time (random repetitive) sampling for repetitive signals at ≥20 ns/div, needs a stable trigger. SEGMented = segmented memory mode. |
| `:ACQuire:POINts?` | (Query only) actual hardware-acquired point count — not directly settable; use `:WAVeform:POINts` to control the *transferred* count. |
| `:ACQuire:SRATe? [MAXimum]` | Current (or max possible) sample rate — not directly settable. |
| `:ACQuire:SEGMented:COUNt <n>` (2–1000, memory-dependent), `:INDex <n>`, `:ANALyze` | Segmented-memory acquisition: set segment count, then acquire with `:DIGitize`/`:SINGle`/`:RUN`; step through segments post-acquisition with `:INDex`, read time-tags via `:WAVeform:SEGMented:TTAG?`. |
| `:ACQuire:RSIGnal {OFF\|OUT\|IN}` | 10 MHz reference clock BNC mode — `OUT` to sync multiple instruments' timebases, `IN` to slave to an external 10 MHz reference (−5 to +17 dBm; **do not exceed 20 dBm**). |

## 6. `:TIMebase` Subsystem (horizontal control)

| Command | Meaning |
|---|---|
| `:TIMebase:MODE {MAIN\|WINDow\|XY\|ROLL}` | MAIN = default after `*RST`; required for `:DIGitize`/`:WAVeform`. WINDow = zoomed/delayed view. XY = ch1 vs ch2 plot, disables RANGe/POSition/REFerence and all measurements. ROLL = continuous untriggered scroll; forces REFerence to RIGHt. |
| `:TIMebase:RANGe <s>` / `:TIMebase:SCALe <s>` | Full 10-division sweep width / per-division time. `RANGe = 10 × SCALe`. |
| `:TIMebase:POSition <s>` (alias `:DELay`) | Time from trigger event to the display reference point. |
| `:TIMebase:REFerence {LEFT\|CENTer\|RIGHt\|CUSTom}` (+ `:REFerence:LOCation 0.0–1.0` for CUSTom) | Where on screen the trigger point is anchored. |
| `:TIMebase:WINDow:POSition/RANGe/SCALe` | Same trio for the zoomed (delayed) window; window range/scale capped at half the main sweep's. |
| `:TIMebase:REFClock {0\|1}` | Enables/disables the rear-panel 10 MHz REF BNC as a clock input (`ON` ≙ `:ACQuire:RSIGnal IN`). |

`:TIMebase?` reporting example after `*RST`: `:TIM:MODE MAIN;REF CENT;MAIN:RANG +1.00E-03;POS +0.0E+00`

## 7. `:TRIGger` Subsystem (essentials — Edge trigger + general)

Default trigger type is **EDGE**. Sweep mode (`:TRIGger:SWEep {AUTO|NORMal}`, called "Mode" on the front panel) governs behavior with no valid trigger: `NORMal` freezes on the last good acquisition (needed for a genuine single-shot capture — don't use `AUTO`, which fabricates a trigger and displays unsynchronized data); `AUTO` is needed to see DC/untriggered signals.

Trigger types settable via `:TRIGger:MODE`: `EDGE | GLITch (pulse width) | PATTern | TV | DELay | EBURst (Nth-edge burst) | OR | RUNT | SHOLd (setup/hold) | TRANsition (rise/fall time) | SBUS{1|2} (serial decode)`. Only Edge is detailed below — the rest are individually simple, same-shaped command families (see §21).

**General commands:**
- `:TRIGger:FORCe` — force-captures an acquisition even if trigger conditions aren't met (front-panel "Force Trigger").
- `:TRIGger:HOLDoff <40ns–10s>` (+ `:HOLDoff:RANDom`, `:MINimum`, `:MAXimum`) — minimum time between triggers, prevents re-triggering on a waveform that crosses the level multiple times per period.
- `:TRIGger:HFReject {0|1}` — 50 kHz low-pass in trigger path to suppress AM/FM broadcast-band noise.
- `:TRIGger:JFRee {0|1}` — Jitter-Free Trigger hardware correction (good for fast digital edges; can worsen slow analog slew-rate signals).
- `:TRIGger:LEVel:ASETup` — auto-sets trigger level to 50% of each displayed analog channel's waveform (0 V if AC-coupled).

**Edge trigger (`:TRIGger[:EDGE]:*`)** — the workhorse for a laser-diagnostics single-shot capture:
- `:SOURce {CHANnel<n>\|EXTernal\|LINE\|WGEN\|WMOD\|DIGital<d> (MSO)}`
- `:LEVel <v>[,<source>]` — 0.75×full-scale from center screen (internal); ±external-range (EXT); ±8 V (digital).
- `:SLOPe {POSitive\|NEGative\|EITHer\|ALTernate}`
- `:COUPling {AC\|DC\|LFReject}` — AC = 10 Hz (analog)/3.5 Hz (EXT) high-pass, removes DC offset for stable triggering on offset signals; LFReject = 50 kHz high-pass; coupled with `:REJect {OFF\|LFReject\|HFReject}`.

`:TRIGger?` reporting example: `:TRIG:MODE EDGE;SWE AUTO;NREJ 0;HFR 0;HOLD +60.0E-09;:TRIG:EDGE:SOUR CHAN1;LEV +0.0E+00;SLOP POS;REJ OFF;COUP DC;:TRIG:ZONE:STAT 0`

## 8. `:CHANnel<n>` Subsystem (per-channel vertical control)

Key commands (n = 1..#analog channels): `:SCALe <v/div>`, `:RANGe <full-scale v>`, `:OFFSet <v>`, `:COUPling {AC|DC}`, `:IMPedance {ONEMeg|FIFTy}`, `:BWLimit {0|1}` + `:BANDwidth <20E6|200E6|1.5E9|3E9>` (available limits depend on model BW, impedance, and V/div — e.g. 50 Ω input on a 6 GHz model allows 20 MHz/200 MHz/1.5 GHz/3 GHz), `:PROBe <attenuation ratio>` (+ extensive `:PROBe:*` subtree for smart-probe ID/head-type/skew/external-sense-resistor/differential mode), `:DISPlay {0|1}`, `:INVert {0|1}`, `:LABel "<string>"` (≤32 chars), `:UNITs {VOLT|AMPere}`, `:VERNier {0|1}` (fine gain adjustment).

`:CHANnel<n>?` reporting example after `*RST`: `:CHAN1:RANG +40.0E+00;OFFS +0.0E+00;COUP DC;IMP ONEM;DISP 1;BWL 0;INV 0;LAB "1";UNIT VOLT;PROB +10E+00;PROB:SKEW +0.0E+00;STYP SING`

## 9. `:WAVeform` Subsystem — Data Transfer (the core of any acquisition driver)

**Two separate reads are required**: `:WAVeform:PREamble?` (scaling metadata) and `:WAVeform:DATA?` (raw samples). Each channel has its own preamble. Queries only work when the `:WAVeform:SOURce` channel is displayed/on.

### 9.1 Setup sequence
```
:WAVeform:SOURce CHANnel1
:WAVeform:FORMat {BYTE|WORD|ASCii}
:WAVeform:POINts <n>            ' or :WAVeform:POINts:MODE {NORMal|MAXimum|RAW}
:WAVeform:DATA?
```
`:WAVeform:POINts` accepts 100–10,000,000 (request limit, not necessarily the memory depth); actual count is bounded by the acquired record and current points mode. `MAXimum` mode + 10,000,000 requested points reads back everything currently displayed (up to 4,000,000 pts analog / 8,000,000 digital in some modes). Point decimation (e.g. requesting 500 of 1000 points) picks time buckets 0,2,4,...— i.e. it subsamples rather than interpolates, and can drop transients.

### 9.2 Data formats (`:WAVeform:FORMat`)
- **ASCii**: internally-converted real Y-axis floats, comma-separated text (up to ~13 bytes/point — slowest but self-scaled; ignores `:WAVeform:BYTeorder`/`:UNSigned`). Holes shown as `9.9e+37`.
- **WORD**: 16-bit binary, 2 bytes/point. If native resolution <16 bits (current max is 12-bit), data is left-shifted and LSBs zero-padded. Byte order controlled by `:WAVeform:BYTeorder {LSBFirst|MSBFirst}` (default MSBFirst). Holes = `0x0000`. Full digitizer range = 65536 steps.
- **BYTE**: 8-bit binary, 1 byte/point — fastest transfer; data >8 bits is right-shifted (truncated). Holes = `0x00`. `:BYTeorder` has no effect. Full digitizer range = 256 steps.
- `:WAVeform:UNSigned {0|1}` — selects signed vs. unsigned integer encoding (no effect on ASCii). **Required ON** for digital channels.
- Special byte/word sentinel values in BYTE/WORD data: `0x00`/`0x0000` = hole (not yet acquired), `0x01`/`0x0100` = clipped low, `0xFF`/`0xFF00` = clipped high.

### 9.3 Preamble (`:WAVeform:PREamble?`) — comma-separated fields, in order
```
<format>, <type>, <points>, <count>, <xincrement>, <xorigin>, <xreference>, <yincrement>, <yorigin>, <yreference>
```
**⚠ The manual gives two different numeric mappings for `format` and `type` in two different places, and they disagree:**

| Field | "Introduction to :WAVeform Commands" overview (p.1524) | `:WAVeform:PREamble` command page itself (p.1538, authoritative) |
|---|---|---|
| `format` | 0=BYTE, 1=WORD, **2**=ASCii | 0=BYTE, 1=WORD, **4**=ASCii |
| `type` | 0=NORMal, 1=PEAK, **3**=AVERage, **4**=HRESolution | 0=NORMal, 1=PEAK, **2**=AVERage, **3**=HRESolution |

The individual command's own description is normally the authoritative one, and it **matches** the numbering used in the manual's own PyVISA example code (§20 below: `wav_form_dict = {0:"BYTE", 1:"WORD", 4:"ASCii"}`, `acq_type_dict = {0:"NORMal", 1:"PEAK", 2:"AVERage", 3:"HRESolution"}`) — so trust the right-hand column. Still, **verify empirically against the real unit** (set each format/type explicitly, then read the preamble) before hard-coding either mapping into `cetal_scopes`, since a genuine inconsistency in the vendor doc is a reasonable basis for distrust of both.

- `count`: average count (else 1), set by `:ACQuire:COUNt`.
- Also individually queryable: `:WAVeform:XINCrement?`, `:XORigin?`, `:XREFerence?` (always 0), `:YINCrement?`, `:YORigin?`, `:YREFerence?`.
- From the preamble diagram: **`yreference` = half the full digitizer step range** (32768 for WORD, 128 for BYTE) — i.e. it's the code value corresponding to the channel's vertical offset, not a per-acquisition value.

### 9.4 Scaling formulas
```
voltage = (data_value - yreference) * yincrement + yorigin
time    = (point_index - xreference) * xincrement + xorigin      ' NORMal/AVERage/HRES
time    = (pair_index  - xreference) * xincrement * 2 + xorigin  ' PEAK mode (min/max pairs)
```

### 9.5 Per-acquisition-type record layout
- **NORMal**: last hit per time bucket, one value/point, left→right screen order; empty buckets = 0.
- **AVERage**: average of first `:ACQuire:COUNt` hits per bucket (partial averages if fewer hits available); max 1000 returned points unless `COUNt=1`.
- **PEAK**: two values per bucket (min, then max); `xincrement` from the preamble must be **doubled** to get true inter-pair time spacing.
- **Digital (MSO only)**: `:WAVeform:UNSigned` must be `ON`. POD1 = D0–D7 in bits 0–7, POD2 = D8–D15 in bits 0–7 of the byte; BUS sources always return 16-bit values regardless of channel count.

### 9.6 Source selection
`:WAVeform:SOURce {CHANnel<n> | FUNCtion<m>/MATH<m> | SBUS | POD{1|2} | BUS{1|2}}` (POD/BUS/DIGital only on MSO models). Serial decode bus sources (`SBUS<n>`) force ASCii-only format and ignore `:WAVeform:POINts` (returns decoded message count instead).

## 10. `:MEASure` Subsystem (automatic measurements — alternative to raw sample analysis)

For any measurement to succeed, the relevant portion of the waveform must be **on screen** (e.g. rise time needs the rising edge plus top/bottom of pulse visible). Failed measurements return the sentinel `+9.9E+37`. If multiple edges/pulses are visible, time measurements use the one closest to the trigger reference. Command form *displays* the result on-screen; query form *returns* it over the bus. `:MEASure:SOURce` sets the default channel(s) for measurements that don't take an explicit source.

**Most relevant commands for a `cetal_scopes` measurement path:**
- `:MEASure:SOURce <source1>[,<source2>]` — sets default measurement source(s); source2 only matters for `:DELay`/`:PHASe`.
- `:MEASure:FREQuency?`, `:PERiod?`, `:VPP?`, `:VAMPlitude?`, `:VMAX?`/`:VMIN?`, `:RISetime?`, `:FALLtime?`, `:PWIDth?`(+pulse), `:NWIDth?`(−pulse), `:DUTYcycle?`, `:DELay?`, `:AREa?` — each installs a screen measurement and returns a single NR3 value on query.
- `:MEASure:RESults?` — returns **all continuously-displayed** measurements as a comma-separated list (max 10 concurrent). With `:MEASure:STATistics ON`, each measurement returns `label, current, min, max, mean, stddev, count` (7 fields) instead of just the current value — useful for a multi-shot statistical readout without pulling raw waveform data every time.
- `:MEASure:STATistics {ON|CURRent|MINimum|MAXimum|MEAN|STDDev|COUNt}` + `:STATistics:RESet` — controls what `:RESults?` returns and lets you reset the running statistics between experiment runs.
- `:MEASure:CLEar` — removes all installed screen measurements.
- `:MEASure:DEFine THResholds,{STANdard | <mode>,<upper>,<middle>,<lower>}[,<source>]` — redefines the % (or absolute-volt) thresholds used by nearly every timing measurement (rise/fall/pulse-width/frequency/period/delay/etc.); default is 10/50/90%. **This matters directly for pulse-diagnostics work** — e.g. measuring rise time against a non-standard threshold pair. `ABSolute` mode depends on `:CHANnel<n>:RANGe`/`:SCALe`/`:PROBe`/`:UNITs` already being set correctly.
- `:MEASure:DELay:DEFine <src1_slope>,<src1_edge#>,<src1_threshold>,<src2_slope>,<src2_edge#>,<src2_threshold>` — the current (non-deprecated) way to specify which edges `:MEASure:DELay?` measures between. `<slope> ::= {RISing|FALLing}`, `<edge#> ::= 0–1000` (0 = auto-select edge closest to timebase reference — both edge numbers must be 0 together), `<threshold> ::= {LOWer|MIDDle|UPPer}`. Useful for inter-channel timing (e.g. trigger-to-signal delay across diagnostic channels).

`:MEASure?` reporting example: `:MEAS:SOUR CHAN1,CHAN2;STAT ON`

## 11. `:SYSTem` Subsystem (setup persistence, error queue, misc.)

- **`:SYSTem:ERRor?`** — pops the oldest entry from a 30-deep FIFO error queue (`<error_number>,"<error_string>"`); returns `+0,"No error"` once empty. Cleared on power-up, on `*CLS`, or once fully drained. **Poll this after every command during driver development** (§17).
- **`:SYSTem:SETup <block>` / `:SYSTem:SETup?`** — save/restore the *entire* instrument configuration as an IEEE-488.2 definite-length block. PyVISA: `setup_bytes = scope.query_binary_values(":SYSTem:SETup?", datatype='s', container=bytes)`, then `scope.write_binary_values(":SYSTem:SETup ", setup_bytes, datatype='B')` to restore. Useful for snapshotting/replaying an exact instrument configuration between experiment runs.
- `:SYSTem:PRESet` — restores factory default *setup* (channels/acquire/trigger/etc.) but leaves user *preferences* (e.g. remote-logging config) untouched; `*RST` is the stronger full reset.
- `:SYSTem:DATE <y,m,d>` / `:SYSTem:TIME <h,m,s>` — set/query the scope's clock.
- `:SYSTem:DSP "<string>"` — write up to 75 characters to the front-panel advisory line (does **not** set the MSG status bit — that's reserved for internal system messages).
- `:SYSTem:LOCK {0|1}` — lock out the front panel remotely.
- `:SYSTem:RLOGger:*` — remote-command logging to file/screen, useful for debugging a `cetal_scopes` session against the real instrument.

## 12. `:SAVE` / `:RECall` — File I/O to/from USB Storage

Short forms `:SAV`/`:REC` accepted. Any command taking a quoted `<file_name>` string can target a connected USB storage device with a `\usb\...` path, e.g.:
```
:SAVE:SETup:STARt "\usb\my_setup_file.scp"
:RECall:SETup:STARt "\usb\my_setup_file.scp"
```

**Waveform export (scope writes the file itself — an alternative to pulling via `:WAVeform:DATA?`):**
```
:SAVE:WAVeform:FORMat {ASCiixy | CSV | BINary}   ' must be set before saving, or save fails
:SAVE:WAVeform:LENGth <n>                        ' or :LENGth:MAX {0|1}
:SAVE:WAVeform[:STARt] "<file_name>"
```
- `ASCiixy` → one `.csv` per displayed analog channel.
- `CSV` → single `.csv` with all displayed channels.
- `BINary` → oscilloscope's native `.bin` format (see User's Guide for layout).

**Setup / screen-image / reference-waveform save-recall:**
- `:SAVE[:SETup[:STARt]] <file_spec>` / `:RECall:SETup[:STARt] <file_spec>` — `<file_spec>` is either an internal slot (`0–9`) or a filename (`.scp`).
- `:SAVE:IMAGe[:STARt] "<file>"` + `:SAVE:IMAGe:FORMat {BMP|BMP8bit|PNG|NONE}`, `:PALette {COLor|GRAYscale}`, `:INKSaver {0|1}` — save a screenshot directly to storage (complements the `:DISPlay:DATA?` pull-based approach in §13).
- `:SAVE:WMEMory[:STARt] "<file>"` (`.h5`) / `:RECall:WMEMory<r>[:STARt] <file|data>` — reference waveform memories; only ADD/SUBtract math results can be saved this way.
- `:SAVE:MASK`, `:RECall:MASK` — mask-test definitions (internal slot 0–3 or file).

## 13. `:DISPlay:DATA?` — Screen Image Pull (via bus, no USB storage needed)

```
:HARDcopy:INKSaver OFF                    ' default is ON, which inverts colors — turn off for a normal-looking capture
:DISPlay:DATA? BMP, COLor                 ' or BMP8bit / PNG; COLor or GRAYscale
```
Returns an IEEE-488.2 binary block (`ReadIEEEBlock`/`query_binary_values` in PyVISA) — write the bytes straight to a `.bmp`/`.png` file. This is the query-based equivalent of `:SAVE:IMAGe`, useful when you don't have USB storage attached and want the image over the same VISA session as your waveform data.

## 14. Digital Channels — `:DIGital<d>` and `:POD<n>` (MSO models only)

- `:DIGital<d>` (d = 0..15): `:DISPlay {0|1}`, `:LABel "<str>"` (≤10 chars), `:POSition <n>` (screen row), `:SIZE {SMALl|MEDium|LARGe}`, `:THReshold {CMOS|ECL|TTL|<custom V>}` (per-channel threshold).
- `:POD<n>` (n = 1|2, POD1=D0–D7, POD2=D8–D15): `:DISPlay {0|1}`, `:SIZE`, `:THReshold` — sets threshold for the whole 8-channel group at once (faster than per-channel if all digital lines share a logic family).

`:DIGital0?` example: `:DIG0:DISP 0;THR +1.40E+00;LAB 'D0';POS +0`. `:POD1?` example: `:POD1:DISP 0;THR +1.40E+00`.

## 15. Synchronizing Acquisitions (critical for driver reliability)

Three general steps: **(1)** stop + `*OPC?` before reconfiguring, **(2)** acquire, **(3)** retrieve results only once acquisition is confirmed complete.

| | Blocking (`:DIGitize`) | Polling (`:SINGle` + status poll) |
|---|---|---|
| Use when | Scope is guaranteed to trigger from current setup/DUT | Trigger may or may not occur |
| Pros | Simplest, fastest | Interface never times out; no Device Clear needed if no trigger |
| Cons | Interface can time out; only Device Clear regains control if no trigger | Slower, needs a poll loop + max-wait bound |

**Blocking pattern:**
```python
scope.write(":DIGitize")          # blocks until acquisition + processing done
```

**Polling pattern (repetitive/continuous DUT):**
```
:STOP
*OPC?                              # wait for stop to complete
:SINGle                            # arm, returns immediately
# poll until RUN bit (bit 3, mask 0x8) of :OPERegister:CONDition? clears, with a timeout
```

**Single-shot DUT pattern** (DUT fires exactly once — the scope MUST be armed before the DUT is triggered; `:DIGitize` cannot be used here because it blocks and there's no chance to enable the DUT in between):
```
:STOP
*OPC?
:SINGle
# poll :AER? until it returns 1  -> trigger system is armed
# NOW enable/fire the external DUT
# poll :OPERegister:CONDition? bit 0x8 (RUN) until clear, with a timeout
```
This single-shot pattern is the closest analog to a laser-plasma shot-triggered acquisition and is the recommended template for `cetal_scopes`' single-shot capture path.

## 16. Status Reporting — Register Model (for robust polling/interrupts)

IEEE-488.2 defines a layered register model: **event registers** (latch until read/cleared) feed **summary bits** into higher-level registers when unmasked by a corresponding **enable register**. `*CLS` clears all event registers and queues (except the output queue, unless sent immediately after a terminator).

| Register | Query | Notes |
|---|---|---|
| Status Byte (STB) | `*STB?` (non-destructive, reads bit 6 as MSS) or serial-poll (destructive, reads bit 6 as RQS and clears it — **preferred** for SRQ-driven designs) | Summary bits: bit7 OPER, bit6 RQS/MSS, bit5 ESB, bit4 MAV, bit2 MSG, bit1 USR, bit0 TRG |
| Service Request Enable (SRE) | `*SRE <mask>` / `*SRE?` | Masks which STB bits can assert SRQ |
| Standard Event Status (ESR) | `*ESR?` (read-to-clear) | Bits: PON, URQ, CME (cmd error), EXE (exec error), DDE, QYE, RQC, **OPC** (bit 0 — key for overlapped-command sync, §3) |
| Standard Event Status Enable (ESE) | `*ESE <mask>` / `*ESE?` | e.g. `0x3C` enables all 4 error bits to summarize into ESB |
| Trigger Event Register (TER) | `:TER?` (read-to-clear) | Sets STB's TRG bit on each trigger; **must be re-cleared after each trigger** if polling for repeated events |
| Arm Event Register (AER) | `:AER?` | 1 once trigger system is armed — core of the single-shot DUT pattern (§15) |
| Operation Status Condition (`:OPERegister:CONDition?`) | live (non-latching) snapshot | **Bit 3 (mask `0x8`) = RUN** — set while an acquisition is in progress; this is the bit polled to detect completion in §15 |
| Operation Status Event (`:OPERegister[:EVENt]?`) | read-to-clear | Bit 3 RUN (stop→run/single transition), bit 4 RUI-Enab (remote UI re-enabled — relevant since some front-panel modal states disable remote commands with "settings conflict" errors), bit 5 WAIT-TRIG (armed), bit 9 MTE, bit 11 OVLR (input overload), bit 12 HWE, bit 13 IOC (any remote IO op completed, any interface), bit 14 IOF (an IO op failed, e.g. USB disconnect) |
| Overload Event Register (`:OVLRegister?`) | read-to-clear | Per-channel + ext-trigger 50 Ω overload flags |
| Error Queue | `:SYSTem:ERRor?` | 30-deep FIFO, oldest-first; overflow → error 350 replaces newest |

**Practical takeaway for `cetal_scopes`:** the polling loop in §15 (`:OPERegister:CONDition?` bit `0x8`) uses the *condition* (live) register, not the *event* (latching) one — appropriate for "is it still running right now", while `:AER?`/`:TER?` are used for one-shot arm/trigger detection.

## 17. Error Checking Pattern

Poll `:SYSTem:ERRor?` after every command/query during development or in a robust driver — it returns `+0,"No error"` when clean, else `<code>,"<message>"`. The reference Python example (§20) wraps every I/O call in `do_command`/`do_query_*` helpers that call `check_instrument_errors()` afterward and abort on the first real error.

## 18. Error Message Codes (Ch. 46, complete list)

Negative codes are the standard IEEE-488.2/SCPI-defined set (same across compliant instruments); positive codes are Keysight/scope-specific. Returned by `:SYSTem:ERRor?` as `<code>,"<string>"`.

**Standard (negative) codes:**
`-100` Command error · `-101` Invalid character · `-102` Syntax error · `-103` Invalid separator · `-104` Data type error · `-105` GET not allowed · `-108` Parameter not allowed · `-109` Missing parameter · `-112` Program mnemonic too long · `-113` Undefined header · `-114` Header suffix out of range · `-120` Numeric data error · `-121` Invalid character in number · `-123` Exponent too large · `-124` Too many digits · `-128` Numeric data not allowed · `-131` Invalid suffix · `-134` Suffix too long · `-138` Suffix not allowed · `-148` Character data not allowed · `-150` String data error · `-151` Invalid string data · `-158` String data not allowed · `-161` Invalid block data · `-168` Block data not allowed · `-170` Expression error · `-171` Invalid expression · `-178` Expression data not allowed · `-181` Invalid outside macro definition · `-183` Invalid inside macro definition · `-200` Execution error · `-220` Parameter error · `-221` **Settings conflict** (e.g. `:DIGitize`/`:WAVeform` while `:TIMebase:MODE` is ROLL/XY — see §19) · `-222` Data out of range · `-223` Too much data · `-224` Illegal parameter value · `-230` Data corrupt or stale · `-231` Data questionable · `-240` Hardware error · `-241` Hardware missing (unlicensed/unavailable feature) · `-250` Mass storage error · `-251` Missing mass storage · `-252` Missing media · `-253` Corrupt media · `-254` Media full · `-255` Directory full · `-256` File name not found · `-257` File name error · `-258` Media protected · `-272` Macro execution error · `-273` Illegal macro label · `-276` Macro recursion error · `-277` Macro redefinition not allowed · `-278` Macro header not found · `-300` Device specific error · `-310` System error · `-311` Memory error · `-313` Calibration memory lost · `-314` Save/recall memory lost · `-315` Configuration memory lost · `-320` Storage fault · `-321` Out of memory · `-330` Self-test failed · `-340` Calibration failed · `-400` Query error · `-410` Query INTERRUPTED · `-420` Query UNTERMINATED · `-430` Query DEADLOCKED · `-440` Query UNTERMINATED after indefinite response

**Device-specific (positive) codes:** `+10` Software Fault Occurred · `+100` File Exists · `+101` End-Of-File Found · `+102` Read Error · `+103` Write Error · `+104` Illegal Operation · `+105` Print Canceled · `+106` Print Initialization Failed · `+107` Invalid Trace File · `+108` Compression Error · `+109` No Data For Operation (e.g. `:DISPlay:DATA?` requested but nothing stored) · `+112` Unknown File Type · `+113` Directory Not Supported

## 19. Notable Gotchas for a `cetal_scopes` Integration

- `:TIMebase:MODE` **must** be `MAIN` (or `WINDow`) for `:DIGitize` and any `:WAVeform` query to succeed — otherwise "Settings conflict" error. `*RST` resets it to `MAIN`.
- Reading `:WAVeform:DATA?` must happen **immediately** after `:DIGitize` completes — restarting acquisition or changing settings can overwrite/invalidate the just-acquired buffer before it's read.
- All query results from one program message must be fully read before sending the next message; queuing another write before reading clears the pending response and raises a query error. Never issue a `read` with no matching pending query — the controller hangs.
- Never use telnet port 5024 for binary waveform transfers (prompt bytes corrupt the stream) — use 5025, or better, a proper VISA/PyVISA session.
- The `:WAVeform:PREamble?` `format`/`type` field numbering is internally inconsistent in the vendor manual — see §9.3. Verify empirically before hard-coding.

## 20. Reference PyVISA Example (from Ch. 50, "Programming Examples")

Adapted skeleton — directly relevant as a starting point for a `cetal_scopes` Keysight backend:

```python
import pyvisa
import struct
import sys

wfm_fmt = "WORD"   # or "BYTE"

def do_command(cmd):
    scope.write(cmd)
    check_errors(cmd)

def do_command_ieee_block(cmd, values):
    scope.write_binary_values(f"{cmd} ", values, datatype='B')
    check_errors(cmd)

def do_query_string(q):
    r = scope.query(q); check_errors(q); return r.strip()

def do_query_number(q):
    r = scope.query(q); check_errors(q); return float(r)

def do_query_ieee_block(q):
    r = scope.query_binary_values(q, datatype='s', container=bytes)
    check_errors(q); return r

def check_errors(cmd):
    while True:
        err = scope.query(":SYSTem:ERRor?")
        if err.startswith("+0,"):
            break
        print(f"ERROR: {err}, command: '{cmd}'"); sys.exit(1)

rm = pyvisa.ResourceManager()
scope = rm.open_resource("TCPIP0::<hostname>::inst0::INSTR")
scope.timeout = 15000
scope.clear()

do_command("*CLS"); do_command("*RST")
do_command(":AUToscale")
do_command(":TRIGger:MODE EDGE")
do_command(":TRIGger:EDGE:SOURce CHANnel1")
do_command(":TRIGger:EDGE:LEVel 1.5")
do_command(":ACQuire:TYPE NORMal")
do_command(":DIGitize CHANnel1")

do_command(":WAVeform:SOURce CHANnel1")
do_command(":WAVeform:POINts:MODE RAW")
do_command(":WAVeform:POINts 10240")
do_command(f":WAVeform:FORMat {wfm_fmt}")
if wfm_fmt == "WORD":
    do_command(":WAVeform:BYTeorder LSBF")

preamble = do_query_string(":WAVeform:PREamble?").split(",")
x_increment = do_query_number(":WAVeform:XINCrement?")
x_origin    = do_query_number(":WAVeform:XORigin?")
y_increment = do_query_number(":WAVeform:YINCrement?")
y_origin    = do_query_number(":WAVeform:YORigin?")
y_reference = do_query_number(":WAVeform:YREFerence?")

data_bytes = do_query_ieee_block(":WAVeform:DATA?")
fmt_char = "B" if wfm_fmt == "BYTE" else "H"
n = len(data_bytes) // (1 if wfm_fmt == "BYTE" else 2)
values = struct.unpack(f"{n}{fmt_char}", data_bytes)

times    = [x_origin + i * x_increment for i in range(n)]
voltages = [(v - y_reference) * y_increment + y_origin for v in values]

scope.close()
```

Note the manual's own dicts here (`wav_form_dict = {0:"BYTE", 1:"WORD", 4:"ASCii"}`, `acq_type_dict = {0:"NORMal", 1:"PEAK", 2:"AVERage", 3:"HRESolution"}`) are the ones cited as authoritative in §9.3.

## 21. Command-Family Map (for future expansion)

Full guide has 44 command-group chapters. Covered above, at varying depth: Common `*`, Root `:` (§4), `:ACQuire` (§5), `:TIMebase` (§6), `:TRIGger` general + Edge (§7), `:CHANnel<n>` (§8), `:WAVeform` (§9), `:MEASure` (§10, key commands only — not all ~150 measurement types), `:SYSTem` (§11), `:SAVE`/`:RECall` (§12), `:DISPlay:DATA?` (§13), `:DIGital<d>`/`:POD<n>` (§14), Status Reporting (§16), full Error Message code list (§18).

Not extracted — genuinely out of scope unless a specific need arises:
- **`:TRIGger:GLITch/PATTern/RUNT/TRANsition/TV/SHOLd/EBURst/ZONE`** (remaining ~8 trigger types beyond Edge) — pulse-width, pattern, runt, rise/fall-time, TV-sync, setup/hold, N-th-edge-burst, zone triggering. Each is its own small command family with its own summary table; pull the specific one if a diagnostic needs non-edge triggering.
- **`:SBUS<n>`** (ch. 37, ~2000 lines — the single largest chapter in the manual) — serial protocol decode/trigger (I2C, SPI, UART, CAN, LIN, USB, etc.). Only relevant if `cetal_scopes` needs decoded serial bus data rather than raw analog/digital waveforms — unlikely for laser-plasma diagnostics.
- **`:WGEN<w>`** (ch. 43) — built-in arbitrary/function waveform generator. Only relevant if the scope itself needs to drive a calibration or test signal.
- **`:CGRade`, `:HISTogram`, `:JITTer`, `:COUNter`, `:DVM`, `:FRANalysis`, `:LTESt`, `:MTESt`, `:RTEYe`, `:COMPliance`, `:CLOCk`** — licensed analysis-application command sets (color-grade, histogram, jitter, frequency counter, digital voltmeter, frequency response analysis, mask/limit testing, real-time eye diagram, USB compliance, clock recovery). Require the corresponding software license on the instrument.
- **The bulk of `:MEASure`'s ~150 individual measurement commands** — §10 covers the dozen most commonly needed plus the threshold/delay-definition mechanisms; the remainder (overshoot, preshoot, phase, slew rate, burst-specific, eye-diagram measurements, power measurements in ch. 30) follow the identical `:MEASure:<NAME>[?] [<source>]` pattern.
- **Obsolete/Discontinued Commands** (ch. 45) and **VISA/SICL/VISA.NET/SCPI.NET example variants** (rest of ch. 50, targeting C#/VB.NET/non-Python stacks) — not relevant to new Python development.

---
*Extracted from `infiiivision_programming.pdf` (Keysight InfiniiVision 6000 X-Series Programmer's Guide) for the [[cetal_scopes]] project.*
