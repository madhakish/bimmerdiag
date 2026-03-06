# EDIABAS / K+DCAN Cable Setup Guide

## Vehicle: E70 xDrive35d (M57TU2 diesel, DDE7 engine ECU)
## Cable: BimmerGeeks Pro K+DCAN (FTDI FT232R)

---

## Prerequisites

- **EDIABAS V7.3.0** installed at `C:\EDIABAS`
- **BimmerGeeks Standard Tools 2.12** (or manual EDIABAS install)
- **Python 3.x 64-bit**
- **BimmerGeeks Pro K+DCAN cable** (FTDI FT232R chipset)
- **FTDI VCP drivers** from ftdichip.com

## Critical Files

### 1. C:\Windows\OBD.INI (MOST IMPORTANT)

**This file MUST exist.** OBD32.dll reads it for CAN initialization. Without it,
you get `IFH-0018: INITIALIZATION ERROR` even though serial communication works.

```ini
[OBD]
Port=Com4
Hardware=OBD
RETRY=ON
```

If missing: `copy C:\EDIABAS\Bin\obd.ini C:\Windows\OBD.INI`

Windows 11 upgrades have been known to remove this file.

### 2. C:\EDIABAS\Bin\obd.ini

Same content as above. Must match your COM port.

### 3. C:\EDIABAS\Bin\EDIABAS.INI

Key settings:
```ini
Interface        =STD:OBD
EcuPath          =C:\EDIABAS\ECU
ApiTrace         =0       ; Set to 3 for debugging
IfhTrace         =0       ; Set to 3 for debugging
```

### 4. Environment Variables (Machine level)

```
EDIABAS_BASE=C:\EDIABAS
EDIABAS_PATH=C:\EDIABAS
EDIABAS_BIN=C:\EDIABAS\Bin
```

## Cable Setup

### COM Port

Check Device Manager -> Ports (COM & LPT) for the FTDI cable.
Update `Port=ComX` in both OBD.INI files to match.

### FTDI Latency Timer

Must be set to **1ms** for reliable BMW diagnostic communication.

Registry path:
```
HKLM\SYSTEM\CurrentControlSet\Enum\FTDIBUS\VID_0403+PID_6001+<SERIAL>\0000\Device Parameters
  LatencyTimer = 0x1
```

Can also be set via Device Manager -> Port properties -> Advanced.

### Cable Switch/Button

The BimmerGeeks cable has a physical switch:

| Position | Mode | Vehicles |
|----------|------|----------|
| **OUT** (unpushed) | New mode (DCAN) | E70, E90, E60, F/G/I series |
| **IN** (pushed) | Old mode (K-line, pins 7-8 bridged) | E46, E39, E53, E38, E83 |

**For E70: Switch must be OUT (unpushed).**

## Software Versions (known working)

| File | Size | Purpose |
|------|------|---------|
| `api32.dll` | 61,440 bytes | Stock BMW EDIABAS API (32-bit) |
| `api64.dll` | 24,576 bytes | EDIABAS API (64-bit, used by Python) |
| `OBD32.dll` | 77,824 bytes | Interface handler (loads OBD.INI) |

**Note:** EdiabasLib (Api32 subdirectory) is NOT needed for the BimmerGeeks cable.
The standard BMW DLLs work correctly.

## Troubleshooting

### IFH-0018: INITIALIZATION ERROR

1. Check `C:\Windows\OBD.INI` exists with correct COM port
2. Check COM port in Device Manager matches OBD.INI
3. Check FTDI latency timer = 1ms
4. Check cable switch: OUT for E70+, IN for E46 and older
5. Verify car ignition is ON (at least position 2)
6. **Close INPA/Tool32** - only one app can use the COM port

### SYS-0005: OBJECT FILE NOT FOUND

Wrong SGBD name. Use `D_MOTOR` group file for auto-detection,
or try known SGBDs: `D73N57B0`, `D73N57C0`, etc.

### SYS-0012: IDENTIFICATION ERROR

The SGBD file was found but the ECU didn't respond to the identification
protocol. Could be wrong SGBD variant for this specific ECU.

### API-0006: ACCESS DENIED

The 64-bit API (`api64.dll`) may have issues loading the 32-bit OBD32.dll.
Use the Python scripts in `bin/` which handle this correctly.

## Architecture Notes

The EDIABAS communication stack is 32-bit:

```
Python (64-bit) -> api64.dll -> [IPC bridge] -> api32.dll -> OBD32.dll -> COM port -> car
```

There is NO `OBD64.dll`. The 64-bit `api64.dll` (24KB) is a thin wrapper that
bridges to the 32-bit EDIABAS stack. All actual protocol handling happens in
the 32-bit layer.

The `apital64.exe` command-line tool can also run jobs from command files:
```
j D_MOTOR IDENT
r
q
```
But it has the same 64-bit limitations and may return `API-0006: ACCESS DENIED`.
