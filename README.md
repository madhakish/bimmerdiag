# bimmerdiag

Python CLI tools for BMW ECU diagnostics via EDIABAS and K+DCAN cable.

Talk to your BMW's engine ECU from the command line. Read live sensor data,
fault codes, injector health, and ECU configuration without needing INPA's
GUI or reading German.

## What it does

```
$ python bin/diag.py --sensors

  Engine RPM                      697.50 rpm
  Battery Voltage                 14.94 V
  Coolant Temp                    58.00 degC
  Engine/Oil Temp                 58.86 degC
  Boost Actual                    1002 hPa (0.99 bar)
  MAF Mass                        21.94 kg/h
  Rail Pressure Actual            311.47 bar
  Odometer                        320,876 km (199,376 mi)
```

```
$ python bin/diag.py --faults

  [1] Code 18853
      Location: 49A5 LIN Bus, Kommunikation
      Symptom:  keine Botschaften von Glühsteuergerät GSG empfangen

  [2] Code 16165
      Location: 3F25 Ladeluftschlauch-Überwachung
      Symptom:  Ladeluftschlauch abgefallen
```

```
$ python bin/diag.py --monitor 60

 Time       RPM    Cool     Oil   Boost      MAF    Rail
    0s      697   58.0C   58.8C  1002hPa  21.9kg/h   311bar
    2s      704   58.0C   58.8C  1005hPa  22.1kg/h   312bar
    4s     1914   60.0C   60.3C  1139hPa  62.6kg/h   371bar
```

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
python bin/diag.py --sensors    # Live sensor data
python bin/diag.py --faults     # Fault codes
python bin/diag.py --injectors  # Injector health
python bin/diag.py --config     # ECU component config
python bin/diag.py --monitor 60 # Monitor for 60 seconds
python bin/diag.py --jobs       # List all 225 available ECU jobs
python bin/diag.py --job FS_LESEN  # Run any specific job
```

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
  diag.py        - CLI diagnostic tool
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
