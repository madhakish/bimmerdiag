# bimmerdiag

Python CLI tools for BMW ECU diagnostics via EDIABAS and K+DCAN cable.

Talk to your BMW's engine ECU from the command line. A terminal-based
alternative to INPA/ISTA — reads live data, interprets values, and tells
you what they mean. In English.

## What it does

```
$ python bin/diag.py --health

  bimmerdiag — BMW ECU Diagnostic Tool
  SGBD: D73N57B0

  ================================================================
    SYSTEM HEALTH CHECK
  ================================================================

  [OK]   Engine         697 rpm — running
  [OK]   Battery        14.94V
  [!!]   Cooling        58°C — not at operating temp
  [OK]   Oil            59°C, level 85%
  [OK]   Turbo          1002 hPa (idle)
  [!!]   Air Mass       -13% from target
  [OK]   Fuel System    Rail: 311 bar
  [OK]   Injectors      Mean: 1.8 rpm (smooth)
  [!!]   Fault Codes    6 DTC(s) stored

  ATTENTION NEEDED — 3 issue(s):
    - Coolant cold (58°C)
    - Air mass -13% below target
    - 6 fault code(s)
```

```
$ python bin/diag.py --injectors

  INJECTOR DIAGNOSTICS
  ================================================================

  Idle Roughness — RPM Deviation
    Good: < 3 rpm | Marginal: 3-8 rpm | Bad: > 8 rpm

    Cylinder 1: +1.23 rpm    [OK]
    Cylinder 2: -0.87 rpm    [OK]
    Cylinder 3: +4.56 rpm    [WARN] elevated
    Cylinder 4: -1.02 rpm    [OK]
    Cylinder 5: +2.34 rpm    [OK]
    Cylinder 6: -0.91 rpm    [OK]

  Per-Cylinder Assessment
    Cylinder 3: MARGINAL — monitor closely
    All others: GOOD — stable idle, minimal correction
```

```
$ python bin/diag.py --turbo

  TURBO / BOOST SYSTEM
  ================================================================

  Boost Pressure
    Actual:      1002 hPa (0.99 bar)
    Target:      1013 hPa (1.00 bar)
    Deviation:   -11 (-1.1%)
    Barometric:  997 hPa
    Relative:    +5 hPa above ambient (no boost at idle — normal)

  Air Mass Flow
    MAF sensor:  21.9 kg/h
    Deviation:   -49.6 mg (-12.7%)
    Air mass > 15% below target — check for:
      - Boost leaks (charge air hose, intercooler)
      - MAF sensor fouling or failure
      - Air filter restriction / box seal leaks
```

## Live Dashboard

```bash
python bin/dashboard.py
```

Full-screen, auto-refreshing visual dashboard with:
- Multi-zone RPM tachometer (green → yellow → red)
- Color-coded temperature gauges (cyan/cold → green/normal → yellow → red/hot)
- Boost actual vs target with deviation percentage
- Rail pressure gauge with target comparison
- Per-cylinder roughness bars with OK/WARN/FAIL status
- Fault code display
- Trend arrows, poll timing, cycle counter

Updates every ~1-2 seconds. Ctrl+C to exit. Requires `pip install rich`.

## CLI Commands

### Diagnostics
| Command | Description |
|---------|-------------|
| `--health` | Quick all-systems dashboard (OK/WARN/CRIT) |
| `--sensors` | All sensor values, grouped by system |
| `--faults` | Fault codes with English translation + shadow faults |
| `--injectors` | Deep injector analysis (IMA, roughness, fuel correction, offsets, per-cylinder assessment) |
| `--turbo` | Boost actual vs target, MAF, intercooler temps |
| `--cooling` | Coolant/oil temps, thermostat quick check |
| `--fuel` | Rail pressure actual vs target, fuel temp |
| `--exhaust` | Exhaust temps, DPF status |
| `--service` | CBS data, oil level, engine hours, odometer |
| `--config` | ECU component config (what's coded on/off) |

### Actions
| Command | Description |
|---------|-------------|
| `--monitor [SECS]` | Continuous monitoring with trend arrows (default 60s) |
| `--monitor 300 --csv log.csv` | Monitor with CSV export |
| `--clear-faults` | Clear all fault codes (with confirmation) |
| `--jobs` | List all 225 available ECU jobs |
| `--job JOB [PARAMS]` | Run any specific EDIABAS job |

### Options
| Option | Description |
|--------|-------------|
| `--sgbd SGBD` | Override SGBD (default: auto-detect via D_MOTOR) |
| `--csv FILE` | Export monitor data to CSV |

Running with no arguments gives a full report (identify + health + sensors +
injectors + faults + config).

## Requirements

- Windows (EDIABAS is Windows-only)
- Python 3.x (64-bit)
- EDIABAS V7.3.0 installed at `C:\EDIABAS`
- BimmerGeeks K+DCAN cable (or compatible FTDI-based cable)
- Car with ignition on, cable connected to OBD port

## Quick Start

1. Ensure EDIABAS is installed and configured (see [docs/SETUP.md](docs/SETUP.md))
2. Connect K+DCAN cable to car's OBD port
3. Turn ignition to position 2 (or start engine for live data)
4. **Close INPA if running** (exclusive COM port access)
5. Run diagnostics:

```bash
cd bimmerdiag
python bin/diag.py              # Full report
python bin/diag.py --health     # Quick system check
python bin/diag.py --injectors  # Injector deep-dive
python bin/diag.py --turbo      # Boost analysis
python bin/diag.py --monitor 60 # Monitor for 60 seconds
```

## What makes this better than INPA

- **English** — fault codes, ECU config, and diagnostic text translated
- **Interpretation** — doesn't just show numbers, tells you what they mean
- **Color-coded** — green/yellow/red status at a glance
- **Health dashboard** — one command to check all systems
- **Per-cylinder analysis** — injector health with thresholds and assessment
- **Actual vs target** — boost, rail pressure, air mass shown as deviations
- **Trend arrows** — monitor mode shows direction of change
- **CSV export** — log data for later analysis
- **No GUI needed** — runs in any terminal over SSH if needed

## Tested On

- **Vehicle:** 2009 BMW E70 xDrive35d (M57TU2 diesel)
- **ECU:** Bosch DDE7.3 (part 8587807, SW 55.84.78)
- **SGBD:** D73N57B0 / D73N57C0
- **Cable:** BimmerGeeks Pro K+DCAN (FTDI FT232R, COM4)
- **Tune:** Malone Stage 2 (EGR delete, DPF delete, DEF delete, swirl flap delete)
- **OS:** Windows 11, Python 3.12 64-bit, EDIABAS V7.3.0

## Architecture

```
diag.py (CLI)  -->  ediabas.py (API wrapper)  -->  api64.dll  -->  OBD32.dll  -->  COM port  -->  car
```

The EDIABAS stack is 32-bit. The 64-bit `api64.dll` bridges to the 32-bit
`api32.dll` / `OBD32.dll` via IPC. All protocol handling (KWP2000/CAN)
happens in the 32-bit layer.

## Files

```
bin/
  ediabas.py     - Python wrapper for EDIABAS API (api64.dll)
  diag.py        - CLI diagnostic tool (reports, analysis, monitoring)
  dashboard.py   - Live visual dashboard (requires: pip install rich)
config/
  obd.ini        - Reference OBD.INI config (copy to C:\Windows\OBD.INI)
docs/
  SETUP.md       - Full setup guide (cable, drivers, config files)
  DDE7-REFERENCE.md - DDE7 job reference, INPA menu translations
```

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `IFH-0018: INITIALIZATION ERROR` | Missing `C:\Windows\OBD.INI` | Copy from `config/obd.ini` |
| `SYS-0005: OBJECT FILE NOT FOUND` | Wrong SGBD name | Use `--sgbd D73N57B0` or auto-detect |
| `IFH-0018` with script but INPA works | INPA holding COM port | Close INPA first |
| All sensor values = 0 | Ignition off or engine not running | Turn key to pos 2 / start engine |
