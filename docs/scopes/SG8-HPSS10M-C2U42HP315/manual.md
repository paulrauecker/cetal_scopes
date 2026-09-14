#  General Manual


!!! warning
    AI generated summary, be sure to double check the reference manual for critical applications.

Source: [*SG8-HP(SS)01M-C2U42HP315 RF Signal Generator 10MHz-8GHz Operating Manual*](http://advantex.ru/joom1/download/doc_download/8-sg8-hpss01m-operating-manual.html), Rev. 1.4, Advantex LLC, December 13, 2011.

Models covered: **SG8-HP01M** and **SG8-HPSS01M** (SS = additional spur-suppression / dual reference PLL option).



---

## 1. Package Contents

| # | Item | Qty | Note |
|---|------|-----|------|
| 1 | SG8 RF Signal Generator 10 MHz – 8 GHz | 1 | |
| 2 | AC power cord, CEE 7/7 (E+F) plug | 1 | Included by default unless customer specifies otherwise |
| 3 | USB Cable A-B, 3 m | 1 | |
| 4 | RS-232 cable, D-sub 9F–D-sub 9M, 3 m | 0/1 | Only included if requested by customer |
| 5 | CD with drivers and documentation | 1 | |
| 6 | Printed operating manual | 1 | |
| 7 | Calibration certificate | 1 | |
| 8 | Warranty certificate | 1 | |
| 9 | Packing list | 1 | |

---

## 2. Overview / RF Architecture

- Core: a PLL-based RF synthesizer. Reference signal → PFD → loop filter → octave VCO (4–8 GHz). VCO output is divided by 8 and fed to a DDS; the DDS output is filtered and fed back to the PFD, closing the loop (DDS acts as a fractional divider for fine step size).
- VCO output → frequency divider chain → harmonic filter bank → Automatic Power Control (APC) → output amplifier → coupler → **RF Out** (N-type connector).
- **Spur suppression (SS option, SG8-HPSS01M only):** adds a second reference generation stage — an internal TCXO (147 MHz) plus a VCXO-based reference PLL producing 150 MHz. The instrument automatically switches between 147 MHz and 150 MHz reference to minimize spurs from the DDS at the given output frequency; the fixed phase relation between the two frequencies and the narrow-band reference PLL response preserve low phase noise during switching.
- Output frequency range: 10 MHz – 8 GHz (RF-synthesizer octave VCO covers 4–8 GHz, divided down for lower ranges: division boundaries at 4000, 2000, 1000, 500, 250, 125, 62.5, 31.25, 15.625, 7.8125, 3.90625 MHz).

---

## 3. Installation, Maintenance and Safety

- Laboratory use only — **not protected against moisture or mud**.
- Leave clearance at bottom/rear for air cooling.
- Verify cable/connector condition and correct mating before connecting, to avoid connector damage.
- Use a grounded 3-pin AC receptacle (ground pin is bonded to chassis).
- Ensure all connected equipment is properly grounded with no voltage difference between chassis, to avoid input/output damage.
- Recommended calibration interval: **1 year** (output level/frequency accuracy can drift over time).
- Power-up/down sequence:
  1. Confirm front-panel ON/OFF is not pressed (off).
  2. Confirm rear-panel AC switch is off.
  3. Connect AC cord to instrument and mains.
  4. Turn on rear-panel AC switch.
  5. Turn on front-panel ON/OFF; the display shows the current operation mode.
  6. To power down, press front-panel ON/OFF.
  - For long-term non-use, also turn off the rear AC switch (only once the instrument is already off).
- **RF Out ON/OFF button** only switches power to the output-stage amplifier; the rest of the instrument keeps running and its state remains changeable even with RF Out off. RF Out defaults to OFF at every power-up or remote reset.
- ⚠ Turning RF Out ON causes a short (~5 ms) glitch/jump in RF level, because the APC has no input signal before RF Out turns on and so runs at maximum gain momentarily. **When connecting a sensitive-input device, use an external fixed attenuator** between RF Out and the DUT (see recommended connection diagram, Fig. 3).
- Rear fuse: 2 A / 250 V recommended.
- **Disposal:** at end of life, dispose via a licensed electronics-recycling representative, or contact sales@advantex.ru.

---

## 4. Control Elements and Interfaces

### Front panel
1. **RF Out** — RF signal output, N-type connector.
2. **RF Out ON/OFF LED** — lit when RF Out is enabled.
3. **RF Out ON/OFF button** — toggles power to the output amplifier only (see safety note above).
4. **Power On/Off button** — turns off all circuits except the first AC/DC converter.
5. Graphical OLED display, 128×64, yellow, 4-bit grayscale.
6. Rotary knob, 24 positions per 360°.
7. Keyboard (numeric/unit keys).
8. Context-sensitive soft-menu keys (4 buttons under the display).

### Rear panel
1. Main AC power switch (kills all circuits, including the AC/DC converter).
2. **USB** interface (Remote Control) — appears to the PC as a virtual COM port via a CP2102 USB-to-UART bridge (driver required; Win2K/XP/2K3/Vista/7, Mac OS, Linux 3.1 supported — download from Silicon Labs CP2102 driver page). See companion doc for remote-control details.
3. **RS-232** interface (Remote Control), 9-pin D-Sub.
4. Cooling fan.
5. Chassis grounding terminal (use only if the AC socket itself has no ground pin — normally the power cord provides grounding).
6. **REF Out** — reference-frequency output, SMA female. Outputs either the internal reference or a repeated/derived external reference (HP version only for the "derived from REF In" case).
7. **REF In** — external reference frequency input, SMA female.
8. **Aux In** — BNC connector. Used as (a) external trigger input in Sweep Frequency/Sweep Level modes, or (b) analog modulation input in FM/PhM modes.
9. **Mic In** — microphone-level analog input, alternate source for the same modulation/trigger path as Aux In.
10. AC power connector with fuse (2 A/250 V recommended).

### Aux In / Mic In internal signal path
Mic In and Aux In are each fed through a variable-gain amplifier (VGA, gain = 1×–64×) into a comparator/op-amp pair, producing two internal signals:
- **Analog In** — used as the modulating signal in FM/PhM modes.
- **Triggering** — used as the external trigger signal in SWF/SWL modes.

(Mic In path includes a 3 kΩ/1 µF bias network; Aux In path includes a 100 Ω series resistor; both merge into the same VGA/comparator stage — see manual Fig. 6 for the schematic.)

**Practical AUX In analog range (from the factory test program, not stated in the operating manual):** roughly **0.5 V to 2.5 V** — a DC sweep from 0.5–0.6 V to 2.4–2.5 V is what the factory test uses to verify the mean-value indicator moves full-scale.

---

## 5. Graphical User Interface

Display layout: **Status Bar** (top line — current mode & instrument state), **Menu Items** (textual/numeric/graphical, arranged left-to-right/top-to-bottom, can span multiple lines with a scrollbar), **Context-Sensitive Menu** (4 soft keys, meaning depends on current item), and a scroll-bar indicator when there are more than 3 lines. Grayed/shaded items are either informational-only or disabled in the current mode. The selected item highlights; a numeric item in edit mode becomes brighter ("active").

### 5.1 Menu Navigation (soft keys + knob)
- **RET** – go up one menu level.
- **↑** – previous item.
- **↓** – next item.
- **>>** – enter submenu (one level down).
- **OPT** – enter the options menu for the current operation mode.
- Knob: clockwise = ↓, counter-clockwise = ↑, press = >>.

### 5.2 Status Bar symbols

Current operation mode: `CW`, `SWF` (Sweep Frequency), `SWFC` (Sweep Frequency-Center, uses Center Freq + Span instead of Start/Stop), `SWL` (Sweep Level), `FM` (Frequency Modulation), `PhM` (Phase Modulation).

Sweep/modulation waveform shape: Saw (↗), Triangle (∧), Sine (∿, FM/PhM internal source), Square (⊔⊓, FM/PhM internal source).

Triggering type (SWF/SWL): Auto (repeats immediately, no external wait), Single (one pass per trigger event, then waits), Step (one step per trigger event).

Trigger/modulation source symbols: Manual (M, via TRIG softkey), Positive Slope (rising edge of Aux In), Negative Slope (falling edge of Aux In), External (Analog In signal from Aux In/Mic In, used in FM/PhM), Internal (I, internal modulation source in FM/PhM).

Reference frequency indicator: REF Out On + internal source in use; REF Out Off + external source in use; REF Out On + external source in use (available when SS option fitted).

Warnings/Errors: `UNC` (Uncalibrated — a parameter of the current mode is outside the calibrated range), `OVT` (Overtime — microcontroller couldn't keep up with the event queue), `#XX` (last error code).

### 5.3 Data Entry

Two entry methods: keyboard or rotary knob.

- Rotary knob default state = menu navigation. Click on a numeric item enters numeric-edit mode; rotation increases/decreases value; click again (or a soft key) exits back to navigation. Minimum knob step ≈ 1/10 of the current unit scale (e.g., 0.1 kHz if scale is kHz); step increases with faster rotation.
- Keyboard groups (Fig. 8):
  1. **D** — numeric digit buttons 0–9, decimal point.
  2. **B** — Backspace ("Bck") — deletes last entered symbol.
  3. **E** — Enter/Units group: GHz/s, MHz/ms, kHz/µs, Hz/dBm (unit also implies quantity type — frequency vs time vs level).
  4. **C** — operations group: `+`, `−`, `×`, `/`.

Pressing any D/B/C key auto-activates numeric entry mode; pressing an E key commits the entry and exits. Pressing an E key while in navigation mode (not editing) instead changes the displayed unit scale factor of that item.

Math operation / entry sequences (R = current value, D = entered numeric value, ⊕ = operator):

| Sequence | Result | Notes |
|---|---|---|
| E | repeats last operation (`R`) | Also used purely for unit-scale entry, e.g. Hz/kHz/MHz/GHz. Pressing "Bck" also exits repeat mode. |
| C E | `R·(−1)` | Only "−" is valid as the C-key here; negates current value. |
| D E | `D` | Direct numeric entry; unit taken from the E-key pressed. |
| D C E | `D·(−1)` | Negative value entry, e.g. "1","0","−","dBm" → −10 dBm. |
| C D E | `R ⊕ D` | Quick incremental entry, e.g. repeatedly pressing "+","1","0","0","MHz" steps the frequency up by 100 MHz each time; also useful for small offsets from the current value. |
| D C D E | `D1 ⊕ D2` | Evaluate an expression while entering a fresh value. |
| C D C E | `(R ⊕ D)·(−1)` | Rarely used. |
| C D C D E | `(R ⊕_A D1) ⊕_B D2` | Chained expression; intermediate results not sent to hardware until final E; each subsequent E-press repeats the last operation with the last operand. |
| D C D C D E | `(D1 ⊕_A D2) ⊕_B D3` | E.g. to compute `D · m/n`. |

Note: for multi-operand expressions, all operands except the last take the current unit scale factor at time of entry; the final operand's scale is set by the closing E-key. `*` and `/` operate with 10⁻³ accuracy and max value ≥ 10¹³. Internal evaluation uses 64-bit fixed point, factor 10⁴ for Hz, 10² for dBm/degrees, 10⁹ for seconds.

---

## 6. Instrument Functions

### 6.1 Main Menu
- **Operation Modes** — submenu of the 5 modes below.
- **Settings** — general instrument settings (Reference Freq., Analog In).
- **Save Current** — stores all current parameter values to nonvolatile EEPROM; these are reloaded automatically at next power-up (if checksum is valid; otherwise vendor defaults load — e.g. after a firmware update that changes the parameter set).
- **Load Default** — resets all parameters to factory defaults and enters CW mode.
- **Info** — instrument identification/diagnostic info.

Each operation mode keeps its own independent parameter set; changes persist across mode switching until power-off (unless saved via Save Current). CW is the power-on default mode unless Save Current was previously used.

### 6.2 Operation Modes (5 total)

#### 6.2.1 Continuous Wave (CW)
Parameters:
- **Frequency** — RF output frequency (settable in 0.0001 Hz increments even though only 12 leading digits display; extra digits can be entered via "C D E" offsetting).
- **Level** — RF output power (dBm).
- **Phase** — RF phase offset.

Phase relation: φ_out = ω_c·t + φ_o(ω_c) + φ, where ω_c = central frequency, φ_o(ω_c) = initial (start-time/frequency-dependent) phase offset, φ = user-set Phase parameter. Used, e.g., to phase-align two SG8 units sharing one reference: set identical frequency on both, then adjust φ on one to null the initial offset, after which φ changes give accurate relative phase control.

#### 6.2.2 Sweep Frequency (SWF / SWFC)
Step change of output frequency in a given shape (saw/triangle) at constant level; T_Sweep = time of one full period.

Main parameters:
- **Start Frequency**, **Stop Frequency** (or **Center Frequency ± Span** in SWFC display mode)
- **Level** — constant RF level during sweep
- **Dwell Time** — duration of each step
- **Frequency Step** — step size

Options menu (OPT):
- **Sweep mode type**: `Auto` (repeats immediately after each pass), `Single` (one pass per trigger, then waits for next trigger), `Step` (one step per trigger event).
- **Triggering**: `Manual` (TRIG softkey) or `External` (signal at Aux In) with selectable **Trig Slope**: `Positive` (rising edge) or `Negative` (falling edge). Even when External is selected, TRIG softkey can still fire it manually.
- **Dwell Time**, **Frequency Step** — duplicated here from the main menu.
- **Shape**: `Saw` or `Tri` (Triangle).
- **Frequency display / input mode**: `F1–F2` (Start/Stop) or `FC±Δ` (Center/Span).

Sweep shape depends on Start vs Stop relation: if F_Start < F_Stop (positive span) Saw ramps up (↗) and Triangle is ∧-shaped rising-then-falling; if F_Start > F_Stop (negative span) Saw ramps down (↘) and Triangle inverts (∨-shaped).

Output level calibration is computed per frequency step across the sweep so level stays constant over wide sweep ranges; however, if the swept range crosses one of the VCO-divider switching boundaries (4000/2000/1000/500/250/125/62.5/31.25/15.625/7.8125/3.90625 MHz) an unavoidable frequency glitch occurs at that boundary because the VCO cannot re-tune instantaneously. To hide this from the spectrum, the instrument briefly turns off RF output while the VCO retunes, causing a level jump on re-enable (due to APC). **Workaround: use FM mode instead of SWF** if a divider boundary must be crossed within the sweep — FM stays within the VCO's continuous-tuning margin and avoids the divider switch, at the cost of a smaller maximum span than SWF.

#### 6.2.3 Sweep Level (SWL)
Step change of output level in a given shape (saw/triangle) at constant frequency; T_Sweep = one pass/period time.

Main parameters:
- **Frequency** — constant output frequency
- **Start Level**, **Stop Level**
- **Dwell Time** — one-step duration
- **Level Step** — step size

Options menu mirrors SWF: Sweep mode type (Auto/Single/Step), Triggering (Manual/External + Trig Slope Positive/Negative), Dwell Time, Level Step, Shape (Saw/Tri). Shape vs Start/Stop level relation mirrors the SWF case (rising if P_Start<P_Stop, falling if P_Start>P_Stop).

#### 6.2.4 Frequency Modulation (FM)
Instantaneous frequency: ω(t) = ω_C + Δ·s(t), where ω_C = central (mean) frequency, s(t) = modulating signal, Δ = **Frequency Sensitivity** (Hz/V). Max|Δ·s(t)| is the **Frequency Deviation**. Output phase: φ(t) = ω_C·t + Δ·∫s(τ)dτ. For a sinusoidal modulating signal of intrinsic frequency Ω and deviation Δ: φ(t) = ω_C·t + (Δ/Ω)·sin(Ωt).

Main parameters:
- **Center Frequency**
- **Level**
- **Frequency Deviation** — used only with internal modulation source (grayed for external)
- **Frequency Sensitivity** — used only with external modulation source (Aux In/Mic In, grayed for internal)
- **Source Frequency** — intrinsic frequency of the internal modulating signal (grayed for external source)

Min/max/mean of the modulating signal shown as a graphical bar on the display.

Options menu: **Source** (Internal/External), **Waveform** (internal source only: Sine/Square), plus sub-items duplicating FM Deviation, FM Sensitivity, Int Source Freq.

⚠ FM is implemented **digitally**: Analog In signal is ADC-sampled (or an internal digital waveform is generated), converted to a frequency value, then loaded into the RF synthesizer. Unlike SWF (which computes/calibrates level per exact frequency), FM computation uses only the *center*-frequency calibration data to keep computation fast — acceptable because deviation ≪ center frequency, so the resulting level error is small. The Analog In VGA gain (Settings▸Analog In) should be tuned so the modulating signal's min/max fill most of the ADC full-scale range (shown via the graphical bar) for best resolution.

#### 6.2.5 Phase Modulation (PhM)
φ(t) = ω_C·t + Δ·s(t), where ω_C = center frequency, s(t) = modulating signal, Δ = **Phase Sensitivity** (rad/V). Max|Δ·s(t)| = **Phase Deviation**.

Main parameters:
- **Center Frequency**
- **Level**
- **Phase Deviation** — internal source only
- **Phase Sensitivity** — external source only (Aux In/Mic In)
- **Source Frequency** — internal source's intrinsic frequency

Options menu: **Source** (Internal/External), **Waveform** (Sine/Square, internal only), plus PhM Deviation / PhM Sensitivity / Int Source Freq sub-items.

⚠ PhM is also implemented **digitally** (same ADC/DDS mechanism as FM).

### 6.3 Settings

#### 6.3.1 Reference Freq.
- **Ext Ref Freq** — nominal value of the external reference signal expected at REF In; must match the actual applied signal for correct operation.
- **Int Ref Freq** — internal reference frequency value, read-only/not editable.
- **Source**: `Internal` or `External` (REF In).
- **Ref Output**: `On` (REF Out active — outputs internal source, or repeats/derives from REF In when external source is selected, HP version only) or `Off`.
- **Int Ref Mode** (SG8-HPSS01M / SS option only — grayed on plain HP units, which are fixed at 147 MHz):
  - `Auto` — instrument automatically picks 147/150 MHz reference to minimize output spurs; **Auto only works correctly in CW mode** — other modes fall back to a fixed reference. *(The manual's literal text names this fallback "174 MHz" — likely a printing artifact given the fixed references elsewhere are 147/150 MHz.)*
  - `Fixed 147` — force 147 MHz reference.
  - `Fixed 150` — force 150 MHz reference.
  - Note: on power-up, HPSS units default Int Ref Mode to `Auto`; HP units default to `Fixed 147`. This setting is **not saved** by Save Current — always resets to its power-on default after a restart.

#### 6.3.2 Analog In
- **Gain** — VGA gain applied to whichever of Aux In / Mic In is in use as Analog In / Triggering source; selectable values: 1, 2, 4, 8, 16, 32, 64. Adjust so signal min/max fill the graphical bar (FM/PhM display) without exceeding it, for best ADC resolution utilization.

### 6.4 Save Current
Stores all current parameter values/settings to EEPROM. On next power-up, stored values are loaded if the checksum is valid (can fail e.g. after a firmware update that changes the parameter layout — defaults load instead).

### 6.5 Load Default
Resets every parameter to factory default and switches to CW mode.

### 6.6 Info
Displays:
- **Part Number** — full model designation including series, modification, assembly variant (e.g. `SG8-HPSS01M-C2U42HP315`).
- **Serial Number** — unique instrument ID (e.g. `64336-1051-001`).
- **Firmware Revision** — format `Rx.x mm/dd/yy` (e.g. `R1.1 05/20/2011`).
- **Operation Time** — cumulative hours of operation (increments hourly; unaffected by power cycles under 1 hour).
- **Power-On Count** — number of power-up events.
- **Temperature** — internal RF-synthesizer block temperature (°C); output-stage power supply auto-shuts-down at 75–80 °C.

---

## 7. Firmware Update

- Instrument MCU has two nonvolatile memories: **Flash** (program code) and **EEPROM** (device info incl. current firmware version, serial number, operation time, power-on count).
- Update requires **two Intel-HEX files** (one for Flash, one for EEPROM), transferred over RS-232 or USB (same CP2102 USB-UART bridge/driver used for remote control).
- On power-up, if the instrument doesn't receive a valid update command within ~0.5 s, it exits update mode and behaves as a normal SCPI-controlled instrument.
- Tool: **XMI Programmer** (Advantex; Windows XP/Vista/7 only).
- Procedure:
  1. Turn off the instrument.
  2. Connect via RS-232 or USB.
  3. Launch XMI Programmer.
  4. Select COM port, click **Connect** (if wrong port, click **Stop**, reselect, **Connect** again).
  5. Turn on the instrument — connection should establish within ~1 s, showing "Connecting... Ok!". If not, likely wrong port: Stop → power off → change port → Connect → power on again.
  6. Select **Write to MCU** radio button; leave **Rewrite All EEPROM Data** unchecked (checking it erases device-specific EEPROM data — serial number, operation time, power-on count — not normally desirable).
  7. Browse to and select the Flash Hex File and EEPROM Hex File.
  8. Click **Go!** — update takes ~30 s; progress/data-integrity check results appear in the log window.
  9. After successful verification, close the application.
  - To back up current firmware/EEPROM first, use the **Read from MCU** radio button at step 6 instead.

---

## 8. Factory Test / Calibration Program

(From Advantex's own *SG8-HP(SS)01M Test Program*, Rev. 1.1. This is the source of the instrument's **real, numeric acceptance specs** — the operating manual and vendor datasheet mirrors don't give these.)

### Document revisions
- Rev. 1.0 (Nov 5, 2011): initial test program for SG8-HP01M and SG8-HPSS01M.
- Rev. 1.1 (Dec 13, 2011): `STAT:QUES?` → `STAT:QUES:COND?`; revised REF Out phase-noise center-frequency list; revised RF Out/REF Out normalized phase-noise masks; revised max value of the calibration area's low bound; revised REF Out level range.

### Test summary (10 test groups)
1. **Mechanical** — case/legs/display/connectors inspected for damage; N-type center-contact blades and SMA/N-type threads checked with a gauge cap or plug.
2. **Power-On** — display lights within 2–5 s; RET/UP/DOWN/SEL-OPT keys, full keypad (digit entry, backspace, unit keys, math-operation sequences C·D·E) and rotary knob all verified functionally; **EEPROM integrity** verified by checking Part Number/Serial Number/Date in the Info menu against the rear-panel label.
3. **PLL Lock** — spectrum-analyzer check of clean, stable spectral shape at **1 GHz + 1 Hz** and **1 GHz − 1 Hz** (catches PLL lock failure at the low/high extremes of tuning voltage).
4. **Remote interfaces** — `*IDN?` sent over both USB and RS-232 must return a valid ID string (see companion doc for full remote-control details).
5. **RF level accuracy / calibration area** (using an R&S NRP-Z22 power sensor):
   - Calibration-area bounds: **high bound of the calibrated area must reach at least +22 dBm** somewhere in-band; **low bound of the calibrated area must be −8 dBm or lower** somewhere in-band.
   - **Absolute level accuracy 0…+20 dBm: ±0.2 dB.**
   - **Absolute level accuracy anywhere else within the calibrated area: ±0.5 dB.**
   - Full automated sweep covers **41 power points from −20 to +28.5 dBm** and **640 frequency points from 12.5 MHz to 8000 MHz** (calibration grid: 1 MHz step 10–100 MHz, 10 MHz step 100 MHz–1 GHz, 25 MHz step 1–8 GHz — the test deliberately offsets its own grid to sample *between* calibration points where error peaks). The SCPI script driving this sweep is reproduced in the companion "code" document.
6. **RF Out frequency / spectrum**:
   - **Frequency accuracy spec: ±(5 − Frequency-Counter's own relative error) ppm.** Measured at 100 MHz, `E_ppm = (F_measured − F_set)/F_set × 10⁶`.
   - **Normalized phase noise mask for RF Out** (referenced to 1 GHz carrier via `Φ_1GHz = Φ_Fc − 20·log₁₀(Fc[GHz]/1[GHz])`, measured at +20 dBm across center frequencies 0.5/1/2/3/4/5/6/7/8 GHz and offsets 1 kHz–10 MHz):

     | Offset | 1 kHz | 10 kHz | 100 kHz | 1 MHz | 10 MHz |
     |---|---|---|---|---|---|
     | Max phase noise | **−100 dBc/Hz** | **−115 dBc/Hz** | **−115 dBc/Hz** | **−115 dBc/Hz** | **−125 dBc/Hz** |
7. **REF Out**:
   - **REF Out level spec: −5 dBm to +10 dBm.**
   - **Normalized phase noise mask for REF Out** (single center frequency = internal 147 MHz reference):

     | Offset | 1 kHz | 10 kHz | 100 kHz | 1 MHz | 10 MHz |
     |---|---|---|---|---|---|
     | Max phase noise | **−110 dBc/Hz** | **−125 dBc/Hz** | **−125 dBc/Hz** | **−125 dBc/Hz** | **−125 dBc/Hz** |
8. **REF In sensitivity** — PLL must lock (clean spectrum) with an external reference of **20 MHz @ +10 dBm** and of **150 MHz @ −10 dBm**. This defines the real usable external-reference amplitude/frequency envelope (tighter/more useful than the rear-panel silkscreen figures).
9. **Analog inputs**:
   - **Mic In**: gain=64, FM mode w/ external source; speaking into the mic should visibly move the signal-level (envelope) and mean-value indicators on the graphical bar.
   - **AUX In / TRIG**: gain=1, FM mode w/ external source; a DC voltage swept **0.5–0.6 V → 2.4–2.5 V** into AUX In should shift the mean-value indicator from left to right (see §4 above).

### Test equipment & required accuracy
- **Power sensor** (e.g. R&S NRP-Z22): VSWR < 1.2 (10 MHz–8 GHz); required uncertainty ≤0.25 dB (−10 to +26 dBm) / ≤0.15 dB (0 to +20 dBm), remote-controllable.
- **Signal/phase-noise analyzer** (e.g. R&S FSUP8): 1 MHz–8 GHz range; required phase-noise floor better than the SG8 spec at every offset (e.g. ≤ −130 dBc/Hz @1 kHz, −150 @1 MHz, referenced to 1 GHz); measurement uncertainty <2.5 dB over 1 kHz–10 MHz.
- **Spectrum analyzer** (e.g. Advantest R3267): 10 MHz–8 GHz, RBW ≤30 Hz, ±0.1 ppm temperature stability / ±0.1 ppm/yr aging.
- **Frequency counter** (e.g. EZ Digital FC-3000): DC–3 GHz, ±1 ppm timebase.
- **External reference signal generator** (e.g. R&S SMC100A): 20–150 MHz output required.
- **DC voltage source**: 0…+3 VDC, ≥10 mA, output resistance <100 Ω (for the AUX In test).
- **Voltmeter** (e.g. Fluke 15B): 0…+3 VDC, 5% accuracy.
- **Microphone**: 2-pin electret condenser, biased +3 V via 3 kΩ (matches the Mic In bias network in §4).
- Test-station PC software stack referenced: WinXP, Tcl/Tk, VISA + IVI-COM drivers, R&S NRP-Toolkit v2.1.10, R&S NRPZ_VXIPNP v2.1.5, MCR (MATLAB Compiler Runtime) v7.14 — i.e. Advantex's own calibration apps are Tcl/Tk- and MATLAB-based.

### Test conditions
Ta = 23 (+5/−3) °C generally, 23 (+2/−3) °C for the level-accuracy test specifically; 20–70% RH; 750±30 mmHg; mains 220±20 V AC, 50±1 Hz (harmonics ≤5%); **15-minute warm-up** required before testing.

### Qualification of test personnel
Verification officers must be certified and have ≥2 years' practical radio-engineering measurement experience, and should study the Operating Manual first.

### Advantex-internal calibration software (not customer tools, documented here for completeness)
- **SG8_Level_Scan** — drives the SG8 over RS-232/USB through an automated scan (script in companion doc); outputs `SG8_level_<SerialNo>.csv` and `SG8_status_<SerialNo>.csv` (semicolon-delimited grids, rows = power in dBm, columns = frequency in MHz).
- **SG8_TP_LevelScan** — post-processes those two CSVs; reports min/max level error and its (power, frequency) coordinates, both within the actual calibrated area and within a user-specified "spec area" (default 0…+20 dBm, 12.5…8000 MHz); produces the absolute-accuracy contour plot and level-scan plot for the test report.
- **SG8_RFOut_PhaseNoise** / **SG8_REFOut_PhaseNoise** — sweep phase-noise measurement at +20 dBm over 0.5–8 GHz center frequencies (RF Out) or the fixed 147 MHz internal reference (REF Out), 1 kHz–10 MHz offsets.
- **SG8_TP_PhaseNoise** — normalizes measured phase noise to a 1 GHz-equivalent figure and compares the worst-case value at each standard offset against the mask tables above.

### Results / paperwork trail
A "PASSED" instrument on every test yields a **Calibration Certificate** (new units get "Factory Standard Calibration"; returning units get "Factory Standard Re-calibration", which has different Test-#1 mechanical requirements — cosmetic wear is tolerated). The underlying raw data/plots are archived in an "Instrument Test Report" and can be supplied on CD or by email on request — worth asking Advantex for this if you need traceable calibration data for a specific serial number.

---

## Quick Reference — Key Specs & Defaults

- Frequency range: 10 MHz – 8 GHz (VCO core 4–8 GHz + dividers)
- Frequency resolution: 0.0001 Hz (10⁻⁴ Hz); factory spec ±(5 − counter error) ppm accuracy @ 100 MHz
- Level resolution: 0.01 dBm (10⁻²); max output up to +28 dBm; absolute accuracy ±0.2 dB (0–20 dBm) / ±0.5 dB (elsewhere in calibrated area)
- Phase resolution: 0.01° (10⁻²); min. step ~0.15° @ 1 GHz
- Phase noise (RF Out, referenced to 1 GHz, +20 dBm): ≤ −100/−115/−115/−115/−125 dBc/Hz at 1k/10k/100k/1M/10M Hz offsets
- REF Out level: −5 to +10 dBm; phase noise ≤ −110/−125/−125/−125/−125 dBc/Hz at the same offsets
- Reference: internal TCXO at 147 MHz (HP), plus internal VCXO-PLL at 150 MHz (HPSS/SS option only)
- REF In must lock with external reference from 20 MHz@+10 dBm to 150 MHz@−10 dBm
- AUX In usable analog range: ~0.5–2.5 V
- Default power-on mode: CW, 1 GHz, 0 dBm, 0° phase, RF Out OFF (same as `*RST`/Load Default)
- Recommended calibration interval: 1 year
- Output-stage thermal shutdown: 75–80 °C internal synthesizer temperature
- Remote interfaces: RS-232 (9-pin D-Sub) and USB (CP2102 virtual COM) — see companion doc for full programming reference

---

## Supplementary Information (found via web search, not in the manuals)

The operating manual and test program don't identify the current company web presence or decode the model number. From a Taiwanese distributor's mirror of the Advantex datasheet (echannel.com.tw) and the vendor's current site:

- The manufacturer's `advantex-rf.com` domain (printed throughout the manuals) no longer resolves; the current vendor site is `advantex.ru` (Moscow).
- No manual/test-program revision newer than Rev. 1.4 / Rev. 1.1 (both Dec 13, 2011) turned up in public listings (ManualsLib and All-Guides mirror the same content), so this appears to still be the latest available documentation.
- **Model-number decode** — `SG8-HP(SS)01M-C2U42HP315`: `SG8` = base platform; `HP` = "High Power" output variant, `HPSS` = High Power **+** the dual-reference Spur-Suppression option (§2); `C2U42HP315` is the mechanical/chassis code — **2U** rack height, **42HP** width (a half-width 19″ rack module — "HP" here means "Horizontal Pitch," unrelated to the "HP" in the model name), **315 mm** depth.

Sources:
- [SG8 Signal Generator — echannel.com.tw (distributor spec sheet)](https://echannel.com.tw/product/sg8-signal-generator/?lang=en)
- [SG8-HP01M-C2U42HP315 — advantex.ru product page](http://advantex.ru/joom1/instruments-systems/rf-signal-generators/79-sg8-hp01m-c2u42hp255.html)
