#!/usr/bin/env python3
"""
diag.py - BMW ECU diagnostic CLI tool

Reads live sensor data, fault codes, injector health, and ECU configuration
from BMW diesel ECUs (DDE7/M57/N57) via the EDIABAS API and K+DCAN cable.

Usage:
    python diag.py                    # Full diagnostic report
    python diag.py --sensors          # Live sensor data only
    python diag.py --faults           # Fault codes only
    python diag.py --injectors        # Injector health only
    python diag.py --monitor [secs]   # Continuous monitoring (default 60s)
    python diag.py --jobs             # List all available ECU jobs
    python diag.py --job JOB [PARAMS] # Run a specific job
    python diag.py --sgbd SGBD        # Override SGBD (default: auto-detect via D_MOTOR)
"""

import argparse
import sys
import time
from ediabas import Ediabas, EdiabasError

# DDE7 N57/M57TU2 sensor definitions: (job_name, result_name, unit, description)
SENSORS = [
    ("STATUS_MOTORDREHZAHL", "STAT_MOTORDREHZAHL_WERT", "rpm", "Engine RPM"),
    ("STATUS_UBATT", "STAT_UBATT_WERT", "mV", "Battery Voltage"),
    ("STATUS_KUEHLMITTELTEMPERATUR", "STAT_KUEHLMITTELTEMPERATUR_WERT", "degC", "Coolant Temp"),
    ("STATUS_MOTORTEMPERATUR", "STAT_MOTORTEMPERATUR_WERT", "degC", "Engine/Oil Temp"),
    ("STATUS_AN_LUFTTEMPERATUR", "STAT_AN_LUFTTEMPERATUR_WERT", "degC", "Intake Air Temp"),
    ("STATUS_ANSAUGLUFTTEMPERATUR", "STAT_ANSAUGLUFTTEMPERATUR_WERT", "degC", "Intake Manifold Temp"),
    ("STATUS_LADELUFTTEMPERATUR", "STAT_LADELUFTTEMPERATUR_WERT", "degC", "Charge Air Temp"),
    ("STATUS_UMGEBUNGSTEMPERATUR", "STAT_UMGEBUNGSTEMPERATUR_WERT", "degC", "Ambient Temp"),
    ("STATUS_KRAFTSTOFFTEMPERATUR", "STAT_KRAFTSTOFFTEMPERATUR_WERT", "degC", "Fuel Temp"),
    ("STATUS_LADEDRUCK_IST", "STAT_LADEDRUCK_IST_WERT", "hPa", "Boost Actual"),
    ("STATUS_LADEDRUCK_SOLL", "STAT_LADEDRUCK_SOLL_WERT", "hPa", "Boost Target"),
    ("STATUS_ATMOSPHAERENDRUCK", "STAT_ATMOSPHAERENDRUCK_WERT", "hPa", "Barometric"),
    ("STATUS_LMM_MASSE", "STAT_LMM_MASSE_WERT", "kg/h", "MAF Mass"),
    ("STATUS_LUFTMASSE_IST", "STAT_LUFTMASSE_IST_WERT", "mg/hub", "Air Mass Actual"),
    ("STATUS_LUFTMASSE_SOLL", "STAT_LUFTMASSE_SOLL_WERT", "mg/hub", "Air Mass Target"),
    ("STATUS_RAILDRUCK_IST", "STAT_RAILDRUCK_IST_WERT", "bar", "Rail Pressure Actual"),
    ("STATUS_RAILDRUCK_SOLL", "STAT_RAILDRUCK_SOLL_WERT", "bar", "Rail Pressure Target"),
    ("STATUS_KILOMETERSTAND", "STAT_KILOMETERSTAND_WERT", "km", "Odometer"),
]

# German -> English fault text translations for common DDE7 faults
FAULT_TRANSLATIONS = {
    "Testbedingungen erfüllt": "Test conditions met",
    "Testbedingungen erf\xfcllt": "Test conditions met",
    "Fehler würde das Aufleuchten einer Warnlampe verursachen": "Fault would cause warning lamp",
    "Fehler w\xfcrde das Aufleuchten einer Warnlampe verursachen": "Fault would cause warning lamp",
    "Fehler würde kein Aufleuchten einer Warnlampe verursachen": "Fault would NOT cause warning lamp",
    "Fehler w\xfcrde kein Aufleuchten einer Warnlampe verursachen": "Fault would NOT cause warning lamp",
    "Ladeluftschlauch abgefallen": "Charge air hose disconnected",
    "Luftmassenmesser": "Mass Airflow Sensor (MAF)",
}


def format_value(val, unit):
    """Format a sensor value with appropriate unit conversion."""
    if val is None:
        return "(no data)"
    if unit == "mV":
        return f"{val / 1000:.2f} V"
    elif unit == "hPa":
        return f"{val:.0f} hPa ({val / 1013.25:.2f} bar)"
    elif unit == "km":
        return f"{val:,.0f} km ({val * 0.621371:,.0f} mi)"
    else:
        return f"{val:.2f} {unit}"


def detect_sgbd(ecu):
    """Auto-detect the correct SGBD via the D_MOTOR group file."""
    try:
        results = ecu.run_job("D_MOTOR", "IDENT")
        if len(results) > 0 and "VARIANTE" in results[0]:
            return results[0]["VARIANTE"]
    except EdiabasError:
        pass

    # Fallback: try known DDE7 SGBDs
    for sgbd in ["D73N57B0", "D73N57C0", "D73M57A0", "D73M57C0"]:
        try:
            ecu.run_job(sgbd, "IDENT")
            return sgbd
        except EdiabasError:
            continue

    return None


def print_header(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def cmd_sensors(ecu, sgbd):
    """Read and display all live sensor values."""
    print_header("LIVE SENSOR DATA")
    for job, wert, unit, desc in SENSORS:
        val = ecu.read_value(sgbd, job, wert)
        print(f"  {desc:30s}  {format_value(val, unit)}")


def cmd_faults(ecu, sgbd):
    """Read and display fault codes with translations."""
    print_header("FAULT CODES (DTC)")
    try:
        faults = ecu.read_faults(sgbd)
    except EdiabasError as e:
        print(f"  Error reading faults: {e}")
        return

    if not faults:
        print("  No fault codes stored.")
        return

    print(f"  {len(faults)} fault(s) stored:\n")
    for i, f in enumerate(faults, 1):
        location = f.get("F_ORT_TEXT", "Unknown")
        symptom = f.get("F_SYMPTOM_TEXT", "Unknown")
        warning = f.get("F_WARNUNG_TEXT", "")
        code = f.get("F_ORT_NR", "?")

        print(f"  [{i}] Code {code}")
        print(f"      Location: {location}")
        print(f"      Symptom:  {symptom}")
        if warning:
            translated = FAULT_TRANSLATIONS.get(warning, warning)
            print(f"      Warning:  {translated}")
        print()


def cmd_injectors(ecu, sgbd):
    """Read injector health data."""
    print_header("INJECTOR DIAGNOSTICS")

    # IMA codes
    print("\n  IMA Matching Codes:")
    try:
        results = ecu.run_job(sgbd, "ABGLEICH_IMA_LESEN")
        if len(results) > 1:
            for key, val in sorted(results[1].items()):
                if "IMA_WERT_ZYL" in key:
                    cyl = key.split("ZYL")[1]
                    print(f"    Cylinder {cyl}: {val}")
    except EdiabasError as e:
        print(f"    Error: {e}")

    # Smoothness - RPM deviation
    print("\n  Idle Roughness (RPM deviation per cylinder):")
    try:
        results = ecu.run_job(sgbd, "STATUS_LAUFUNRUHE_DREHZAHL")
        if len(results) > 1:
            for key, val in sorted(results[1].items()):
                if "ZYL" in key and "WERT" in key:
                    cyl = key.split("ZYL")[1].split("_")[0]
                    flag = " <<<" if isinstance(val, (int, float)) and abs(val) > 5.0 else ""
                    print(f"    Cylinder {cyl}: {val:.3f} rpm{flag}")
    except EdiabasError as e:
        print(f"    Error: {e}")

    # Smoothness - fuel correction
    print("\n  Idle Fuel Correction (mg/stroke per cylinder):")
    try:
        results = ecu.run_job(sgbd, "STATUS_LAUFUNRUHE_LLR_MENGE")
        if len(results) > 1:
            for key, val in sorted(results[1].items()):
                if "ZYL" in key and "WERT" in key:
                    cyl = key.split("ZYL")[1].split("_")[0]
                    flag = " <<<" if isinstance(val, (int, float)) and abs(val) > 2.0 else ""
                    print(f"    Cylinder {cyl}: {val:.4f} mg/hub{flag}")
    except EdiabasError as e:
        print(f"    Error: {e}")

    # Injector swap counter
    print("\n  Injector Usage Counter:")
    try:
        results = ecu.run_job(sgbd, "STATUS_INJEKTORTAUSCH")
        if len(results) > 1:
            info = results[1].get("STAT_INJEKTORTAUSCH_INFO", "Unknown")
            print(f"    Status: {info}")
    except EdiabasError as e:
        print(f"    Error: {e}")


def cmd_identify(ecu, sgbd):
    """Display ECU identification."""
    print_header("ECU IDENTIFICATION")
    try:
        ident = ecu.identify(sgbd)
        field_names = {
            "ID_BMW_NR": "BMW Part Number",
            "ID_LIEF_TEXT": "Manufacturer",
            "ID_SW_NR_FSV": "Software (FSV)",
            "ID_SW_NR_MCV": "Software (MCV)",
            "ID_SW_NR_OSV": "Software (OSV)",
            "ID_DATUM": "Production Date",
            "ID_DIAG_INDEX": "Diag Index",
            "ID_VAR_INDEX": "Variant Index",
            "ID_COD_INDEX": "Coding Index",
            "ID_HW_NR": "Hardware Number",
            "JOB_STATUS": "Status",
        }
        for key, val in sorted(ident.items()):
            label = field_names.get(key, key)
            print(f"  {label:30s}  {val}")
    except EdiabasError as e:
        print(f"  Error: {e}")


def cmd_ecu_config(ecu, sgbd):
    """Display ECU component configuration (what's coded in/out)."""
    print_header("ECU COMPONENT CONFIGURATION")
    try:
        results = ecu.run_job(sgbd, "ECU_CONFIG")
        if len(results) > 1:
            cfg = results[1]
            for key in sorted(cfg.keys()):
                if key.endswith("_INFO"):
                    base = key.replace("_INFO", "")
                    status = cfg.get(base, "?")
                    text = cfg.get(base + "_TEXT", "?")
                    info = cfg[key]
                    if info != "-":
                        marker = "ON " if status == 1 else "OFF"
                        print(f"  [{marker}] {info:45s} ({text})")
    except EdiabasError as e:
        print(f"  Error: {e}")


def cmd_monitor(ecu, sgbd, duration=60):
    """Continuous sensor monitoring with timestamps."""
    print_header(f"MONITORING ({duration}s)")

    monitor_sensors = [
        ("STATUS_MOTORDREHZAHL", "STAT_MOTORDREHZAHL_WERT", "RPM"),
        ("STATUS_KUEHLMITTELTEMPERATUR", "STAT_KUEHLMITTELTEMPERATUR_WERT", "Cool"),
        ("STATUS_MOTORTEMPERATUR", "STAT_MOTORTEMPERATUR_WERT", "Oil"),
        ("STATUS_LADEDRUCK_IST", "STAT_LADEDRUCK_IST_WERT", "Boost"),
        ("STATUS_LMM_MASSE", "STAT_LMM_MASSE_WERT", "MAF"),
        ("STATUS_RAILDRUCK_IST", "STAT_RAILDRUCK_IST_WERT", "Rail"),
    ]

    header = f"{'Time':>5s}"
    for _, _, label in monitor_sensors:
        header += f"  {label:>8s}"
    print(header)
    print("-" * len(header))

    start = time.time()
    try:
        while time.time() - start < duration:
            elapsed = time.time() - start
            line = f"{elapsed:5.0f}s"
            for job, wert, label in monitor_sensors:
                val = ecu.read_value(sgbd, job, wert)
                if val is not None:
                    if label == "RPM":
                        line += f"  {val:8.0f}"
                    elif label in ("Cool", "Oil"):
                        line += f"  {val:7.1f}C"
                    elif label == "Boost":
                        line += f"  {val:6.0f}hPa"
                    elif label == "MAF":
                        line += f"  {val:6.1f}kg/h"
                    elif label == "Rail":
                        line += f"  {val:6.0f}bar"
                    else:
                        line += f"  {val:8.2f}"
                else:
                    line += f"  {'n/a':>8s}"
            print(line)
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n  Monitoring stopped.")


def cmd_run_job(ecu, sgbd, job_name, params=""):
    """Run an arbitrary EDIABAS job and display results."""
    print_header(f"JOB: {job_name}({params})")
    try:
        results = ecu.run_job(sgbd, job_name, params)
        for i, result_set in enumerate(results):
            if not result_set:
                continue
            print(f"\n  Set {i}:")
            for key, val in sorted(result_set.items()):
                print(f"    {key} = {val}")
    except EdiabasError as e:
        print(f"  Error: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="BMW ECU Diagnostic CLI - reads data via EDIABAS/K+DCAN",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python diag.py                          Full diagnostic report
  python diag.py --sensors                Live sensor values
  python diag.py --faults                 Read fault codes
  python diag.py --injectors              Injector health check
  python diag.py --monitor 120            Monitor for 2 minutes
  python diag.py --job FS_LOESCHEN        Clear fault codes
  python diag.py --job STATUS_MESSWERTBLOCK_LESEN "JA;CoSCR_st"
        """,
    )
    parser.add_argument("--sgbd", default=None,
                        help="SGBD file name (default: auto-detect via D_MOTOR)")
    parser.add_argument("--sensors", action="store_true", help="Show live sensor data")
    parser.add_argument("--faults", action="store_true", help="Show fault codes")
    parser.add_argument("--injectors", action="store_true", help="Show injector diagnostics")
    parser.add_argument("--config", action="store_true", help="Show ECU component config")
    parser.add_argument("--monitor", nargs="?", const=60, type=int, metavar="SECS",
                        help="Continuous monitoring (default 60s)")
    parser.add_argument("--jobs", action="store_true", help="List all available jobs")
    parser.add_argument("--job", nargs="+", metavar=("JOB", "PARAMS"),
                        help="Run a specific job")
    args = parser.parse_args()

    # Default to full report if no specific command given
    full_report = not any([args.sensors, args.faults, args.injectors, args.config,
                           args.monitor, args.jobs, args.job])

    with Ediabas() as ecu:
        # Detect or use specified SGBD
        sgbd = args.sgbd
        if not sgbd:
            print("Auto-detecting ECU...")
            sgbd = detect_sgbd(ecu)
            if not sgbd:
                print("ERROR: Could not detect ECU. Use --sgbd to specify manually.")
                sys.exit(1)
        print(f"SGBD: {sgbd}")

        if args.jobs:
            print_header("AVAILABLE JOBS")
            jobs = ecu.list_jobs(sgbd)
            print(f"  {len(jobs)} jobs available:\n")
            for j in jobs:
                print(f"    {j}")
            return

        if args.job:
            job_name = args.job[0]
            params = args.job[1] if len(args.job) > 1 else ""
            cmd_run_job(ecu, sgbd, job_name, params)
            return

        if args.monitor is not None:
            cmd_monitor(ecu, sgbd, args.monitor)
            return

        if full_report:
            cmd_identify(ecu, sgbd)
            cmd_sensors(ecu, sgbd)
            cmd_injectors(ecu, sgbd)
            cmd_ecu_config(ecu, sgbd)
            cmd_faults(ecu, sgbd)
        else:
            if args.sensors:
                cmd_sensors(ecu, sgbd)
            if args.faults:
                cmd_faults(ecu, sgbd)
            if args.injectors:
                cmd_injectors(ecu, sgbd)
            if args.config:
                cmd_ecu_config(ecu, sgbd)


if __name__ == "__main__":
    main()
