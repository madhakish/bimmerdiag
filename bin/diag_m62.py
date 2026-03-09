#!/usr/bin/env python3
"""
diag_m62.py — BMW E39 540i diagnostic CLI (M62B44 / Siemens MS41.0)

Diagnostic tool for the 1997-1998 E39 540i M62B44 engine (non-TU).
Reads live data via EDIABAS and the K+DCAN cable (OLD mode).
Focused on idle quality, fuel trims, and vacuum leak diagnostics.

The MS41.0 (Siemens) DME uses SGBD DM528DS0 (auto-detected via D_0012).
Job and result names verified live against actual ECU — no [VERIFY] tags.

Usage:
    python bin/diag_m62.py                    # Full report
    python bin/diag_m62.py --health           # Quick system check
    python bin/diag_m62.py --idle             # Idle quality deep-dive (ICV, trims, lambda)
    python bin/diag_m62.py --trims            # Fuel trim analysis per bank
    python bin/diag_m62.py --lambda           # Lambda sensor monitoring
    python bin/diag_m62.py --roughness        # Per-cylinder roughness analysis
    python bin/diag_m62.py --sensors          # All sensor data
    python bin/diag_m62.py --faults           # Fault codes
    python bin/diag_m62.py --monitor [SECS]   # Continuous monitoring
    python bin/diag_m62.py --jobs             # List all available jobs
    python bin/diag_m62.py --job JOB [PARAMS] # Run any EDIABAS job

Notes:
    - Cable must be in OLD mode (K-line) for E39
    - Connect via 20-pin round connector under hood
    - Engine running for live data, ignition-on for stored data
"""

import argparse
import csv
import json
import os
import sys
import time
from ediabas import Ediabas, EdiabasError

# ---------------------------------------------------------------------------
# Terminal colors (same as diag.py)
# ---------------------------------------------------------------------------
if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    DIM = "\033[90m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
else:
    GREEN = YELLOW = RED = CYAN = DIM = BOLD = RESET = ""

NUM_CYLINDERS = 8  # M62 V8

# ---------------------------------------------------------------------------
# Default SGBD — override with --sgbd
# D_0012 group file resolves to DM528DS0 for the '97 540i MS41.0
# ---------------------------------------------------------------------------
DEFAULT_SGBD = "DM528DS0"
DME_GROUP = "D_0012"
FALLBACK_SGBDS = ["DM528DS0", "DM52M620", "DM52M621"]

# ---------------------------------------------------------------------------
# MS41.0 Job Mapping — VERIFIED LIVE against actual ECU
#
# The MS41 uses mixed naming: some results are STAT_xxx_WERT, others
# STATUS_xxx_WERT. The values below were probed with the engine running.
#
# Key MS41 quirk: run_job enumeration (apiResultName) misses many result
# fields. Use read_value() with direct name lookup instead.
# ---------------------------------------------------------------------------

# --- Core Engine Status ---
SENSORS_ENGINE = [
    # (job, result_name, unit, description)
    ("STATUS_MOTORDREHZAHL", "STAT_MOTORDREHZAHL_WERT", "rpm", "Engine RPM"),
    ("STATUS_UBATT", "STAT_UBATT_WERT", "V", "Battery Voltage"),
    ("STATUS_MOTORTEMPERATUR", "STAT_MOTORTEMPERATUR_WERT", "degC", "Coolant Temp"),
    ("STATUS_AN_LUFTTEMPERATUR", "STAT_AN_LUFTTEMPERATUR_WERT", "degC", "Intake Air Temp"),
    ("STATUS_GESCHWINDIGKEIT", "STATUS_GESCHWINDIGKEIT_WERT", "km/h", "Vehicle Speed"),
]

SENSORS_AIR = [
    ("STATUS_LMM", "STATUS_LMM_WERT", "kg/h", "MAF Mass Flow"),
    ("STATUS_LMM_VOLT", "STATUS_LMM_VOLT_WERT", "V", "MAF Voltage"),
    ("STATUS_DKP_VOLT", "STATUS_DKP_VOLT_WERT", "V", "Throttle Position (voltage)"),
    ("STATUS_LAST", "STAT_LAST_WERT", "ms", "Engine Load"),
    ("STATUS_EINSPRITZZEIT", "STAT_EINSPRITZZEIT_WERT", "ms", "Injection Time"),
    ("STATUS_ZUENDWINKEL", "STAT_ZUENDWINKEL_WERT", "deg", "Ignition Timing (BTDC)"),
]

# --- Idle Control ---
# The ICV on MS41 is reported via STATUS_LL_REGLER (idle controller %).
# STATUS_LL_LUFTBEDARF reports idle air demand in kg/h.
SENSORS_IDLE = [
    ("STATUS_LL_REGLER", "STATUS_LL_REGLER_WERT", "%", "Idle Controller (ICV)"),
    ("STATUS_LL_LUFTBEDARF", "STATUS_LL_LUFTBEDARF_WERT", "kg/h", "Idle Air Demand"),
    ("STATUS_MOTORDREHZAHL", "STAT_MOTORDREHZAHL_WERT", "rpm", "Engine RPM"),
]

# --- Lambda / Fuel Trims ---
# MS41 reports lambda adaptation as additive and multiplicative per bank.
# Additive = correction at idle (vacuum leak shows here)
# Multiplicative = correction at load (MAF scaling issues show here)
#
# Bank 1 = cylinders 1-4 (passenger side)
# Bank 2 = cylinders 5-8 (driver side)
SENSORS_LAMBDA = [
    # Pre-cat O2 sensor voltages (narrowband, 0.1-0.9V)
    ("STATUS_L_SONDE", "STATUS_L_SONDE_WERT", "V", "Pre-Cat O2 Bank 1"),
    ("STATUS_L_SONDE_2", "STATUS_L_SONDE_2_WERT", "V", "Pre-Cat O2 Bank 2"),
    # Pre-cat VKAT signal
    ("STATUS_LS_VKAT_SIGNAL_1", "STAT_LS_VKAT_SIGNAL_1_WERT", "V", "Pre-Cat Signal Bank 1"),
    ("STATUS_LS_VKAT_SIGNAL_2", "STAT_LS_VKAT_SIGNAL_2_WERT", "V", "Pre-Cat Signal Bank 2"),
    # Post-cat NKAT signal
    ("STATUS_LS_NKAT_SIGNAL_1", "STAT_LS_NKAT_SIGNAL_1_WERT", "V", "Post-Cat Signal Bank 1"),
    ("STATUS_LS_NKAT_SIGNAL_2", "STAT_LS_NKAT_SIGNAL_2_WERT", "V", "Post-Cat Signal Bank 2"),
]

SENSORS_TRIMS = [
    # Additive adaptation — this is the idle trim
    ("STATUS_LAMBDA_ADD_1", "STAT_LAMBDA_ADD_1_WERT", "%", "Additive Trim Bank 1"),
    ("STATUS_LAMBDA_ADD_2", "STAT_LAMBDA_ADD_2_WERT", "%", "Additive Trim Bank 2"),
    # Multiplicative adaptation — this is the load trim
    ("STATUS_LAMBDA_MUL_1", "STAT_LAMBDA_MUL_1_WERT", "%", "Multiplicative Trim Bank 1"),
    ("STATUS_LAMBDA_MUL_2", "STAT_LAMBDA_MUL_2_WERT", "%", "Multiplicative Trim Bank 2"),
    # Lambda integrator (short-term correction)
    ("STATUS_LAMBDA_INTEGRATOR_1", "STAT_LAMBDA_INTEGRATOR_1_WERT", "%",
     "Lambda Integrator Bank 1"),
    ("STATUS_LAMBDA_INTEGRATOR_2", "STAT_LAMBDA_INTEGRATOR_2_WERT", "%",
     "Lambda Integrator Bank 2"),
]

# All sensors for full dump
ALL_SENSORS = SENSORS_ENGINE + SENSORS_AIR + SENSORS_IDLE + SENSORS_LAMBDA + SENSORS_TRIMS

# --- German-English Translation (ME5.2 fault text) ---
# Reuse from diag.py or import if restructured
GERMAN_WORDS = {
    "Fehler": "Fault", "Sensor": "Sensor", "Leitung": "Wiring",
    "Kurzschluss": "Short circuit", "Unterbrechung": "Open circuit",
    "Signal": "Signal", "Grenzwert": "Threshold",
    "Lambdasonde": "Lambda sensor", "Gemischregelung": "Mixture control",
    "Leerlaufregler": "Idle control valve", "Leerlauf": "Idle",
    "Drosselklappe": "Throttle", "Luftmasse": "Air mass",
    "Luftmassenmesser": "MAF sensor", "Einspritzventil": "Injector",
    "Tankentl\xfcftung": "EVAP purge", "Tankentlüftung": "EVAP purge",
    "Nockenwelle": "Camshaft", "Klopfsensor": "Knock sensor",
    "K\xfchlmittel": "Coolant", "Kühlmittel": "Coolant",
    "Motortemperatur": "Engine temp",
    "Ansaugluft": "Intake air",
    "Abgas": "Exhaust",
    "Katalysator": "Catalytic converter",
    "oder": "or", "und": "and", "nicht": "not",
    "zu": "too", "hoch": "high", "niedrig": "low",
    "vor": "pre", "nach": "post",
    "\xfcberschritten": "exceeded", "überschritten": "exceeded",
    "unterschritten": "below limit",
}


# ---------------------------------------------------------------------------
# Formatting helpers (same patterns as diag.py)
# ---------------------------------------------------------------------------
def translate_german(text):
    if not text:
        return text
    words = text.split()
    translated = []
    for w in words:
        stripped = w.rstrip(".,;:!?")
        suffix = w[len(stripped):]
        if stripped in GERMAN_WORDS:
            translated.append(GERMAN_WORDS[stripped] + suffix)
        else:
            translated.append(w)
    return " ".join(translated)


def format_value(val, unit):
    if val is None:
        return f"{DIM}(no data){RESET}"
    if unit == "mV":
        return f"{val / 1000:.2f} V"
    elif unit == "rpm":
        return f"{val:.0f} rpm"
    elif unit == "degC":
        return f"{val:.1f} °C"
    elif unit == "%":
        return f"{val:.1f}%"
    elif unit == "V":
        return f"{val:.3f} V"
    elif unit == "kg/h":
        return f"{val:.1f} kg/h"
    else:
        return f"{val}"


def print_header(title):
    print(f"\n{BOLD}{'=' * 64}")
    print(f"  {title}")
    print(f"{'=' * 64}{RESET}")


def print_subheader(title):
    print(f"\n  {BOLD}{title}{RESET}")
    print(f"  {'-' * 40}")


def read_sensor(ecu, sgbd, job, result_name):
    try:
        return ecu.read_value(sgbd, job, result_name)
    except EdiabasError:
        return None


# ---------------------------------------------------------------------------
# SGBD Detection
# ---------------------------------------------------------------------------
def detect_sgbd(ecu):
    """Auto-detect M62 SGBD via D_0012 group file."""
    try:
        results = ecu.run_job(DME_GROUP, "IDENT")
        if len(results) > 0 and "VARIANTE" in results[0]:
            variante = results[0]["VARIANTE"]
            print(f"  {GREEN}detected: {variante}{RESET}")
            return variante
    except EdiabasError:
        pass

    # Fallback: try known M62 SGBDs
    for sgbd in FALLBACK_SGBDS:
        try:
            ecu.run_job(sgbd, "IDENT")
            print(f"  {GREEN}found: {sgbd}{RESET}")
            return sgbd
        except EdiabasError:
            continue

    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_identify(ecu, sgbd):
    """Display ECU identification."""
    print_header("ECU IDENTIFICATION — M62 DME (Siemens MS41.0)")
    try:
        ident = ecu.identify(sgbd)
        field_names = {
            "ID_BMW_NR": "BMW Part Number",
            "ID_MOTOR": "Engine",
            "ID_LIEF_NR": "Supplier Number",
            "ID_SW_NR": "Software Version",
            "ID_HW_NR": "Hardware Version",
            "ID_DIAG_INDEX": "Diag Index",
            "ID_COD_INDEX": "Coding Index",
            "ID_BUS_INDEX": "Bus Index",
        }
        for key, val in sorted(ident.items()):
            if key == "JOB_STATUS":
                continue
            label = field_names.get(key, key)
            print(f"    {label:30s}  {val}")

        # VIN from AIF
        try:
            aif = ecu.run_job(sgbd, "AIF_LESEN")
            if len(aif) > 1:
                vin = aif[1].get("AIF_FG_NR", "")
                if vin:
                    print(f"    {'VIN':30s}  {BOLD}{vin}{RESET}")
        except EdiabasError:
            pass

    except EdiabasError as e:
        print(f"  {RED}Error: {e}{RESET}")


def cmd_sensors(ecu, sgbd):
    """Read and display all sensors."""
    print_header("LIVE SENSOR DATA — M62")

    groups = [
        ("Engine", SENSORS_ENGINE),
        ("Air / Throttle", SENSORS_AIR),
        ("Idle Control", SENSORS_IDLE),
        ("Lambda Sensors", SENSORS_LAMBDA),
        ("Fuel Trims / Adaptation", SENSORS_TRIMS),
    ]
    for group_name, sensors in groups:
        print_subheader(group_name)
        for job, wert, unit, desc in sensors:
            val = read_sensor(ecu, sgbd, job, wert)
            print(f"    {desc:35s}  {format_value(val, unit)}")


def cmd_idle(ecu, sgbd):
    """
    Idle quality deep-dive.

    This is the primary diagnostic for the ICV hunting / stumble issue.
    Reads ICV duty cycle, RPM, fuel trims, and lambda simultaneously
    to characterize the feedback loop behavior.
    """
    print_header("IDLE QUALITY DIAGNOSTICS — M62")

    print(f"  {DIM}Diagnosing idle control loop stability.{RESET}")
    print(f"  {DIM}Engine must be running at idle for meaningful data.{RESET}")

    # --- Current State ---
    print_subheader("Current Idle State")
    rpm = read_sensor(ecu, sgbd, "STATUS_MOTORDREHZAHL", "STAT_MOTORDREHZAHL_WERT")
    icv = read_sensor(ecu, sgbd, "STATUS_LL_REGLER", "STATUS_LL_REGLER_WERT")
    coolant = read_sensor(ecu, sgbd, "STATUS_MOTORTEMPERATUR", "STAT_MOTORTEMPERATUR_WERT")
    maf = read_sensor(ecu, sgbd, "STATUS_LMM", "STATUS_LMM_WERT")
    idle_air = read_sensor(ecu, sgbd, "STATUS_LL_LUFTBEDARF", "STATUS_LL_LUFTBEDARF_WERT")
    throttle = read_sensor(ecu, sgbd, "STATUS_DKP_VOLT", "STATUS_DKP_VOLT_WERT")
    rpm_target = None  # MS41 doesn't expose target idle RPM directly

    print(f"    Engine RPM:        {format_value(rpm, 'rpm')}", end="")
    if rpm_target is not None and rpm is not None:
        delta = rpm - rpm_target
        color = GREEN if abs(delta) < 30 else (YELLOW if abs(delta) < 80 else RED)
        print(f"  (target: {rpm_target:.0f}, delta: {color}{delta:+.0f}{RESET})")
    else:
        print()

    print(f"    ICV Duty Cycle:    {format_value(icv, '%')}", end="")
    if icv is not None:
        # Normal idle ICV duty is roughly 25-45% when warm
        if icv > 60:
            print(f"  {RED}HIGH — DME is opening ICV wide to maintain idle{RESET}")
        elif icv > 45:
            print(f"  {YELLOW}elevated — DME is compensating{RESET}")
        elif icv < 15:
            print(f"  {YELLOW}very low — possible high idle air source bypassing ICV{RESET}")
        else:
            print(f"  {GREEN}(normal range){RESET}")
    else:
        print()

    print(f"    Coolant Temp:      {format_value(coolant, 'degC')}", end="")
    if coolant is not None and coolant < 80:
        print(f"  {YELLOW}(not at operating temp — trims may not be stable){RESET}")
    else:
        print()

    print(f"    MAF:               {format_value(maf, 'kg/h')}")
    if idle_air is not None:
        print(f"    Idle Air Demand:   {format_value(idle_air, 'kg/h')}")
    print(f"    Throttle (volts):  {format_value(throttle, 'V')}")

    # --- Fuel Trims ---
    print_subheader("Fuel Trim Analysis")
    print(f"    {DIM}Additive = idle correction (vacuum leak detector){RESET}")
    print(f"    {DIM}Multiplicative = load correction (MAF scaling){RESET}")
    print(f"    {DIM}Positive additive = adding fuel = compensating for lean = air leak{RESET}\n")

    add_b1 = read_sensor(ecu, sgbd, "STATUS_LAMBDA_ADD_1",
                          "STAT_LAMBDA_ADD_1_WERT")
    add_b2 = read_sensor(ecu, sgbd, "STATUS_LAMBDA_ADD_2",
                          "STAT_LAMBDA_ADD_2_WERT")
    mul_b1 = read_sensor(ecu, sgbd, "STATUS_LAMBDA_MUL_1",
                          "STAT_LAMBDA_MUL_1_WERT")
    mul_b2 = read_sensor(ecu, sgbd, "STATUS_LAMBDA_MUL_2",
                          "STAT_LAMBDA_MUL_2_WERT")
    int_b1 = read_sensor(ecu, sgbd, "STATUS_LAMBDA_INTEGRATOR_1",
                          "STAT_LAMBDA_INTEGRATOR_1_WERT")
    int_b2 = read_sensor(ecu, sgbd, "STATUS_LAMBDA_INTEGRATOR_2",
                          "STAT_LAMBDA_INTEGRATOR_2_WERT")

    def trim_color(val, threshold_warn=5, threshold_crit=10):
        if val is None:
            return DIM
        if abs(val) > threshold_crit:
            return RED
        if abs(val) > threshold_warn:
            return YELLOW
        return GREEN

    print(f"    {'':35s}  {'Bank 1':>10s}  {'Bank 2':>10s}  {'Delta':>8s}")
    print(f"    {'':35s}  {'(cyl 1-4)':>10s}  {'(cyl 5-8)':>10s}")
    print(f"    {'-' * 64}")

    for label, v1, v2 in [
        ("Additive (idle trim)", add_b1, add_b2),
        ("Multiplicative (load trim)", mul_b1, mul_b2),
        ("Lambda integrator (short-term)", int_b1, int_b2),
    ]:
        s1 = f"{trim_color(v1)}{v1:+.1f}%{RESET}" if v1 is not None else f"{DIM}n/a{RESET}"
        s2 = f"{trim_color(v2)}{v2:+.1f}%{RESET}" if v2 is not None else f"{DIM}n/a{RESET}"
        delta = ""
        if v1 is not None and v2 is not None:
            d = abs(v1 - v2)
            dc = YELLOW if d > 3 else GREEN
            delta = f"{dc}{d:.1f}{RESET}"
        print(f"    {label:35s}  {s1:>20s}  {s2:>20s}  {delta:>18s}")

    # --- Interpretation ---
    print_subheader("Diagnosis")
    issues = []

    if add_b1 is not None and add_b2 is not None:
        both_positive = add_b1 > 3 and add_b2 > 3
        both_high = add_b1 > 8 or add_b2 > 8
        one_side = abs(add_b1 - add_b2) > 5

        if both_positive and not one_side:
            issues.append(
                f"Both banks pulling positive additive trim ({add_b1:+.1f}% / {add_b2:+.1f}%)\n"
                f"      → Central vacuum leak (CCV, brake booster, EVAP purge, ICV gasket)\n"
                f"      → Or MAF under-reading due to intake modifications"
            )
        elif one_side:
            high_bank = "Bank 1 (cyl 1-4)" if add_b1 > add_b2 else "Bank 2 (cyl 5-8)"
            issues.append(
                f"Asymmetric additive trim — {high_bank} is higher\n"
                f"      → Leak on that bank (intake gasket, injector o-ring, valve cover gasket)"
            )

        if both_high:
            issues.append(
                "Additive trims near or beyond adaptation limit\n"
                "      → Large vacuum leak or severely miscalibrated MAF"
            )

    if mul_b1 is not None and mul_b2 is not None:
        if abs(mul_b1) > 10 or abs(mul_b2) > 10:
            issues.append(
                f"Multiplicative trims high ({mul_b1:+.1f}% / {mul_b2:+.1f}%)\n"
                f"      → MAF calibration mismatch (expected with 4\" intake + fluted manifold)\n"
                f"      → Consider: MAF flow straightener, or retune to match hardware"
            )

    if icv is not None and icv > 55:
        issues.append(
            f"ICV duty cycle high ({icv:.0f}%) — DME is opening ICV aggressively\n"
            f"      → Consistent with compensating for lean condition at idle\n"
            f"      → Combined with positive additive trims = vacuum leak confirmed"
        )

    if not issues:
        if all(v is None for v in [add_b1, add_b2, mul_b1, mul_b2]):
            print(f"    {YELLOW}Could not read trim data — verify job names with --jobs{RESET}")
            print(f"    {DIM}Run: python bin/diag_m62.py --jobs | grep -i adapt{RESET}")
            print(f"    {DIM}     python bin/diag_m62.py --jobs | grep -i lambda{RESET}")
            print(f"    {DIM}     python bin/diag_m62.py --jobs | grep -i leerlauf{RESET}")
        else:
            print(f"    {GREEN}Fuel trims within normal range. Idle control loop appears stable.{RESET}")
    else:
        print(f"    {YELLOW}{BOLD}FINDINGS:{RESET}\n")
        for i, issue in enumerate(issues, 1):
            print(f"    {YELLOW}[{i}] {issue}{RESET}\n")

    # --- ICV Stability Check ---
    print_subheader("ICV Stability Check (10 readings, 20s)")
    print(f"    {DIM}Watching ICV duty cycle and RPM for hunting behavior...{RESET}\n")

    icv_readings = []
    rpm_readings = []
    try:
        for i in range(10):
            icv_val = read_sensor(ecu, sgbd, "STATUS_LL_REGLER",
                                   "STATUS_LL_REGLER_WERT")
            rpm_val = read_sensor(ecu, sgbd, "STATUS_MOTORDREHZAHL",
                                   "STAT_MOTORDREHZAHL_WERT")
            if icv_val is not None:
                icv_readings.append(icv_val)
            if rpm_val is not None:
                rpm_readings.append(rpm_val)

            icv_str = f"{icv_val:.1f}%" if icv_val is not None else "n/a"
            rpm_str = f"{rpm_val:.0f}" if rpm_val is not None else "n/a"

            # Trend arrows
            icv_arrow = rpm_arrow = ""
            if len(icv_readings) > 1:
                diff = icv_readings[-1] - icv_readings[-2]
                if diff > 1:
                    icv_arrow = f" {RED}^{RESET}"
                elif diff < -1:
                    icv_arrow = f" {CYAN}v{RESET}"
                else:
                    icv_arrow = f" {DIM}={RESET}"
            if len(rpm_readings) > 1:
                diff = rpm_readings[-1] - rpm_readings[-2]
                if diff > 15:
                    rpm_arrow = f" {RED}^{RESET}"
                elif diff < -15:
                    rpm_arrow = f" {CYAN}v{RESET}"
                else:
                    rpm_arrow = f" {DIM}={RESET}"

            print(f"    {i * 2:2d}s: ICV {icv_str:>6s}{icv_arrow}   RPM {rpm_str:>5s}{rpm_arrow}")

            if i < 9:
                time.sleep(2)
    except KeyboardInterrupt:
        print(f"\n    {DIM}Interrupted.{RESET}")

    if len(icv_readings) >= 3:
        icv_min = min(icv_readings)
        icv_max = max(icv_readings)
        icv_range = icv_max - icv_min
        icv_mean = sum(icv_readings) / len(icv_readings)

        rpm_min = min(rpm_readings) if rpm_readings else 0
        rpm_max = max(rpm_readings) if rpm_readings else 0
        rpm_range = rpm_max - rpm_min

        print(f"\n    ICV range: {icv_min:.1f}% — {icv_max:.1f}% (swing: {icv_range:.1f}%)")
        print(f"    RPM range: {rpm_min:.0f} — {rpm_max:.0f} (swing: {rpm_range:.0f})")

        if icv_range > 10:
            print(f"\n    {RED}ICV is hunting — duty cycle swinging {icv_range:.0f}%{RESET}")
            print(f"    {RED}The DME idle control loop is oscillating.{RESET}")
            print(f"    {DIM}This confirms the feedback loop instability.{RESET}")
            print(f"    {DIM}Root cause is upstream: vacuum leak, MAF signal noise,{RESET}")
            print(f"    {DIM}or intake modification causing turbulent flow at MAF.{RESET}")
        elif icv_range > 5:
            print(f"\n    {YELLOW}ICV somewhat unstable — {icv_range:.0f}% swing{RESET}")
            print(f"    {DIM}Minor hunting detected. May be borderline.{RESET}")
        else:
            print(f"\n    {GREEN}ICV stable — {icv_range:.1f}% swing (normal){RESET}")

        if rpm_range > 100:
            print(f"    {RED}RPM unstable — {rpm_range:.0f} rpm swing at idle{RESET}")
        elif rpm_range > 50:
            print(f"    {YELLOW}RPM slightly unsteady — {rpm_range:.0f} rpm swing{RESET}")
        else:
            print(f"    {GREEN}RPM stable — {rpm_range:.0f} rpm swing{RESET}")


def cmd_trims(ecu, sgbd):
    """Focused fuel trim analysis with interpretation."""
    print_header("FUEL TRIM ANALYSIS — M62")
    print(f"    {DIM}Reading adaptation values from DME...{RESET}\n")

    print_subheader("Adaptation Values")
    for job, wert, unit, desc in SENSORS_TRIMS:
        val = read_sensor(ecu, sgbd, job, wert)
        color = GREEN
        if val is not None:
            if abs(val) > 10:
                color = RED
            elif abs(val) > 5:
                color = YELLOW
        fmt = f"{color}{format_value(val, unit)}{RESET}"
        print(f"    {desc:35s}  {fmt}")

    print_subheader("What These Mean For Your Car")
    print(f"""
    {BOLD}With the 4" intake + fluted manifold on a stock flash:{RESET}

    Multiplicative trims will likely be non-zero because the MAF
    calibration doesn't match the new airflow dynamics. This is
    {YELLOW}expected{RESET} and not the cause of your stumble.

    Additive trims are the vacuum leak indicator. These represent
    the DME's idle-specific fuel correction. If these are pulling
    positive (adding fuel), the engine is lean at idle — meaning
    unmetered air is getting in somewhere.

    Lambda integrator is the {BOLD}real-time{RESET} correction happening right
    now. Watch this with --monitor to see it oscillate. If it's
    swinging rapidly between positive and negative, the DME is
    chasing something — which makes the ICV chase it too.

    {BOLD}Key diagnostic thresholds:{RESET}
    Additive:       ±3% normal, ±5-8% suspect, >10% definite issue
    Multiplicative:  ±5% normal with mods, >15% needs attention
    Integrator:      Should oscillate ±2-3% around zero steadily
    """)


def cmd_lambda(ecu, sgbd):
    """Live lambda sensor monitoring."""
    print_header("LAMBDA SENSOR MONITORING — M62")
    print(f"    {DIM}Watching pre-cat O2 sensor voltages...{RESET}")
    print(f"    {DIM}Healthy sensors oscillate 0.1-0.9V at ~1-2 Hz.{RESET}")
    print(f"    {DIM}Lean condition: stuck low (< 0.3V). Rich: stuck high (> 0.7V).{RESET}")
    print(f"    {DIM}Ctrl+C to stop.{RESET}\n")

    print(f"    {'Time':>5s}  {'Bank 1':>8s}  {'Bank 2':>8s}  {'B1 Status':>12s}  {'B2 Status':>12s}")
    print(f"    {'-' * 50}")

    try:
        start = time.time()
        b1_vals = []
        b2_vals = []
        while True:
            elapsed = time.time() - start
            b1 = read_sensor(ecu, sgbd, "STATUS_L_SONDE",
                              "STATUS_L_SONDE_WERT")
            b2 = read_sensor(ecu, sgbd, "STATUS_L_SONDE_2",
                              "STATUS_L_SONDE_2_WERT")

            if b1 is not None:
                b1_vals.append(b1)
            if b2 is not None:
                b2_vals.append(b2)

            def o2_status(val):
                if val is None:
                    return f"{DIM}no data{RESET}"
                if val < 0.2:
                    return f"{CYAN}LEAN{RESET}"
                elif val > 0.7:
                    return f"{RED}RICH{RESET}"
                else:
                    return f"{GREEN}switching{RESET}"

            b1s = f"{b1:.3f}V" if b1 is not None else "n/a"
            b2s = f"{b2:.3f}V" if b2 is not None else "n/a"

            print(f"    {elapsed:5.1f}  {b1s:>8s}  {b2s:>8s}  "
                  f"{o2_status(b1):>22s}  {o2_status(b2):>22s}")

            # Keep last 30 readings for analysis
            b1_vals = b1_vals[-30:]
            b2_vals = b2_vals[-30:]

            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    # Summary
    print()
    for label, vals in [("Bank 1", b1_vals), ("Bank 2", b2_vals)]:
        if len(vals) >= 5:
            avg = sum(vals) / len(vals)
            mn, mx = min(vals), max(vals)
            oscillation = mx - mn
            print(f"    {label}: avg {avg:.3f}V, range {mn:.3f}-{mx:.3f}V, "
                  f"oscillation {oscillation:.3f}V", end="")
            if oscillation < 0.3:
                print(f"  {RED}(not switching — possible dead sensor or extreme lean/rich){RESET}")
            elif oscillation < 0.5:
                print(f"  {YELLOW}(weak switching){RESET}")
            else:
                print(f"  {GREEN}(healthy switching){RESET}")


def cmd_faults(ecu, sgbd):
    """Read and display fault codes."""
    print_header("FAULT CODES — M62 DME")
    try:
        results = ecu.run_job(sgbd, "FS_LESEN")
    except EdiabasError as e:
        print(f"  {RED}Error: {e}{RESET}")
        return

    faults = [r for r in results[1:] if "F_ORT_NR" in r]

    if not faults:
        print(f"  {GREEN}No fault codes stored.{RESET}")
    else:
        print(f"  {YELLOW}{len(faults)} fault(s):{RESET}\n")
        for i, f in enumerate(faults, 1):
            ort_nr = f.get("F_ORT_NR", "?")
            ort_text = f.get("F_ORT_TEXT", "Unknown")
            count = f.get("F_LZ", 0)
            loc_en = translate_german(ort_text)

            # Check if currently present
            present = False
            for k in range(1, 9):
                art_text = f.get(f"F_ART{k}_TEXT", "")
                if "momentan vorhanden" in art_text:
                    present = True
                    break

            status_color = RED if present else YELLOW
            status_label = "ACTIVE" if present else "STORED"

            print(f"  {status_color}[{status_label}]{RESET}  #{ort_nr} — {BOLD}{loc_en}{RESET}")
            if loc_en != ort_text:
                print(f"           DE: {DIM}{ort_text}{RESET}")
            print(f"           Count: {count}x")

            # Environment data
            for uw in range(1, 6):
                uw_text = f.get(f"F_UW{uw}_TEXT", "")
                uw_wert = f.get(f"F_UW{uw}_WERT")
                uw_einh = f.get(f"F_UW{uw}_EINH", "")
                if uw_text and uw_wert is not None and "Hexcode" not in str(uw_text):
                    en_text = translate_german(uw_text)
                    print(f"           {en_text}: {uw_wert:.1f} {uw_einh}")

            # Status annotations
            for k in range(1, 9):
                art_text = f.get(f"F_ART{k}_TEXT", "")
                if art_text and art_text != "--":
                    print(f"           {DIM}{translate_german(art_text)}{RESET}")
            print()


def cmd_health(ecu, sgbd):
    """Quick system health check."""
    print_header("SYSTEM HEALTH — M62 (E39 540i)")
    print()

    issues = []

    rpm = read_sensor(ecu, sgbd, "STATUS_MOTORDREHZAHL", "STAT_MOTORDREHZAHL_WERT")
    if rpm is not None:
        if rpm > 100:
            print(f"  {GREEN}[OK]{RESET}   Engine         {rpm:.0f} rpm — running")
        else:
            print(f"  {YELLOW}[--]{RESET}   Engine         Not running (ignition on)")
    else:
        print(f"  {RED}[!!]{RESET}   Engine         No data")
        issues.append("No DME communication")

    batt = read_sensor(ecu, sgbd, "STATUS_UBATT", "STAT_UBATT_WERT")
    if batt is not None:
        # MS41 returns voltage in Volts directly
        if batt < 12.0:
            print(f"  {RED}[!!]{RESET}   Battery        {batt:.2f}V")
            issues.append(f"Battery low ({batt:.1f}V)")
        elif batt < 13.5:
            print(f"  {YELLOW}[!!]{RESET}   Battery        {batt:.2f}V — alternator?")
            issues.append(f"Battery {batt:.1f}V — check alternator")
        else:
            print(f"  {GREEN}[OK]{RESET}   Battery        {batt:.2f}V")

    coolant = read_sensor(ecu, sgbd, "STATUS_MOTORTEMPERATUR", "STAT_MOTORTEMPERATUR_WERT")
    if coolant is not None:
        if coolant > 110:
            print(f"  {RED}[!!]{RESET}   Coolant        {coolant:.0f}°C — HOT")
            issues.append(f"Coolant {coolant:.0f}°C")
        elif coolant < 75 and rpm and rpm > 100:
            print(f"  {YELLOW}[!!]{RESET}   Coolant        {coolant:.0f}°C — cold")
        else:
            print(f"  {GREEN}[OK]{RESET}   Coolant        {coolant:.0f}°C")

    icv = read_sensor(ecu, sgbd, "STATUS_LL_REGLER", "STATUS_LL_REGLER_WERT")
    if icv is not None:
        if abs(icv) > 15:
            print(f"  {YELLOW}[!!]{RESET}   Idle Ctrl      {icv:+.1f}% (high correction)")
            issues.append(f"Idle controller at {icv:+.1f}%")
        else:
            print(f"  {GREEN}[OK]{RESET}   Idle Ctrl      {icv:+.1f}%")

    # Trims
    add_b1 = read_sensor(ecu, sgbd, "STATUS_LAMBDA_ADD_1",
                          "STAT_LAMBDA_ADD_1_WERT")
    add_b2 = read_sensor(ecu, sgbd, "STATUS_LAMBDA_ADD_2",
                          "STAT_LAMBDA_ADD_2_WERT")
    if add_b1 is not None and add_b2 is not None:
        max_trim = max(abs(add_b1), abs(add_b2))
        if max_trim > 10:
            print(f"  {RED}[!!]{RESET}   Fuel Trims     B1:{add_b1:+.1f}%  B2:{add_b2:+.1f}%")
            issues.append(f"Additive trims high ({add_b1:+.1f}/{add_b2:+.1f})")
        elif max_trim > 5:
            print(f"  {YELLOW}[!!]{RESET}   Fuel Trims     B1:{add_b1:+.1f}%  B2:{add_b2:+.1f}%")
            issues.append(f"Additive trims elevated")
        else:
            print(f"  {GREEN}[OK]{RESET}   Fuel Trims     B1:{add_b1:+.1f}%  B2:{add_b2:+.1f}%")
    elif add_b1 is not None or add_b2 is not None:
        val = add_b1 if add_b1 is not None else add_b2
        print(f"  {DIM}[--]{RESET}   Fuel Trims     partial data: {val:+.1f}%")

    # Faults
    try:
        faults = ecu.read_faults(sgbd)
        n = len(faults)
        if n == 0:
            print(f"  {GREEN}[OK]{RESET}   Fault Codes    None stored")
        else:
            print(f"  {YELLOW}[!!]{RESET}   Fault Codes    {n} DTC(s)")
            issues.append(f"{n} fault code(s)")
    except EdiabasError:
        print(f"  {DIM}[--]{RESET}   Fault Codes    could not read")

    # Summary
    print(f"\n{'=' * 64}")
    if not issues:
        print(f"  {GREEN}{BOLD}ALL SYSTEMS OK{RESET}")
    else:
        print(f"  {YELLOW}{BOLD}ATTENTION NEEDED — {len(issues)} issue(s):{RESET}")
        for issue in issues:
            print(f"    - {issue}")
    print()


def cmd_monitor(ecu, sgbd, duration, csv_file=None):
    """Continuous monitoring with focus on idle stability."""
    print_header(f"IDLE MONITOR — M62 ({duration}s)")

    fields = [
        ("STATUS_MOTORDREHZAHL", "STAT_MOTORDREHZAHL_WERT", "RPM"),
        ("STATUS_LL_REGLER", "STATUS_LL_REGLER_WERT", "ICV%"),
        ("STATUS_LAMBDA_INTEGRATOR_1", "STAT_LAMBDA_INTEGRATOR_1_WERT", "IntB1"),
        ("STATUS_LAMBDA_INTEGRATOR_2", "STAT_LAMBDA_INTEGRATOR_2_WERT", "IntB2"),
        ("STATUS_LAMBDA_ADD_1", "STAT_LAMBDA_ADD_1_WERT", "AddB1"),
        ("STATUS_LAMBDA_ADD_2", "STAT_LAMBDA_ADD_2_WERT", "AddB2"),
    ]

    header = f"  {'Time':>5s}"
    for _, _, label in fields:
        header += f"  {label:>8s}"
    print(header)
    print(f"  {'-' * (6 + 10 * len(fields))}")

    csv_fh = None
    csv_writer = None
    if csv_file:
        csv_fh = open(csv_file, "w", newline="")
        csv_writer = csv.writer(csv_fh)
        csv_writer.writerow(["timestamp"] + [label for _, _, label in fields])

    prev = {}
    try:
        start = time.time()
        while time.time() - start < duration:
            elapsed = time.time() - start
            line = f"  {elapsed:5.1f}"
            csv_row = [f"{elapsed:.1f}"]

            for job, wert, label in fields:
                val = read_sensor(ecu, sgbd, job, wert)
                if val is not None:
                    # Trend arrow
                    arrow = ""
                    if label in prev and prev[label] is not None:
                        diff = val - prev[label]
                        if label == "RPM":
                            if diff > 15:
                                arrow = f"{RED}^{RESET}"
                            elif diff < -15:
                                arrow = f"{CYAN}v{RESET}"
                        else:
                            if diff > 0.5:
                                arrow = f"{RED}^{RESET}"
                            elif diff < -0.5:
                                arrow = f"{CYAN}v{RESET}"

                    if label == "RPM":
                        line += f"  {val:>6.0f}{arrow:>2s}"
                    else:
                        line += f"  {val:>+7.1f}{arrow:>1s}"
                    csv_row.append(f"{val:.2f}")
                    prev[label] = val
                else:
                    line += f"  {'n/a':>8s}"
                    csv_row.append("")

            print(line)
            if csv_writer:
                csv_writer.writerow(csv_row)

            time.sleep(2)
    except KeyboardInterrupt:
        print(f"\n  {DIM}Stopped.{RESET}")
    finally:
        if csv_fh:
            csv_fh.close()
            print(f"\n  CSV saved: {csv_file}")


def cmd_roughness(ecu, sgbd):
    """Per-cylinder roughness / misfire analysis."""
    print_header("CYLINDER ROUGHNESS ANALYSIS — M62 V8")

    try:
        results = ecu.run_job(sgbd, "LESEN_SYSTEMCHECK_LAUFUNRUHE")
        data = results[1] if len(results) > 1 else {}
    except EdiabasError as e:
        print(f"  {RED}Error: {e}{RESET}")
        return

    values = {}
    for cyl in range(1, NUM_CYLINDERS + 1):
        key = f"LESEN_SYSTEMCHECK_LAUFUNRUHE_ZYL{cyl}_WERT"
        val = data.get(key)
        if val is not None:
            values[cyl] = val

    if not values:
        print(f"\n  {YELLOW}No roughness data available.{RESET}")
        print(f"  {DIM}Engine may need to be at operating temperature.{RESET}")
        return

    print(f"\n  Roughness Counters (lower = smoother)")
    print(f"  {DIM}Good: < 200 | Marginal: 200-600 | Bad: > 600{RESET}\n")

    max_val = max(values.values()) if values else 1
    bar_width = 40
    bank1_vals, bank2_vals = [], []

    for cyl in range(1, NUM_CYLINDERS + 1):
        val = values.get(cyl)
        if val is None:
            print(f"    Cyl {cyl}:  {DIM}--- (no data){RESET}")
            continue

        if val < 200:
            color, status = GREEN, "OK"
        elif val < 600:
            color, status = YELLOW, "WARN"
        else:
            color, status = RED, "FAIL"

        bar_len = int(val / max(max_val, 1) * bar_width) if max_val > 0 else 0
        bar = "#" * bar_len + "-" * (bar_width - bar_len)
        bank = "B1" if cyl <= 4 else "B2"
        print(f"    Cyl {cyl} ({bank}):  {color}{bar} {val:>6}{RESET}  [{status}]")

        if cyl <= 4:
            bank1_vals.append(val)
        else:
            bank2_vals.append(val)

    # Bank comparison
    print(f"\n  Bank Comparison:")
    if bank1_vals:
        avg1 = sum(bank1_vals) / len(bank1_vals)
        print(f"    Bank 1 (Cyl 1-4) avg: {avg1:.0f}")
    if bank2_vals:
        avg2 = sum(bank2_vals) / len(bank2_vals)
        print(f"    Bank 2 (Cyl 5-8) avg: {avg2:.0f}")

    if bank1_vals and bank2_vals:
        avg1 = sum(bank1_vals) / len(bank1_vals)
        avg2 = sum(bank2_vals) / len(bank2_vals)
        ratio = avg1 / avg2 if avg2 > 0 else 999
        if ratio > 2:
            print(f"\n    {YELLOW}Bank 1 significantly rougher than Bank 2 ({ratio:.1f}x){RESET}")
            print(f"    {DIM}Check: vacuum leaks, injectors, ignition on bank 1 side{RESET}")
        elif ratio < 0.5:
            print(f"\n    {YELLOW}Bank 2 significantly rougher than Bank 1 ({1/ratio:.1f}x){RESET}")
            print(f"    {DIM}Check: vacuum leaks, injectors, ignition on bank 2 side{RESET}")
        else:
            print(f"\n    {GREEN}Banks are reasonably balanced.{RESET}")


def cmd_reset_adapt(ecu, sgbd):
    """Reset DME adaptations (ADAPT_LOESCHEN)."""
    print_header("RESET DME ADAPTATIONS")
    print(f"\n  {YELLOW}This will reset:{RESET}")
    print(f"    - Lambda fuel trim adaptations (both banks)")
    print(f"    - Idle adaptations")
    print(f"    - Knock sensor adaptations")
    print(f"\n  {DIM}The ECU will re-learn these values over the next few drive cycles.{RESET}")

    confirm = input(f"\n  Reset all adaptations? (y/N): ").strip().lower()
    if confirm != "y":
        print(f"  {DIM}Cancelled.{RESET}")
        return

    try:
        ecu.run_job(sgbd, "ADAPT_LOESCHEN")
        print(f"\n  {GREEN}Adaptations reset successfully.{RESET}")
        print(f"  {DIM}Drive through several warm-up/cool-down cycles to re-learn.{RESET}")
    except EdiabasError as e:
        print(f"\n  {RED}Error: {e}{RESET}")


def cmd_run_job(ecu, sgbd, job_name, params=""):
    """Run an arbitrary EDIABAS job."""
    print_header(f"JOB: {job_name}" + (f" ({params})" if params else ""))
    try:
        results = ecu.run_job(sgbd, job_name, params)
        for i, result_set in enumerate(results):
            if not result_set:
                continue
            print(f"\n  {BOLD}Result Set {i}:{RESET}")
            for key, val in sorted(result_set.items()):
                print(f"    {key:40s}  {val}")
    except EdiabasError as e:
        print(f"  {RED}Error: {e}{RESET}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="BMW E39 540i Diagnostic CLI — M62B44 / Siemens MS41.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
{BOLD}Diagnostic commands:{RESET}
  --health           Quick system dashboard
  --idle             Idle quality deep-dive (ICV, trims, lambda, stability check)
  --trims            Fuel trim analysis per bank with interpretation
  --lambda           Live lambda sensor monitoring (Ctrl+C to stop)
  --roughness        Per-cylinder roughness analysis (8 cylinders)
  --sensors          All sensor values
  --faults           Fault codes with English translation

{BOLD}Actions:{RESET}
  --monitor [SECS]   Continuous idle monitoring (default 60s)
  --clear-faults     Clear all fault codes (with confirmation)
  --reset-adapt      Reset DME adaptations (lambda, idle, knock)
  --jobs             List all available ECU jobs
  --job JOB [PARAMS] Run any specific EDIABAS job

{BOLD}Options:{RESET}
  --sgbd SGBD        Override SGBD (default: auto-detect via {DME_GROUP})
  --csv FILE         Export monitor data to CSV

{BOLD}For idle hunting / stumble diagnostics:{RESET}
  python bin/diag_m62.py --idle              # Full idle diagnostic
  python bin/diag_m62.py --monitor 120       # Watch the hunting in real time
  python bin/diag_m62.py --monitor 120 --csv idle_hunt.csv  # Log for analysis
        """,
    )
    parser.add_argument("--sgbd", default=None)
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--idle", action="store_true")
    parser.add_argument("--trims", action="store_true")
    parser.add_argument("--lambda", dest="lambda_", action="store_true")
    parser.add_argument("--roughness", action="store_true")
    parser.add_argument("--sensors", action="store_true")
    parser.add_argument("--faults", action="store_true")
    parser.add_argument("--monitor", nargs="?", const=60, type=int, metavar="SECS")
    parser.add_argument("--clear-faults", action="store_true")
    parser.add_argument("--reset-adapt", action="store_true")
    parser.add_argument("--jobs", action="store_true")
    parser.add_argument("--job", nargs="+", metavar=("JOB", "PARAMS"))
    parser.add_argument("--csv", metavar="FILE")
    args = parser.parse_args()

    specific = any([args.health, args.idle, args.trims, args.lambda_, args.roughness,
                     args.sensors, args.faults, args.monitor is not None,
                     args.clear_faults, args.reset_adapt, args.jobs, args.job])
    full_report = not specific

    print(f"{BOLD}bimmerdiag{RESET} — E39 540i Diagnostic Tool (M62B44 / MS41.0)")
    print(f"{DIM}github.com/madhakish/bimmerdiag{RESET}")

    with Ediabas() as ecu:
        sgbd = args.sgbd
        if not sgbd:
            print(f"\n{DIM}Auto-detecting ECU via {DME_GROUP}...{RESET}", end=" ", flush=True)
            sgbd = detect_sgbd(ecu)
            if not sgbd:
                print(f"\n{RED}ERROR: Could not detect M62 ECU.{RESET}")
                print(f"  Try: --sgbd DM528DS0")
                sys.exit(1)
        print(f"SGBD: {BOLD}{sgbd}{RESET}")

        if args.jobs:
            print_header("AVAILABLE JOBS")
            jobs = ecu.list_jobs(sgbd)
            print(f"  {len(jobs)} jobs available:\n")
            for j in jobs:
                print(f"    {j}")
            print(f"\n  {DIM}Tip: pipe to file and grep for what you need:{RESET}")
            print(f"  {DIM}  python bin/diag_m62.py --jobs > m62_jobs.txt{RESET}")
            print(f"  {DIM}  grep -i leerlauf m62_jobs.txt    # idle control{RESET}")
            print(f"  {DIM}  grep -i lambda m62_jobs.txt      # lambda/O2{RESET}")
            print(f"  {DIM}  grep -i adaption m62_jobs.txt    # fuel trims{RESET}")
            return

        if args.job:
            job_name = args.job[0]
            params = args.job[1] if len(args.job) > 1 else ""
            cmd_run_job(ecu, sgbd, job_name, params)
            return

        if args.clear_faults:
            print_header("CLEAR FAULT CODES")
            try:
                results = ecu.run_job(sgbd, "FS_LESEN")
                fault_count = sum(1 for r in results[1:] if "F_ORT_NR" in r)
                if fault_count == 0:
                    print(f"  {GREEN}No faults — nothing to clear.{RESET}")
                    return
                print(f"  {fault_count} fault(s). Clear all?")
                if input("  Type 'yes': ").strip().lower() == "yes":
                    ecu.run_job(sgbd, "FS_LOESCHEN")
                    print(f"  {GREEN}Cleared.{RESET}")
                    time.sleep(1)
                    results = ecu.run_job(sgbd, "FS_LESEN")
                    remaining = sum(1 for r in results[1:] if "F_ORT_NR" in r)
                    if remaining > 0:
                        print(f"  {YELLOW}{remaining} fault(s) remain — these are active.{RESET}")
                else:
                    print("  Cancelled.")
            except EdiabasError as e:
                print(f"  {RED}Error: {e}{RESET}")
            return

        if args.reset_adapt:
            cmd_reset_adapt(ecu, sgbd)
            return

        if args.monitor is not None:
            cmd_monitor(ecu, sgbd, args.monitor, csv_file=args.csv)
            return

        if full_report:
            cmd_identify(ecu, sgbd)
            cmd_health(ecu, sgbd)
            cmd_idle(ecu, sgbd)
            cmd_faults(ecu, sgbd)
        else:
            if args.health:
                cmd_health(ecu, sgbd)
            if args.idle:
                cmd_idle(ecu, sgbd)
            if args.trims:
                cmd_trims(ecu, sgbd)
            if args.lambda_:
                cmd_lambda(ecu, sgbd)
            if args.roughness:
                cmd_roughness(ecu, sgbd)
            if args.sensors:
                cmd_sensors(ecu, sgbd)
            if args.faults:
                cmd_faults(ecu, sgbd)


if __name__ == "__main__":
    main()
