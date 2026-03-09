#!/usr/bin/env python3
"""
diag.py - BMW ECU diagnostic CLI

Terminal-based diagnostic tool for BMW DDE7 diesel ECUs (M57TU2/N57).
Reads live data via EDIABAS API and K+DCAN cable, interprets values,
and presents meaningful diagnostics in English.

Think of it as INPA/ISTA for your garage, minus the German.

Usage:
    python diag.py                    # Full diagnostic report
    python diag.py --sensors          # Live sensor data (grouped)
    python diag.py --faults           # Fault codes with translation
    python diag.py --injectors        # Deep injector analysis
    python diag.py --turbo            # Boost/turbo system analysis
    python diag.py --cooling          # Cooling system focus
    python diag.py --fuel             # Fuel system analysis
    python diag.py --exhaust          # Exhaust/DPF data
    python diag.py --service          # CBS, oil, engine hours
    python diag.py --config           # ECU component config
    python diag.py --health           # Quick all-systems dashboard
    python diag.py --monitor [secs]   # Continuous monitoring
    python diag.py --clear-faults     # Clear all fault codes
    python diag.py --jobs             # List all 225 ECU jobs
    python diag.py --job JOB [PARAMS] # Run any specific job
"""

import argparse
import csv
import io
import json
import os
import sys
import time
from ediabas import Ediabas, EdiabasError

# Baseline profile path (same directory as this script)
BASELINE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "baseline.json")

# ---------------------------------------------------------------------------
# Terminal colors (ANSI — works in Git Bash, Windows Terminal, most terminals)
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

# Status labels
OK = f"{GREEN}OK{RESET}"
WARN = f"{YELLOW}WARN{RESET}"
CRIT = f"{RED}CRIT{RESET}"
NA = f"{DIM}N/A{RESET}"

NUM_CYLINDERS = 6  # M57/N57 inline-6

# ---------------------------------------------------------------------------
# Sensor definitions — (job, result_name, unit, description)
# ---------------------------------------------------------------------------
SENSORS_ENGINE = [
    ("STATUS_MOTORDREHZAHL", "STAT_MOTORDREHZAHL_WERT", "rpm", "Engine RPM"),
    ("STATUS_UBATT", "STAT_UBATT_WERT", "mV", "Battery Voltage"),
]

SENSORS_TEMPS = [
    ("STATUS_KUEHLMITTELTEMPERATUR", "STAT_KUEHLMITTELTEMPERATUR_WERT", "degC", "Coolant Temp"),
    ("STATUS_MOTORTEMPERATUR", "STAT_MOTORTEMPERATUR_WERT", "degC", "Engine/Oil Temp"),
    ("STATUS_AN_LUFTTEMPERATUR", "STAT_AN_LUFTTEMPERATUR_WERT", "degC", "Intake Air Temp"),
    ("STATUS_ANSAUGLUFTTEMPERATUR", "STAT_ANSAUGLUFTTEMPERATUR_WERT", "degC", "Intake Manifold Temp"),
    ("STATUS_LADELUFTTEMPERATUR", "STAT_LADELUFTTEMPERATUR_WERT", "degC", "Charge Air Temp"),
    ("STATUS_UMGEBUNGSTEMPERATUR", "STAT_UMGEBUNGSTEMPERATUR_WERT", "degC", "Ambient Temp"),
    ("STATUS_KRAFTSTOFFTEMPERATUR", "STAT_KRAFTSTOFFTEMPERATUR_WERT", "degC", "Fuel Temp"),
]

SENSORS_BOOST = [
    ("STATUS_LADEDRUCK_IST", "STAT_LADEDRUCK_IST_WERT", "hPa", "Boost Actual"),
    ("STATUS_LADEDRUCK_SOLL", "STAT_LADEDRUCK_SOLL_WERT", "hPa", "Boost Target"),
    ("STATUS_ATMOSPHAERENDRUCK", "STAT_ATMOSPHAERENDRUCK_WERT", "hPa", "Barometric Pressure"),
]

SENSORS_AIR = [
    ("STATUS_LMM_MASSE", "STAT_LMM_MASSE_WERT", "kg/h", "MAF Mass Flow"),
    ("STATUS_LUFTMASSE_IST", "STAT_LUFTMASSE_IST_WERT", "mg/hub", "Air Mass Actual"),
    ("STATUS_LUFTMASSE_SOLL", "STAT_LUFTMASSE_SOLL_WERT", "mg/hub", "Air Mass Target"),
]

SENSORS_FUEL = [
    ("STATUS_RAILDRUCK_IST", "STAT_RAILDRUCK_IST_WERT", "bar", "Rail Pressure Actual"),
    ("STATUS_RAILDRUCK_SOLL", "STAT_RAILDRUCK_SOLL_WERT", "bar", "Rail Pressure Target"),
]

SENSORS_OTHER = [
    ("STATUS_KILOMETERSTAND", "STAT_KILOMETERSTAND_WERT", "km", "Odometer"),
]

ALL_SENSORS = (SENSORS_ENGINE + SENSORS_TEMPS + SENSORS_BOOST +
               SENSORS_AIR + SENSORS_FUEL + SENSORS_OTHER)

# ---------------------------------------------------------------------------
# Thresholds for health assessment — (warn_low, ok_low, ok_high, warn_high)
# None means no limit on that side. Values outside ok range are WARN,
# outside warn range are CRIT.
# ---------------------------------------------------------------------------
THRESHOLDS = {
    # Coolant: should be 80-105 when warm
    "Coolant Temp": (60, 80, 105, 115),
    # Oil: should be 80-120 when warm
    "Engine/Oil Temp": (60, 80, 120, 135),
    # Battery: 12.0-14.8V (values come as mV, converted before check)
    "Battery Voltage": (11.5, 12.0, 14.8, 15.5),
    # Rail pressure idle: 250-400 bar
    "Rail Pressure Actual": (200, 250, 1600, 1800),
}

# ---------------------------------------------------------------------------
# German-English word-level translation for fault text
# ---------------------------------------------------------------------------
GERMAN_WORDS = {
    # Common diagnostic terms
    "Fehler": "Fault", "fehler": "fault",
    "Sensor": "Sensor", "sensor": "sensor",
    "Leitung": "Wiring", "Leitungen": "Wiring",
    "Signal": "Signal", "signal": "signal",
    "Kurzschluss": "Short circuit", "kurzschluss": "short circuit",
    "Unterbrechung": "Open circuit",
    "Plausibilit\xe4t": "Plausibility", "Plausibilität": "Plausibility",
    "Grenzwert": "Threshold", "grenzwert": "threshold",
    "\xfcberschritten": "exceeded", "überschritten": "exceeded",
    "unterschritten": "below limit",
    "Regelabweichung": "Control deviation",
    "Kommunikation": "Communication",
    "Botschaften": "Messages", "Botschaft": "Message",
    "keine": "No", "kein": "No",
    "empfangen": "received", "gesendet": "sent",
    "von": "from", "nach": "to",
    "oder": "or", "und": "and",
    "nicht": "not",
    "zu": "too", "hoch": "high", "niedrig": "low",
    # Components
    "Abgas": "Exhaust", "abgas": "exhaust",
    "Abgastemperatur": "Exhaust temp", "Abgastemperatursensor": "Exhaust temp sensor",
    "Ladedruck": "Boost pressure",
    "Ladeluft": "Charge air", "Ladeluftschlauch": "Charge air hose",
    "Luftmasse": "Air mass", "Luftmassenmesser": "MAF sensor",
    "Kraftstoff": "Fuel", "Kraftstofftemperatur": "Fuel temp",
    "K\xfchlmittel": "Coolant", "Kühlmittel": "Coolant",
    "K\xfchlmitteltemperatur": "Coolant temp", "Kühlmitteltemperatur": "Coolant temp",
    "Motordrehzahl": "Engine RPM",
    "Motortemperatur": "Engine temp",
    "Raildruck": "Rail pressure",
    "Gl\xfchkerze": "Glow plug", "Glühkerze": "Glow plug",
    "Gl\xfchkerzen": "Glow plugs", "Glühkerzen": "Glow plugs",
    "Gl\xfchsteuerger\xe4t": "Glow plug controller", "Glühsteuergerät": "Glow plug controller",
    "Einspritzventil": "Injector",
    "Turbolader": "Turbocharger",
    "Partikelfilter": "Particulate filter", "DPF": "DPF",
    "Katalysator": "Catalytic converter",
    "Thermostat": "Thermostat",
    "Laufunruhe": "Idle roughness",
    "Drosselklappe": "Throttle", "Drosselklappen": "Throttle",
    "Drallklappen": "Swirl flaps", "Drallklappe": "Swirl flap",
    "Bremsunterdruck": "Brake vacuum",
    "Bremsunterdrucksensor": "Brake vacuum sensor",
    "Zuheizer": "Auxiliary heater",
    "Kraftstofffilterheizung": "Fuel filter heater",
    "Motorlager": "Engine mount",
    "Klimaanlage": "A/C system",
    "Ölniveau": "Oil level", "\xd6lniveau": "Oil level",
    "\xdcberwachung": "Monitoring", "Überwachung": "Monitoring",
    # Status terms
    "abgefallen": "disconnected",
    "verbaut": "installed", "nicht verbaut": "not installed",
    "aktiv": "active", "inaktiv": "inactive",
    "gesperrt": "locked", "freigegeben": "enabled",
    "Testbedingungen": "Test conditions",
    "erf\xfcllt": "met", "erfüllt": "met",
    "Aufleuchten": "illumination",
    "Warnlampe": "warning lamp",
    "verursachen": "cause",
    "w\xfcrde": "would", "würde": "would",
    "Fehlerspeicher": "Fault memory",
    "Umgebungsbedingungen": "Environmental conditions",
    "Spannungsversorgung": "Power supply",
}

# Exact phrase translations (checked first, higher priority)
GERMAN_PHRASES = {
    "Testbedingungen erf\xfcllt": "Test conditions met",
    "Testbedingungen erfüllt": "Test conditions met",
    "Ladeluftschlauch abgefallen": "Charge air hose disconnected",
    "keine Botschaften von Gl\xfchsteuerger\xe4t GSG empfangen":
        "No messages from glow plug controller (GSG)",
    "Fehler w\xfcrde das Aufleuchten einer Warnlampe verursachen":
        "Would trigger warning lamp",
    "Fehler w\xfcrde kein Aufleuchten einer Warnlampe verursachen":
        "Would NOT trigger warning lamp",
    "Fehler würde das Aufleuchten einer Warnlampe verursachen":
        "Would trigger warning lamp",
    "Fehler würde kein Aufleuchten einer Warnlampe verursachen":
        "Would NOT trigger warning lamp",
}

# ECU config flag English names
CONFIG_NAMES = {
    "DPF": "Diesel Particulate Filter",
    "DRO": "Throttle / Swirl Flaps",
    "GSG": "Glow Plug Controller",
    "ACC": "Adaptive Cruise Control",
    "MSA": "Auto Start-Stop",
    "FGR": "Cruise Control",
    "KLIMA": "A/C System",
    "PCSF": "DPF Pressure Sensor",
    "TCSF": "Exhaust Temp Sensor (pre-DPF)",
    "TOXI": "Exhaust Temp Sensor (pre-Cat)",
    "PDIFF": "DPF Differential Pressure",
    "ZUH": "Auxiliary Heater",
    "KFH": "Fuel Filter Heater",
    "BUS": "Brake Vacuum Sensor",
    "MLA": "Active Engine Mounts",
    "SCR": "SCR / DEF System",
    "EGR": "Exhaust Gas Recirculation",
}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def translate_german(text):
    """Translate German diagnostic text to English using phrase + word matching."""
    if not text:
        return text
    # Try exact phrase match first
    for de, en in GERMAN_PHRASES.items():
        if de in text:
            text = text.replace(de, en)
    # Word-level translation for remaining German
    words = text.split()
    translated = []
    for w in words:
        # Strip trailing punctuation for lookup
        stripped = w.rstrip(".,;:!?")
        suffix = w[len(stripped):]
        if stripped in GERMAN_WORDS:
            translated.append(GERMAN_WORDS[stripped] + suffix)
        else:
            translated.append(w)
    return " ".join(translated)


def format_value(val, unit):
    """Format a sensor value with unit conversion and display."""
    if val is None:
        return f"{DIM}(no data){RESET}"
    if unit == "mV":
        volts = val / 1000
        return f"{volts:.2f} V"
    elif unit == "hPa":
        bar = val / 1013.25
        return f"{val:.0f} hPa ({bar:.2f} bar)"
    elif unit == "km":
        mi = val * 0.621371
        return f"{val:,.0f} km ({mi:,.0f} mi)"
    elif unit == "rpm":
        return f"{val:.1f} rpm"
    elif unit == "bar":
        return f"{val:.1f} bar"
    elif unit == "kg/h":
        return f"{val:.1f} kg/h"
    elif unit == "mg/hub":
        return f"{val:.1f} mg/stroke"
    elif unit in ("degC", "°C"):
        return f"{val:.1f} °C"
    elif unit == "s":
        hours = val / 3600
        return f"{hours:.1f} hours ({val:.0f} s)"
    elif unit == "%":
        return f"{val:.0f}%"
    else:
        return f"{val:.2f} {unit}"


def status_color(val, threshold_key, unit=None):
    """Return colored status string based on thresholds."""
    if val is None or threshold_key not in THRESHOLDS:
        return ""
    # Convert mV to V for battery check
    check_val = val / 1000 if unit == "mV" else val
    warn_lo, ok_lo, ok_hi, warn_hi = THRESHOLDS[threshold_key]
    if (warn_lo is not None and check_val < warn_lo) or \
       (warn_hi is not None and check_val > warn_hi):
        return f"  {RED}[CRIT]{RESET}"
    if (ok_lo is not None and check_val < ok_lo) or \
       (ok_hi is not None and check_val > ok_hi):
        return f"  {YELLOW}[WARN]{RESET}"
    return f"  {GREEN}[OK]{RESET}"


def deviation_str(actual, target):
    """Format actual vs target deviation."""
    if actual is None or target is None or target == 0:
        return ""
    diff = actual - target
    pct = (diff / target) * 100
    color = GREEN if abs(pct) < 5 else (YELLOW if abs(pct) < 15 else RED)
    sign = "+" if diff > 0 else ""
    return f"{color}{sign}{diff:.1f} ({sign}{pct:.1f}%){RESET}"


def print_header(title):
    """Print a section header."""
    print(f"\n{BOLD}{'=' * 64}")
    print(f"  {title}")
    print(f"{'=' * 64}{RESET}")


def print_subheader(title):
    """Print a subsection header."""
    print(f"\n  {BOLD}{title}{RESET}")
    print(f"  {'-' * 40}")


def read_sensor(ecu, sgbd, job, result_name):
    """Read a single sensor value, returning None on error."""
    try:
        return ecu.read_value(sgbd, job, result_name)
    except EdiabasError:
        return None


# ---------------------------------------------------------------------------
# SGBD Detection
# ---------------------------------------------------------------------------
def detect_sgbd(ecu):
    """Auto-detect SGBD via D_MOTOR group file, with fallbacks."""
    try:
        results = ecu.run_job("D_MOTOR", "IDENT")
        if len(results) > 0 and "VARIANTE" in results[0]:
            return results[0]["VARIANTE"]
    except EdiabasError:
        pass
    for sgbd in ["D73N57B0", "D73N57C0", "D73M57A0", "D73M57C0"]:
        try:
            ecu.run_job(sgbd, "IDENT")
            return sgbd
        except EdiabasError:
            continue
    return None


# ---------------------------------------------------------------------------
# Baseline Profile
# ---------------------------------------------------------------------------
def load_baseline():
    """Load tuned baseline profile if it exists."""
    path = os.path.normpath(BASELINE_FILE)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return None


def save_baseline(profile):
    """Save tuned baseline profile."""
    path = os.path.normpath(BASELINE_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(profile, f, indent=2)


def cmd_baseline(ecu, sgbd):
    """Capture a tuned baseline profile at idle.

    Records current air mass, boost, and other deviations as the 'known good'
    state for this car's modification level. Future health checks compare
    against this baseline instead of the ECU's stock targets.
    """
    print_header("CAPTURE TUNED BASELINE")

    print(f"  {DIM}This records current sensor deviations as 'normal' for your car.{RESET}")
    print(f"  {DIM}Run at idle with engine warmed up for best results.{RESET}\n")

    # Take 5 samples over 10 seconds and average
    print(f"  Sampling (5 readings over 10 seconds)...\n")
    samples = {"air_dev": [], "boost_dev": [], "rpm": [], "coolant": [],
               "maf": [], "air_actual": [], "air_target": []}

    for i in range(5):
        rpm = read_sensor(ecu, sgbd, "STATUS_MOTORDREHZAHL", "STAT_MOTORDREHZAHL_WERT")
        air_act = read_sensor(ecu, sgbd, "STATUS_LUFTMASSE_IST", "STAT_LUFTMASSE_IST_WERT")
        air_tgt = read_sensor(ecu, sgbd, "STATUS_LUFTMASSE_SOLL", "STAT_LUFTMASSE_SOLL_WERT")
        maf = read_sensor(ecu, sgbd, "STATUS_LMM_MASSE", "STAT_LMM_MASSE_WERT")
        boost_act = read_sensor(ecu, sgbd, "STATUS_LADEDRUCK_IST", "STAT_LADEDRUCK_IST_WERT")
        boost_tgt = read_sensor(ecu, sgbd, "STATUS_LADEDRUCK_SOLL", "STAT_LADEDRUCK_SOLL_WERT")
        coolant = read_sensor(ecu, sgbd, "STATUS_KUEHLMITTELTEMPERATUR",
                              "STAT_KUEHLMITTELTEMPERATUR_WERT")

        if all(v is not None for v in [rpm, air_act, air_tgt, boost_act, boost_tgt]):
            air_dev = (air_act - air_tgt) / air_tgt * 100
            boost_dev = (boost_act - boost_tgt) / boost_tgt * 100
            samples["air_dev"].append(air_dev)
            samples["boost_dev"].append(boost_dev)
            samples["rpm"].append(rpm)
            samples["coolant"].append(coolant or 0)
            samples["maf"].append(maf or 0)
            samples["air_actual"].append(air_act)
            samples["air_target"].append(air_tgt)
            print(f"    Sample {i+1}: RPM {rpm:.0f}, air {air_dev:+.1f}%, "
                  f"boost {boost_dev:+.1f}%, coolant {coolant:.0f}°C")

        if i < 4:
            time.sleep(2)

    if not samples["air_dev"]:
        print(f"\n  {RED}No valid samples collected.{RESET}")
        return

    # Calculate averages
    avg_air_dev = sum(samples["air_dev"]) / len(samples["air_dev"])
    avg_boost_dev = sum(samples["boost_dev"]) / len(samples["boost_dev"])
    avg_rpm = sum(samples["rpm"]) / len(samples["rpm"])
    avg_coolant = sum(samples["coolant"]) / len(samples["coolant"])

    profile = {
        "description": "Tuned baseline — known-good idle deviations",
        "captured": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sgbd": sgbd,
        "mods": "Malone Stage 2 (EGR/DPF/DEF/swirl delete)",
        "conditions": {
            "rpm": round(avg_rpm, 0),
            "coolant_c": round(avg_coolant, 1),
            "samples": len(samples["air_dev"]),
        },
        "idle_air_deviation_pct": round(avg_air_dev, 1),
        "idle_boost_deviation_pct": round(avg_boost_dev, 1),
        "thresholds": {
            "air_mass_warn_pct": round(avg_air_dev - 8, 1),
            "air_mass_crit_pct": round(avg_air_dev - 15, 1),
        },
    }

    print(f"\n  {BOLD}Baseline Profile:{RESET}")
    print(f"    Air mass deviation at idle:  {avg_air_dev:+.1f}% {DIM}(tuned normal){RESET}")
    print(f"    Boost deviation at idle:     {avg_boost_dev:+.1f}%")
    print(f"    Captured at:                 {avg_rpm:.0f} rpm, {avg_coolant:.0f}°C coolant")
    print(f"\n    {DIM}Health check will warn if air mass drops below "
          f"{profile['thresholds']['air_mass_warn_pct']:+.1f}%{RESET}")
    print(f"    {DIM}(that's 8% worse than your tuned baseline){RESET}")

    print(f"\n  {BOLD}Save this baseline?{RESET}")
    answer = input(f"  Type 'yes' to confirm: ").strip().lower()
    if answer != "yes":
        print("  Cancelled.")
        return

    save_baseline(profile)
    print(f"\n  {GREEN}Baseline saved to {os.path.normpath(BASELINE_FILE)}{RESET}")
    print(f"  {DIM}Health checks will now use this as the reference.{RESET}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_sensors(ecu, sgbd):
    """Read and display all sensors, grouped by system."""
    print_header("LIVE SENSOR DATA")

    groups = [
        ("Engine", SENSORS_ENGINE),
        ("Temperatures", SENSORS_TEMPS),
        ("Boost / Turbo", SENSORS_BOOST),
        ("Air Mass", SENSORS_AIR),
        ("Fuel System", SENSORS_FUEL),
        ("Other", SENSORS_OTHER),
    ]
    for group_name, sensors in groups:
        print_subheader(group_name)
        for job, wert, unit, desc in sensors:
            val = read_sensor(ecu, sgbd, job, wert)
            status = status_color(val, desc, unit)
            print(f"    {desc:30s}  {format_value(val, unit):>25s}{status}")


def cmd_faults(ecu, sgbd):
    """Read and display fault codes with English translation."""
    print_header("FAULT CODES (DTC)")

    # Active faults
    try:
        faults = ecu.read_faults(sgbd)
    except EdiabasError as e:
        print(f"  {RED}Error reading faults: {e}{RESET}")
        return

    if not faults:
        print(f"  {GREEN}No active fault codes stored.{RESET}")
    else:
        print(f"  {YELLOW}{len(faults)} active fault(s):{RESET}\n")
        for i, f in enumerate(faults, 1):
            code = f.get("F_ORT_NR", "?")
            location = f.get("F_ORT_TEXT", "Unknown")
            symptom = f.get("F_SYMPTOM_TEXT", "Unknown")
            warning = f.get("F_WARNUNG_TEXT", "")
            env = f.get("F_UW_TEXT", "")

            loc_en = translate_german(location)
            sym_en = translate_german(symptom)
            warn_en = translate_german(warning) if warning else ""
            env_en = translate_german(env) if env else ""

            print(f"  {BOLD}[{i}] DTC {code}{RESET}")
            print(f"      Location: {loc_en}")
            if loc_en != location:
                print(f"      {DIM}({location}){RESET}")
            print(f"      Symptom:  {sym_en}")
            if sym_en != symptom:
                print(f"      {DIM}({symptom}){RESET}")
            if warn_en:
                color = RED if "would trigger" in warn_en.lower() else YELLOW
                print(f"      Severity: {color}{warn_en}{RESET}")
            if env_en:
                print(f"      Context:  {env_en}")
            print()

    # Shadow / info faults
    print_subheader("Info Memory (Shadow Faults)")
    try:
        results = ecu.run_job(sgbd, "IS_LESEN")
        shadow = [r for r in results[1:] if "F_ORT_NR" in r]
        if not shadow:
            print(f"    {GREEN}No shadow faults.{RESET}")
        else:
            print(f"    {len(shadow)} shadow fault(s) (cleared but remembered):\n")
            for i, f in enumerate(shadow, 1):
                code = f.get("F_ORT_NR", "?")
                location = translate_german(f.get("F_ORT_TEXT", "Unknown"))
                print(f"    [{i}] DTC {code} — {location}")
    except EdiabasError:
        print(f"    {DIM}Could not read info memory.{RESET}")


def cmd_injectors(ecu, sgbd):
    """Deep injector analysis with per-cylinder health assessment."""
    print_header("INJECTOR DIAGNOSTICS")

    # --- IMA Matching Codes ---
    print_subheader("IMA Matching Codes")
    ima_codes = {}
    try:
        results = ecu.run_job(sgbd, "ABGLEICH_IMA_LESEN")
        if len(results) > 1:
            for key, val in sorted(results[1].items()):
                if "IMA_WERT_ZYL" in key:
                    cyl = key.split("ZYL")[1].split("_")[0]
                    ima_codes[cyl] = val
                    print(f"    Cylinder {cyl}: {BOLD}{val}{RESET}")
            if not ima_codes:
                print(f"    {DIM}No IMA codes returned.{RESET}")
    except EdiabasError as e:
        print(f"    {RED}Error: {e}{RESET}")

    # --- NMK Learned Corrections (Nadelmengenkorrektur) ---
    print_subheader("Learned Injector Corrections (NMK)")
    print(f"    {DIM}ECU-learned fuel quantity corrections per cylinder at 3 rail pressures.{RESET}")
    print(f"    {DIM}Lower values = newer/better injector. High values = wear compensation.{RESET}\n")
    nmk_data = {}  # {cyl: {pressure: value}}
    try:
        results = ecu.run_job(sgbd, "ABGLEICH_NMK_LESEN")
        if len(results) > 1:
            for key, val in sorted(results[1].items()):
                if "NMK_ZYL" in key and "WERT" in key and isinstance(val, (int, float)):
                    # e.g. STAT_NMK_ZYL1_400BAR_WERT
                    parts = key.split("_")
                    cyl = None
                    pressure = None
                    for p in parts:
                        if p.startswith("ZYL"):
                            cyl = p[3:]
                        if "BAR" in p:
                            pressure = p
                    if cyl and pressure:
                        nmk_data.setdefault(cyl, {})[pressure] = val

            if nmk_data:
                pressures = ["400BAR", "700BAR", "1000BAR"]
                header = f"    {'Cylinder':>12s}"
                for p in pressures:
                    header += f"  {p:>8s}"
                print(header)
                print(f"    {'':>12s}  {'--------':>8s}  {'--------':>8s}  {'--------':>8s}")
                for cyl in sorted(nmk_data.keys()):
                    vals = nmk_data[cyl]
                    total = sum(abs(vals.get(p, 0)) for p in pressures)
                    if total > 80:
                        color = RED
                    elif total > 40:
                        color = YELLOW
                    else:
                        color = GREEN
                    line = f"    {color}{'Cyl ' + cyl:>12s}"
                    for p in pressures:
                        v = vals.get(p, 0)
                        line += f"  {v:>+8.1f}"
                    line += RESET
                    print(line)

                # Learning status
                learn_400 = results[1].get("STAT_NMK_CTLRN_400BAR_WERT")
                learn_700 = results[1].get("STAT_NMK_CTLRN_700BAR_WERT")
                freigabe = results[1].get("STAT_NMK_FREIGABE_TEXT", "")
                if learn_400 is not None:
                    print(f"\n    {DIM}Learning cycles: {learn_400:.0f} (400 bar), "
                          f"{learn_700:.0f} (700 bar){RESET}")
                if freigabe:
                    print(f"    {DIM}Enabled for cylinders: {freigabe}{RESET}")
    except EdiabasError as e:
        print(f"    {DIM}Not available: {e}{RESET}")

    # --- Idle Roughness (RPM deviation) ---
    print_subheader("Idle Roughness — RPM Deviation")
    print(f"    {DIM}Relative deviation between cylinders (5 values for 6 cylinders).{RESET}")
    print(f"    {DIM}Good: < 3 rpm | Marginal: 3-8 rpm | Bad: > 8 rpm{RESET}\n")
    rpm_devs = {}
    roughness_inactive = False
    try:
        results = ecu.run_job(sgbd, "STATUS_LAUFUNRUHE_DREHZAHL")
        if len(results) > 1:
            for key, val in sorted(results[1].items()):
                if "ZYL" in key and "WERT" in key and isinstance(val, (int, float)):
                    cyl = key.split("ZYL")[1].split("_")[0]
                    rpm_devs[cyl] = val
                    absval = abs(val)
                    if absval > 8:
                        status = f"{RED}[BAD]{RESET}  needs attention"
                    elif absval > 3:
                        status = f"{YELLOW}[WARN]{RESET} elevated"
                    else:
                        status = f"{GREEN}[OK]{RESET}"
                    sign = "+" if val > 0 else ""
                    print(f"    Cylinder {cyl}: {sign}{val:7.2f} rpm    {status}")

            # Detect inactive roughness controller (all zeros = not running)
            if rpm_devs and all(v == 0.0 for v in rpm_devs.values()):
                roughness_inactive = True
                print(f"\n    {YELLOW}All values zero — roughness controller not active.{RESET}")
                print(f"    {DIM}Requires engine at operating temp (~85°C+) and stable idle.{RESET}")
    except EdiabasError as e:
        print(f"    {RED}Error: {e}{RESET}")

    if rpm_devs and not roughness_inactive:
        vals = list(rpm_devs.values())
        mean_dev = sum(abs(v) for v in vals) / len(vals)
        worst = max(vals, key=abs)
        worst_cyl = [c for c, v in rpm_devs.items() if v == worst][0]
        print(f"\n    Mean |deviation|: {mean_dev:.2f} rpm")
        print(f"    Worst cylinder:  {worst_cyl} ({worst:+.2f} rpm)")

    # --- Fuel Quantity Correction ---
    print_subheader("Fuel Quantity Correction (mg/stroke)")
    print(f"    {DIM}How much extra fuel each cylinder needs for smooth idle.{RESET}")
    print(f"    {DIM}Good: < 1.5 mg | Marginal: 1.5-3.0 mg | Bad: > 3.0 mg{RESET}\n")
    fuel_corr = {}
    fuel_corr_inactive = False
    try:
        results = ecu.run_job(sgbd, "STATUS_LAUFUNRUHE_LLR_MENGE")
        if len(results) > 1:
            for key, val in sorted(results[1].items()):
                if "ZYL" in key and "WERT" in key and isinstance(val, (int, float)):
                    cyl = key.split("ZYL")[1].split("_")[0]
                    fuel_corr[cyl] = val
                    absval = abs(val)
                    if absval > 3.0:
                        status = f"{RED}[BAD]{RESET}  high correction"
                    elif absval > 1.5:
                        status = f"{YELLOW}[WARN]{RESET} above normal"
                    else:
                        status = f"{GREEN}[OK]{RESET}"
                    sign = "+" if val > 0 else ""
                    print(f"    Cylinder {cyl}: {sign}{val:7.3f} mg     {status}")

            # Detect inactive (all identical = default/not running)
            if fuel_corr:
                vals = list(fuel_corr.values())
                if len(set(f"{v:.6f}" for v in vals)) == 1:
                    fuel_corr_inactive = True
                    print(f"\n    {YELLOW}All values identical — correction not active.{RESET}")
                    print(f"    {DIM}Requires engine at operating temp (~85°C+) and stable idle.{RESET}")
    except EdiabasError as e:
        print(f"    {RED}Error: {e}{RESET}")

    if fuel_corr and not fuel_corr_inactive:
        vals = list(fuel_corr.values())
        worst = max(vals, key=abs)
        worst_cyl = [c for c, v in fuel_corr.items() if v == worst][0]
        print(f"\n    Max correction:  {abs(worst):.3f} mg (Cyl {worst_cyl})")

    # --- Injector Offset Values ---
    print_subheader("Injector Offset Values")
    try:
        results = ecu.run_job(sgbd, "STATUS_OFFSETWERTE")
        if len(results) > 1:
            has_data = False
            for key, val in sorted(results[1].items()):
                if key == "JOB_STATUS":
                    continue
                has_data = True
                label = key.replace("STAT_", "").replace("_WERT", "")
                print(f"    {label:40s}  {val}")
            if not has_data:
                print(f"    {DIM}No offset data returned.{RESET}")
        else:
            print(f"    {DIM}No offset data returned.{RESET}")
    except EdiabasError as e:
        print(f"    {DIM}Not available: {e}{RESET}")

    # --- Injector Offset Learning ---
    print_subheader("Injector Offset Learning")
    try:
        results = ecu.run_job(sgbd, "STATUS_OFFSETLERNEN")
        if len(results) > 1:
            for key, val in sorted(results[1].items()):
                if key == "JOB_STATUS":
                    continue
                label = key.replace("STAT_", "").replace("_WERT", "")
                print(f"    {label:40s}  {val}")
        else:
            print(f"    {DIM}No offset learning data returned.{RESET}")
    except EdiabasError as e:
        print(f"    {DIM}Not available: {e}{RESET}")

    # --- Usage Counter ---
    print_subheader("Injector Usage Counter")
    try:
        results = ecu.run_job(sgbd, "STATUS_INJEKTORTAUSCH")
        if len(results) > 1:
            info = results[1].get("STAT_INJEKTORTAUSCH_INFO", None)
            if info:
                info_str = str(info)
                if "niO" in info_str or "Reset" in info_str:
                    print(f"    Status: {YELLOW}Counter needs reset{RESET}")
                    print(f"    {DIM}Should be reset after injector replacement.{RESET}")
                    print(f"    {DIM}Reset via ABGLEICH_NMK_SCHREIBEN:{RESET}")
                    print(f"    {DIM}  Single cyl: INJSINGLE + NUM_ZYL={RESET}")
                    print(f"    {DIM}  All cyls:   INJALL{RESET}")
                elif "iO" in info_str:
                    print(f"    Status: {GREEN}OK — counters current{RESET}")
                else:
                    print(f"    Status: {info}")
    except EdiabasError as e:
        print(f"    {DIM}Not available: {e}{RESET}")

    # --- Per-Cylinder Assessment ---
    print_subheader("Per-Cylinder Assessment")
    if roughness_inactive and fuel_corr_inactive:
        print(f"    {YELLOW}Roughness controller not active — engine not at operating temp.{RESET}")
        print(f"    {DIM}Run again when coolant is above 85°C for live per-cylinder data.{RESET}")
        if nmk_data:
            print(f"\n    {DIM}NMK learned corrections (from ECU memory) are shown above.{RESET}")
    elif rpm_devs or fuel_corr:
        all_cyls = sorted(set(rpm_devs.keys()) | set(fuel_corr.keys()))
        for cyl in all_cyls:
            rpm = abs(rpm_devs.get(cyl, 0))
            fuel = abs(fuel_corr.get(cyl, 0))
            if rpm > 8 or fuel > 3.0:
                verdict = f"{RED}ATTENTION{RESET} — may need injector service/replacement"
            elif rpm > 3 or fuel > 1.5:
                verdict = f"{YELLOW}MARGINAL{RESET} — monitor closely"
            else:
                verdict = f"{GREEN}GOOD{RESET} — stable idle, minimal correction"
            print(f"    Cylinder {cyl}: {verdict}")
            print(f"      {DIM}roughness: {rpm:.2f} rpm, fuel correction: {fuel:.3f} mg{RESET}")


def cmd_turbo(ecu, sgbd):
    """Boost/turbo system analysis with actual vs target comparison."""
    print_header("TURBO / BOOST SYSTEM")

    # Read all boost-related values
    boost_act = read_sensor(ecu, sgbd, "STATUS_LADEDRUCK_IST", "STAT_LADEDRUCK_IST_WERT")
    boost_tgt = read_sensor(ecu, sgbd, "STATUS_LADEDRUCK_SOLL", "STAT_LADEDRUCK_SOLL_WERT")
    baro = read_sensor(ecu, sgbd, "STATUS_ATMOSPHAERENDRUCK", "STAT_ATMOSPHAERENDRUCK_WERT")
    maf = read_sensor(ecu, sgbd, "STATUS_LMM_MASSE", "STAT_LMM_MASSE_WERT")
    air_act = read_sensor(ecu, sgbd, "STATUS_LUFTMASSE_IST", "STAT_LUFTMASSE_IST_WERT")
    air_tgt = read_sensor(ecu, sgbd, "STATUS_LUFTMASSE_SOLL", "STAT_LUFTMASSE_SOLL_WERT")
    charge_t = read_sensor(ecu, sgbd, "STATUS_LADELUFTTEMPERATUR", "STAT_LADELUFTTEMPERATUR_WERT")
    intake_t = read_sensor(ecu, sgbd, "STATUS_AN_LUFTTEMPERATUR", "STAT_AN_LUFTTEMPERATUR_WERT")
    manifold_t = read_sensor(ecu, sgbd, "STATUS_ANSAUGLUFTTEMPERATUR", "STAT_ANSAUGLUFTTEMPERATUR_WERT")

    print_subheader("Boost Pressure")
    print(f"    Actual:      {format_value(boost_act, 'hPa')}")
    print(f"    Target:      {format_value(boost_tgt, 'hPa')}")
    dev = deviation_str(boost_act, boost_tgt)
    if dev:
        print(f"    Deviation:   {dev}")
    print(f"    Barometric:  {format_value(baro, 'hPa')}")
    if boost_act and baro:
        rel_boost = boost_act - baro
        print(f"    Relative:    {rel_boost:+.0f} hPa above ambient"
              f"{'  (no boost at idle — normal)' if abs(rel_boost) < 50 else ''}")

    print_subheader("Air Mass Flow")
    print(f"    MAF sensor:  {format_value(maf, 'kg/h')}")
    print(f"    Per-stroke actual: {format_value(air_act, 'mg/hub')}")
    print(f"    Per-stroke target: {format_value(air_tgt, 'mg/hub')}")
    dev = deviation_str(air_act, air_tgt)
    if dev:
        print(f"    Deviation:   {dev}")
        if air_act and air_tgt and air_act < air_tgt * 0.85:
            print(f"    {YELLOW}Air mass > 15% below target — check for:{RESET}")
            print(f"    {YELLOW}  - Boost leaks (charge air hose, intercooler){RESET}")
            print(f"    {YELLOW}  - MAF sensor fouling or failure{RESET}")
            print(f"    {YELLOW}  - Air filter restriction / box seal leaks{RESET}")

    print_subheader("Intake Temperatures")
    print(f"    Ambient air:       {format_value(intake_t, 'degC')}")
    print(f"    Charge air (post-IC): {format_value(charge_t, 'degC')}")
    print(f"    Intake manifold:   {format_value(manifold_t, 'degC')}")
    if intake_t and charge_t:
        delta = charge_t - intake_t
        if delta > 30:
            print(f"    {YELLOW}Charge air {delta:.0f}°C above ambient — "
                  f"intercooler may not be cooling efficiently{RESET}")
        elif delta < 5:
            print(f"    {GREEN}Intercooler working well "
                  f"(+{delta:.0f}°C above ambient){RESET}")


def cmd_fuel(ecu, sgbd):
    """Fuel system analysis."""
    print_header("FUEL SYSTEM")

    rail_act = read_sensor(ecu, sgbd, "STATUS_RAILDRUCK_IST", "STAT_RAILDRUCK_IST_WERT")
    rail_tgt = read_sensor(ecu, sgbd, "STATUS_RAILDRUCK_SOLL", "STAT_RAILDRUCK_SOLL_WERT")
    fuel_temp = read_sensor(ecu, sgbd, "STATUS_KRAFTSTOFFTEMPERATUR", "STAT_KRAFTSTOFFTEMPERATUR_WERT")

    print_subheader("Common Rail Pressure")
    print(f"    Actual:    {format_value(rail_act, 'bar')}")
    print(f"    Target:    {format_value(rail_tgt, 'bar')}")
    dev = deviation_str(rail_act, rail_tgt)
    if dev:
        print(f"    Deviation: {dev}")

    if rail_act and rail_tgt:
        pct = abs(rail_act - rail_tgt) / rail_tgt * 100
        if pct > 10:
            print(f"    {RED}Rail pressure deviation > 10% — possible fuel system issue{RESET}")
            print(f"    {RED}  Check: high-pressure pump, rail pressure sensor, injector leak-back{RESET}")
        elif pct > 5:
            print(f"    {YELLOW}Rail pressure slightly off target{RESET}")
        else:
            print(f"    {GREEN}Rail pressure tracking target well{RESET}")

    if rail_act:
        if rail_act < 200:
            print(f"    {RED}Rail pressure very low — engine may not start reliably{RESET}")
        elif rail_act < 250:
            print(f"    {YELLOW}Rail pressure below normal idle range (250-400 bar){RESET}")

    print_subheader("Fuel Temperature")
    print(f"    Fuel temp: {format_value(fuel_temp, 'degC')}")
    if fuel_temp:
        if fuel_temp > 60:
            print(f"    {YELLOW}Fuel temperature elevated — reduced fuel density{RESET}")
        elif fuel_temp > 80:
            print(f"    {RED}Fuel temperature high — may affect injector cooling{RESET}")


def cmd_cooling(ecu, sgbd):
    """Cooling system analysis with thermostat monitoring."""
    print_header("COOLING SYSTEM")

    coolant = read_sensor(ecu, sgbd, "STATUS_KUEHLMITTELTEMPERATUR", "STAT_KUEHLMITTELTEMPERATUR_WERT")
    oil = read_sensor(ecu, sgbd, "STATUS_MOTORTEMPERATUR", "STAT_MOTORTEMPERATUR_WERT")
    ambient = read_sensor(ecu, sgbd, "STATUS_UMGEBUNGSTEMPERATUR", "STAT_UMGEBUNGSTEMPERATUR_WERT")

    print_subheader("Current Temperatures")
    print(f"    Coolant:     {format_value(coolant, 'degC')}"
          f"{status_color(coolant, 'Coolant Temp')}")
    print(f"    Engine/Oil:  {format_value(oil, 'degC')}"
          f"{status_color(oil, 'Engine/Oil Temp')}")
    print(f"    Ambient:     {format_value(ambient, 'degC')}")

    if coolant and oil:
        delta = abs(coolant - oil)
        print(f"\n    Coolant-Oil delta: {delta:.1f}°C", end="")
        if delta > 20:
            print(f"  {YELLOW}(large gap — possible sensor or thermostat issue){RESET}")
        else:
            print(f"  {GREEN}(normal){RESET}")

    # Interpretation
    print_subheader("Assessment")
    if coolant is not None:
        if coolant < 70:
            print(f"    {YELLOW}Coolant not at operating temperature ({coolant:.0f}°C).{RESET}")
            print(f"    M57TU2 normal operating range: 85-100°C.")
            print(f"    If engine has been running > 10 min at this temp:")
            print(f"      - Thermostat may be stuck open")
            print(f"      - Air trapped in cooling system (common on E70)")
            print(f"      - Bleed screw: 8mm hex on thermostat housing")
            print(f"      - Procedure: idle with bleed screw cracked open")
            print(f"        until steady stream of coolant, no bubbles")
        elif coolant < 80:
            print(f"    {YELLOW}Coolant warming up ({coolant:.0f}°C) — "
                  f"may need more time or thermostat check.{RESET}")
        elif coolant <= 105:
            print(f"    {GREEN}Coolant temperature normal ({coolant:.0f}°C).{RESET}")
        elif coolant <= 115:
            print(f"    {YELLOW}Coolant running hot ({coolant:.0f}°C) — "
                  f"monitor closely.{RESET}")
        else:
            print(f"    {RED}COOLANT OVERHEATING ({coolant:.0f}°C) — "
                  f"shut down and investigate!{RESET}")

    # Quick thermostat trend (5 readings over 10 seconds)
    print_subheader("Thermostat Quick Check (5 readings, 10s)")
    temps = []
    try:
        for i in range(5):
            t = read_sensor(ecu, sgbd, "STATUS_KUEHLMITTELTEMPERATUR",
                            "STAT_KUEHLMITTELTEMPERATUR_WERT")
            if t is not None:
                temps.append(t)
                arrow = ""
                if len(temps) > 1:
                    diff = temps[-1] - temps[-2]
                    if diff > 0.3:
                        arrow = f" {GREEN}^{RESET}"
                    elif diff < -0.3:
                        arrow = f" {CYAN}v{RESET}"
                    else:
                        arrow = f" {DIM}={RESET}"
                print(f"    {i*2:2d}s: {t:.1f}°C{arrow}")
            if i < 4:
                time.sleep(2)
    except KeyboardInterrupt:
        pass
    if len(temps) >= 2:
        total_change = temps[-1] - temps[0]
        if abs(total_change) < 0.5:
            print(f"    {DIM}Stable (delta: {total_change:+.1f}°C over 10s){RESET}")
        else:
            direction = "rising" if total_change > 0 else "falling"
            print(f"    Temperature {direction}: {total_change:+.1f}°C over 10s")


def cmd_exhaust(ecu, sgbd):
    """Exhaust system data (DPF, cat, exhaust temps)."""
    print_header("EXHAUST SYSTEM")

    # Exhaust temps
    print_subheader("Exhaust Temperatures")
    for job, wert, desc in [
        ("STATUS_ABGASTEMPERATUR_KAT", "STAT_ABGASTEMPERATUR_KAT_WERT", "Before Catalytic Converter"),
        ("STATUS_ABGASTEMPERATUR_CSF", "STAT_ABGASTEMPERATUR_CSF_WERT", "Before DPF"),
    ]:
        val = read_sensor(ecu, sgbd, job, wert)
        if val is not None:
            print(f"    {desc:35s}  {val:.1f} °C")
        else:
            print(f"    {desc:35s}  {DIM}N/A (sensor removed?){RESET}")

    # DPF status
    print_subheader("DPF Status")
    try:
        results = ecu.run_job(sgbd, "STATUS_PARTIKELFILTER_VERBAUT")
        if len(results) > 1:
            for key, val in sorted(results[1].items()):
                if key != "JOB_STATUS":
                    print(f"    {key}: {val}")
    except EdiabasError:
        print(f"    {DIM}DPF status not available (likely deleted).{RESET}")

    for job, desc in [
        ("STATUS_REGENERATION_CSF", "DPF Regeneration"),
        ("STATUS_RESTLAUFSTRECKE_CSF", "DPF Remaining Distance"),
        ("STATUS_DIFFERENZDRUCK_CSF", "DPF Differential Pressure"),
    ]:
        try:
            results = ecu.run_job(sgbd, job)
            if len(results) > 1:
                print(f"\n    {desc}:")
                for key, val in sorted(results[1].items()):
                    if key != "JOB_STATUS":
                        label = key.replace("STAT_", "").replace("_WERT", "")
                        print(f"      {label}: {val}")
        except EdiabasError:
            print(f"    {desc}: {DIM}N/A{RESET}")


def cmd_service(ecu, sgbd):
    """Service data — CBS, oil level, operating hours, serial number."""
    print_header("SERVICE DATA")

    # ECU serial
    print_subheader("ECU Serial Number")
    try:
        results = ecu.run_job(sgbd, "SERIENNUMMER_LESEN")
        if len(results) > 1:
            for key, val in sorted(results[1].items()):
                if key != "JOB_STATUS":
                    print(f"    {key}: {val}")
    except EdiabasError:
        print(f"    {DIM}Not available.{RESET}")

    # Operating hours
    print_subheader("Operating Statistics")
    hours_val = read_sensor(ecu, sgbd, "STATUS_BETRIEBSSTUNDENZAEHLER",
                            "STAT_BETRIEBSSTUNDENZAEHLER_WERT")
    odo_val = read_sensor(ecu, sgbd, "STATUS_KILOMETERSTAND", "STAT_KILOMETERSTAND_WERT")
    oil_val = read_sensor(ecu, sgbd, "STATUS_OELNIVEAU", "STATUS_OELNIVEAU")

    if hours_val is not None:
        hours = hours_val / 3600
        print(f"    Engine hours:  {hours:,.1f} hours ({hours_val:,.0f} seconds)")
        if odo_val and hours > 0:
            avg_speed = odo_val / hours
            print(f"    Avg speed:     {avg_speed:.1f} km/h ({avg_speed * 0.621371:.1f} mph)")
    else:
        print(f"    Engine hours:  {DIM}Not available{RESET}")

    if odo_val is not None:
        miles = odo_val * 0.621371
        print(f"    Odometer:      {odo_val:,.0f} km ({miles:,.0f} mi)")

    if oil_val is not None:
        if oil_val < 20:
            color = RED
        elif oil_val < 40:
            color = YELLOW
        else:
            color = GREEN
        print(f"    Oil level:     {color}{oil_val:.0f}%{RESET}")
    else:
        print(f"    Oil level:     {DIM}Not available{RESET}")

    # CBS data
    print_subheader("Condition Based Service (CBS)")
    try:
        results = ecu.run_job(sgbd, "CBS_DATEN_LESEN")
        if len(results) > 1:
            for key, val in sorted(results[1].items()):
                if key != "JOB_STATUS":
                    label = key.replace("STAT_", "").replace("CBS_", "")
                    print(f"    {label:40s}  {val}")
        else:
            print(f"    {DIM}No CBS data returned.{RESET}")
    except EdiabasError:
        print(f"    {DIM}CBS data not available.{RESET}")


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
        }
        for key, val in sorted(ident.items()):
            if key == "JOB_STATUS":
                continue
            label = field_names.get(key, key)
            print(f"    {label:30s}  {val}")
    except EdiabasError as e:
        print(f"  {RED}Error: {e}{RESET}")


def cmd_ecu_config(ecu, sgbd):
    """Display ECU component configuration (what's coded in/out)."""
    print_header("ECU COMPONENT CONFIGURATION")
    try:
        results = ecu.run_job(sgbd, "ECU_CONFIG")
        if len(results) > 1:
            cfg = results[1]
            on_items = []
            off_items = []
            for key in sorted(cfg.keys()):
                if key.endswith("_INFO"):
                    base = key.replace("_INFO", "")
                    status = cfg.get(base, "?")
                    info = cfg[key]
                    if info == "-":
                        continue
                    en_name = CONFIG_NAMES.get(base, info)
                    if status == 1:
                        on_items.append((base, en_name))
                    else:
                        off_items.append((base, en_name))

            print(f"\n  {GREEN}Enabled:{RESET}")
            for code, name in on_items:
                print(f"    [{GREEN}ON{RESET} ]  {name} ({code})")
            print(f"\n  {DIM}Disabled / Not Installed:{RESET}")
            for code, name in off_items:
                print(f"    [{DIM}OFF{RESET}]  {name} ({code})")
    except EdiabasError as e:
        print(f"  {RED}Error: {e}{RESET}")


def cmd_health(ecu, sgbd):
    """Quick all-systems health dashboard."""
    print_header("SYSTEM HEALTH CHECK")
    print()

    issues = []

    # Engine
    rpm = read_sensor(ecu, sgbd, "STATUS_MOTORDREHZAHL", "STAT_MOTORDREHZAHL_WERT")
    if rpm is not None:
        if rpm > 100:
            print(f"  {GREEN}[OK]{RESET}   Engine         {rpm:.0f} rpm — running")
        else:
            print(f"  {YELLOW}[--]{RESET}   Engine         Not running (ignition on)")
    else:
        print(f"  {RED}[!!]{RESET}   Engine         No data — check connection")
        issues.append("No ECU communication")

    # Battery
    batt = read_sensor(ecu, sgbd, "STATUS_UBATT", "STAT_UBATT_WERT")
    if batt is not None:
        bv = batt / 1000
        if bv < 11.5:
            print(f"  {RED}[!!]{RESET}   Battery        {bv:.2f}V — LOW")
            issues.append(f"Battery voltage critical ({bv:.1f}V)")
        elif bv < 12.0:
            print(f"  {YELLOW}[!!]{RESET}   Battery        {bv:.2f}V — low")
            issues.append(f"Battery voltage low ({bv:.1f}V)")
        else:
            print(f"  {GREEN}[OK]{RESET}   Battery        {bv:.2f}V")

    # Coolant
    coolant = read_sensor(ecu, sgbd, "STATUS_KUEHLMITTELTEMPERATUR",
                          "STAT_KUEHLMITTELTEMPERATUR_WERT")
    if coolant is not None:
        if coolant > 115:
            print(f"  {RED}[!!]{RESET}   Cooling        {coolant:.0f}°C — OVERHEATING")
            issues.append(f"Coolant overheating ({coolant:.0f}°C)")
        elif coolant > 105:
            print(f"  {YELLOW}[!!]{RESET}   Cooling        {coolant:.0f}°C — running hot")
            issues.append(f"Coolant hot ({coolant:.0f}°C)")
        elif coolant < 70 and rpm and rpm > 100:
            print(f"  {YELLOW}[!!]{RESET}   Cooling        {coolant:.0f}°C — not at operating temp")
            issues.append(f"Coolant cold ({coolant:.0f}°C)")
        else:
            print(f"  {GREEN}[OK]{RESET}   Cooling        {coolant:.0f}°C")

    # Oil
    oil = read_sensor(ecu, sgbd, "STATUS_MOTORTEMPERATUR", "STAT_MOTORTEMPERATUR_WERT")
    oil_level = read_sensor(ecu, sgbd, "STATUS_OELNIVEAU", "STATUS_OELNIVEAU")
    oil_parts = []
    if oil is not None:
        oil_parts.append(f"{oil:.0f}°C")
    if oil_level is not None:
        oil_parts.append(f"level {oil_level:.0f}%")
        if oil_level < 20:
            issues.append(f"Oil level critical ({oil_level:.0f}%)")
    if oil_parts:
        oil_str = ", ".join(oil_parts)
        if oil_level is not None and oil_level < 20:
            print(f"  {RED}[!!]{RESET}   Oil            {oil_str}")
        elif oil_level is not None and oil_level < 40:
            print(f"  {YELLOW}[!!]{RESET}   Oil            {oil_str}")
        else:
            print(f"  {GREEN}[OK]{RESET}   Oil            {oil_str}")
    else:
        print(f"  {DIM}[--]{RESET}   Oil            no data")

    # Turbo / Boost
    boost = read_sensor(ecu, sgbd, "STATUS_LADEDRUCK_IST", "STAT_LADEDRUCK_IST_WERT")
    boost_t = read_sensor(ecu, sgbd, "STATUS_LADEDRUCK_SOLL", "STAT_LADEDRUCK_SOLL_WERT")
    if boost is not None:
        if boost_t and boost_t > 1100:  # under load
            pct = abs(boost - boost_t) / boost_t * 100
            if pct > 15:
                print(f"  {RED}[!!]{RESET}   Turbo          {boost:.0f}/{boost_t:.0f} hPa "
                      f"({pct:.0f}% deviation)")
                issues.append(f"Boost deviation {pct:.0f}%")
            elif pct > 5:
                print(f"  {YELLOW}[!!]{RESET}   Turbo          {boost:.0f}/{boost_t:.0f} hPa")
            else:
                print(f"  {GREEN}[OK]{RESET}   Turbo          {boost:.0f} hPa (on target)")
        else:
            print(f"  {GREEN}[OK]{RESET}   Turbo          {boost:.0f} hPa (idle)")

    # Air mass (baseline-aware)
    air_act = read_sensor(ecu, sgbd, "STATUS_LUFTMASSE_IST", "STAT_LUFTMASSE_IST_WERT")
    air_tgt = read_sensor(ecu, sgbd, "STATUS_LUFTMASSE_SOLL", "STAT_LUFTMASSE_SOLL_WERT")
    if air_act is not None and air_tgt is not None and air_tgt > 0:
        pct = (air_act - air_tgt) / air_tgt * 100
        baseline = load_baseline()
        if baseline:
            warn_thresh = baseline["thresholds"]["air_mass_warn_pct"]
            crit_thresh = baseline["thresholds"]["air_mass_crit_pct"]
            base_dev = baseline["idle_air_deviation_pct"]
            if pct < crit_thresh:
                print(f"  {RED}[!!]{RESET}   Air Mass       {pct:+.0f}% from ECU target "
                      f"({pct - base_dev:+.0f}% from baseline)")
                issues.append(f"Air mass {pct - base_dev:.0f}% worse than baseline")
            elif pct < warn_thresh:
                print(f"  {YELLOW}[!!]{RESET}   Air Mass       {pct:+.0f}% from ECU target "
                      f"({pct - base_dev:+.0f}% from baseline)")
                issues.append(f"Air mass {pct - base_dev:.0f}% worse than baseline")
            else:
                print(f"  {GREEN}[OK]{RESET}   Air Mass       {pct:+.0f}% from ECU target "
                      f"{DIM}(normal for tune){RESET}")
        else:
            # No baseline — use stock thresholds
            if pct < -15:
                print(f"  {RED}[!!]{RESET}   Air Mass       {pct:+.0f}% from target")
                issues.append(f"Air mass {pct:.0f}% below target")
            elif pct < -5:
                print(f"  {YELLOW}[!!]{RESET}   Air Mass       {pct:+.0f}% from target")
                issues.append(f"Air mass {pct:.0f}% below target")
            else:
                print(f"  {GREEN}[OK]{RESET}   Air Mass       on target ({pct:+.0f}%)")

    # Fuel
    rail = read_sensor(ecu, sgbd, "STATUS_RAILDRUCK_IST", "STAT_RAILDRUCK_IST_WERT")
    rail_t = read_sensor(ecu, sgbd, "STATUS_RAILDRUCK_SOLL", "STAT_RAILDRUCK_SOLL_WERT")
    if rail is not None:
        if rail_t and abs(rail - rail_t) / max(rail_t, 1) * 100 > 10:
            pct = (rail - rail_t) / rail_t * 100
            print(f"  {YELLOW}[!!]{RESET}   Fuel System    Rail: {rail:.0f}/{rail_t:.0f} bar "
                  f"({pct:+.0f}%)")
            issues.append(f"Rail pressure off target ({pct:+.0f}%)")
        else:
            print(f"  {GREEN}[OK]{RESET}   Fuel System    Rail: {rail:.0f} bar")

    # Injectors (quick check — just mean roughness)
    try:
        results = ecu.run_job(sgbd, "STATUS_LAUFUNRUHE_DREHZAHL")
        if len(results) > 1:
            devs = [abs(v) for k, v in results[1].items()
                    if "ZYL" in k and "WERT" in k and isinstance(v, (int, float))]
            if devs:
                worst = max(devs)
                mean = sum(devs) / len(devs)
                if worst > 8:
                    print(f"  {RED}[!!]{RESET}   Injectors      Worst: {worst:.1f} rpm roughness")
                    issues.append(f"Injector roughness {worst:.1f} rpm")
                elif worst > 3:
                    print(f"  {YELLOW}[!!]{RESET}   Injectors      Worst: {worst:.1f} rpm roughness")
                    issues.append(f"Injector roughness elevated ({worst:.1f} rpm)")
                else:
                    print(f"  {GREEN}[OK]{RESET}   Injectors      Mean: {mean:.1f} rpm (smooth)")
    except EdiabasError:
        print(f"  {DIM}[--]{RESET}   Injectors      could not read")

    # Faults
    try:
        faults = ecu.read_faults(sgbd)
        n = len(faults)
        if n == 0:
            print(f"  {GREEN}[OK]{RESET}   Fault Codes    No stored DTCs")
        else:
            print(f"  {YELLOW}[!!]{RESET}   Fault Codes    {n} DTC(s) stored")
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


# ---------------------------------------------------------------------------
# Transmission (EGS) Commands
# ---------------------------------------------------------------------------
EGS_SGBD = "GS19"

# Shift names for display
UPSHIFT_NAMES = [
    ("GLS_1_2", "1 -> 2"),
    ("GLS_2_3", "2 -> 3"),
    ("GLS_3_4", "3 -> 4"),
    ("GLS_4_5", "4 -> 5"),
    ("GLS_5_6", "5 -> 6"),
]
DOWNSHIFT_NAMES = [
    ("GLS_2_1", "2 -> 1"),
    ("GLS_3_2", "3 -> 2"),
    ("GLS_4_3", "4 -> 3"),
    ("GLS_5_4", "5 -> 4"),
    ("GLS_6_5", "6 -> 5"),
]
GLUE_NAMES = [
    ("GLUE_1_2", "1 -> 2"),
    ("GLUE_2_3", "2 -> 3"),
    ("GLUE_3_4", "3 -> 4"),
    ("GLUE_4_5", "4 -> 5"),
    ("GLUE_5_6", "5 -> 6"),
]
FLARE_NAMES = [
    ("FLARE_2_1", "2 -> 1"),
    ("FLARE_3_2", "3 -> 2"),
    ("FLARE_4_3", "4 -> 3"),
    ("FLARE_5_4", "5 -> 4"),
    ("FLARE_6_5", "6 -> 5"),
]


def _summarize_adaptation_grid(data):
    """Summarize an adaptation value grid: min, max, mean, non-zero count."""
    vals = [v for k, v in data.items()
            if k.startswith("ARRAY_") and isinstance(v, (int, float))]
    if not vals:
        return None
    nonzero = [v for v in vals if v != 0.0]
    return {
        "min": min(vals),
        "max": max(vals),
        "mean": sum(vals) / len(vals),
        "count": len(vals),
        "nonzero": len(nonzero),
        "max_abs": max(abs(v) for v in vals),
    }


def cmd_trans(ecu, sgbd_engine):
    """Transmission (EGS) diagnostics — shift adaptations, faults, status."""
    print_header("TRANSMISSION (EGS) DIAGNOSTICS")

    # --- EGS Identification ---
    print_subheader("EGS Identification")
    try:
        ident = ecu.identify(EGS_SGBD)
        if ident:
            part = ident.get("ID_BMW_NR", "?")
            supplier = ident.get("ID_LIEF_TEXT", "?")
            sw = ident.get("ID_SW_NR_FSV", "?")
            date = ident.get("ID_DATUM", "?")
            print(f"    Part:     {part}")
            print(f"    Supplier: {supplier}")
            print(f"    Software: {sw}")
            print(f"    Date:     {date}")
    except EdiabasError as e:
        print(f"    {RED}Cannot communicate with EGS: {e}{RESET}")
        return

    # --- Current Status ---
    print_subheader("Current Status")
    try:
        gear_r = ecu.run_job(EGS_SGBD, "STATUS_GEAR")
        gear_info = gear_r[1] if len(gear_r) > 1 else {}
        pos = gear_info.get("STAT_SA_TEXT", "?")
        wk = gear_info.get("STAT_WK_TEXT", "?")
        print(f"    Selector:  {pos}")
        print(f"    Converter: {wk}")
    except EdiabasError:
        pass

    try:
        ags_r = ecu.run_job(EGS_SGBD, "STATUS_AGS")
        ags = ags_r[1].get("STAT_SCHALTDIAGRAMM_AGS_TEXT", "?") if len(ags_r) > 1 else "?"
        print(f"    Shift map: {ags}")
    except EdiabasError:
        pass

    # --- EGS Faults ---
    print_subheader("Fault Codes")
    try:
        results = ecu.run_job(EGS_SGBD, "FS_LESEN")
        faults = [s for s in results[1:] if "F_ORT_NR" in s]
        if faults:
            print(f"    {YELLOW}{len(faults)} fault(s):{RESET}")
            for f in faults:
                code = f.get("F_ORT_NR", "?")
                loc = f.get("F_ORT_TEXT", "Unknown")
                print(f"      DTC {code}: {loc}")
        else:
            print(f"    {GREEN}No faults stored.{RESET}")
    except EdiabasError as e:
        print(f"    {RED}Error: {e}{RESET}")

    # --- Shift Adaptation Summary ---
    print_subheader("Shift Adaptation Values")
    print(f"    {DIM}Corrections the EGS has learned for each gear change.{RESET}")
    print(f"    {DIM}Large values = heavy compensation. Threshold ~0.5 is notable.{RESET}\n")

    # Upshifts (clutch apply)
    print(f"    {BOLD}Upshift clutch apply (GLS):{RESET}")
    print(f"    {'Shift':>10s}  {'Min':>7s}  {'Max':>7s}  {'Mean':>7s}  {'Max|v|':>7s}  Status")
    print(f"    {'':>10s}  {'-------':>7s}  {'-------':>7s}  {'-------':>7s}  {'-------':>7s}  ------")
    for suffix, label in UPSHIFT_NAMES:
        job = f"STATUS_ADAPTIONSWERTE_{suffix}"
        try:
            r = ecu.run_job(EGS_SGBD, job)
            if len(r) > 1 and r[1]:
                vals = {k: v for k, v in r[1].items() if k != "JOB_STATUS"}
                s = _summarize_adaptation_grid(vals)
                if s:
                    if s["max_abs"] > 0.7:
                        status = f"{RED}HIGH{RESET}"
                    elif s["max_abs"] > 0.4:
                        status = f"{YELLOW}MODERATE{RESET}"
                    else:
                        status = f"{GREEN}OK{RESET}"
                    print(f"    {label:>10s}  {s['min']:>+7.2f}  {s['max']:>+7.2f}  "
                          f"{s['mean']:>+7.3f}  {s['max_abs']:>7.2f}  {status}")
        except EdiabasError:
            print(f"    {label:>10s}  {'error':>7s}")

    # Downshifts
    print(f"\n    {BOLD}Downshift clutch release (GLS):{RESET}")
    print(f"    {'Shift':>10s}  {'Min':>7s}  {'Max':>7s}  {'Mean':>7s}  {'Max|v|':>7s}  Status")
    print(f"    {'':>10s}  {'-------':>7s}  {'-------':>7s}  {'-------':>7s}  {'-------':>7s}  ------")
    for suffix, label in DOWNSHIFT_NAMES:
        job = f"STATUS_ADAPTIONSWERTE_{suffix}"
        try:
            r = ecu.run_job(EGS_SGBD, job)
            if len(r) > 1 and r[1]:
                vals = {k: v for k, v in r[1].items() if k != "JOB_STATUS"}
                s = _summarize_adaptation_grid(vals)
                if s:
                    if s["max_abs"] > 0.7:
                        status = f"{RED}HIGH{RESET}"
                    elif s["max_abs"] > 0.4:
                        status = f"{YELLOW}MODERATE{RESET}"
                    else:
                        status = f"{GREEN}OK{RESET}"
                    print(f"    {label:>10s}  {s['min']:>+7.2f}  {s['max']:>+7.2f}  "
                          f"{s['mean']:>+7.3f}  {s['max_abs']:>7.2f}  {status}")
        except EdiabasError:
            print(f"    {label:>10s}  {'error':>7s}")

    # GLUE (upshift overlap timing)
    print(f"\n    {BOLD}Upshift overlap timing (GLUE):{RESET}")
    print(f"    {'Shift':>10s}  {'Min':>7s}  {'Max':>7s}  {'Mean':>7s}  {'Max|v|':>7s}  Status")
    print(f"    {'':>10s}  {'-------':>7s}  {'-------':>7s}  {'-------':>7s}  {'-------':>7s}  ------")
    for suffix, label in GLUE_NAMES:
        job = f"STATUS_ADAPTIONSWERTE_{suffix}"
        try:
            r = ecu.run_job(EGS_SGBD, job)
            if len(r) > 1 and r[1]:
                vals = {k: v for k, v in r[1].items() if k != "JOB_STATUS"}
                s = _summarize_adaptation_grid(vals)
                if s:
                    if s["max_abs"] > 0.7:
                        status = f"{RED}HIGH{RESET}"
                    elif s["max_abs"] > 0.4:
                        status = f"{YELLOW}MODERATE{RESET}"
                    else:
                        status = f"{GREEN}OK{RESET}"
                    print(f"    {label:>10s}  {s['min']:>+7.2f}  {s['max']:>+7.2f}  "
                          f"{s['mean']:>+7.3f}  {s['max_abs']:>7.2f}  {status}")
        except EdiabasError:
            print(f"    {label:>10s}  {'error':>7s}")

    # --- Quick Fill and Converter ---
    print_subheader("Other Adaptations")
    try:
        r = ecu.run_job(EGS_SGBD, "STATUS_ADAPTIONSWERTE_SBC")
        if len(r) > 1:
            sbc = r[1].get("SBC_1_WERT")
            if sbc is not None:
                print(f"    Quick-fill correction (SBC):  {sbc:+.1f}")
    except EdiabasError:
        pass

    try:
        r = ecu.run_job(EGS_SGBD, "STATUS_ADAPTIONSWERTE_PFN")
        if len(r) > 1:
            pfn_vals = {k: v for k, v in r[1].items()
                        if k.startswith("PFN_") and k.endswith("_WERT")}
            if pfn_vals:
                vals_str = ", ".join(f"{v:.0f}" for _, v in sorted(pfn_vals.items()))
                print(f"    Pressure regulation (PFN):    [{vals_str}]")
    except EdiabasError:
        pass

    # --- Assessment ---
    print_subheader("Assessment")
    issues = []

    # Check upshifts for high corrections
    for suffix, label in UPSHIFT_NAMES:
        job = f"STATUS_ADAPTIONSWERTE_{suffix}"
        try:
            r = ecu.run_job(EGS_SGBD, job)
            if len(r) > 1 and r[1]:
                vals = {k: v for k, v in r[1].items() if k != "JOB_STATUS"}
                s = _summarize_adaptation_grid(vals)
                if s and s["max_abs"] > 0.5:
                    issues.append(f"Upshift {label}: corrections up to "
                                  f"{s['max_abs']:.2f} (range {s['min']:+.2f} to {s['max']:+.2f})")
        except EdiabasError:
            pass

    # Check downshifts
    for suffix, label in DOWNSHIFT_NAMES:
        job = f"STATUS_ADAPTIONSWERTE_{suffix}"
        try:
            r = ecu.run_job(EGS_SGBD, job)
            if len(r) > 1 and r[1]:
                vals = {k: v for k, v in r[1].items() if k != "JOB_STATUS"}
                s = _summarize_adaptation_grid(vals)
                if s and s["max_abs"] > 0.5:
                    issues.append(f"Downshift {label}: corrections up to "
                                  f"{s['max_abs']:.2f} (range {s['min']:+.2f} to {s['max']:+.2f})")
        except EdiabasError:
            pass

    if issues:
        print(f"    {YELLOW}Shift adaptations show significant corrections:{RESET}")
        for issue in issues:
            print(f"      - {issue}")
        print(f"\n    {DIM}Consider:{RESET}")
        print(f"    {DIM}  - Reset adaptations: --reset-trans{RESET}")
        print(f"    {DIM}  - Check trans fluid condition and level{RESET}")
        print(f"    {DIM}  - Re-learn takes 50-100 shift cycles of normal driving{RESET}")
    else:
        print(f"    {GREEN}Shift adaptations within normal range.{RESET}")


def cmd_reset_trans(ecu, sgbd_engine):
    """Reset all EGS transmission shift adaptations."""
    print_header("RESET TRANSMISSION ADAPTATIONS")

    # Show current state summary
    print_subheader("Current Adaptation Summary")
    print(f"    {'Shift':>10s}  {'Max|v|':>7s}  Status")
    print(f"    {'':>10s}  {'-------':>7s}  ------")
    for suffix, label in UPSHIFT_NAMES + DOWNSHIFT_NAMES:
        job = f"STATUS_ADAPTIONSWERTE_{suffix}"
        try:
            r = ecu.run_job(EGS_SGBD, job)
            if len(r) > 1 and r[1]:
                vals = {k: v for k, v in r[1].items() if k != "JOB_STATUS"}
                s = _summarize_adaptation_grid(vals)
                if s:
                    if s["max_abs"] > 0.7:
                        status = f"{RED}HIGH{RESET}"
                    elif s["max_abs"] > 0.4:
                        status = f"{YELLOW}MODERATE{RESET}"
                    else:
                        status = f"{GREEN}OK{RESET}"
                    print(f"    {label:>10s}  {s['max_abs']:>7.2f}  {status}")
        except EdiabasError:
            pass

    print(f"\n  Operation: {BOLD}Reset ALL shift adaptations to zero{RESET}")
    print(f"  {DIM}The EGS will re-learn all shift timing and pressure from scratch.{RESET}")
    print(f"  {DIM}Expect slightly rough shifts for the first 50-100 shift cycles.{RESET}")
    print(f"  {DIM}Drive normally — highway + city mix is ideal for fast re-learning.{RESET}")

    print(f"\n  {BOLD}Reset transmission adaptations?{RESET}")
    answer = input(f"  Type 'yes' to confirm: ").strip().lower()
    if answer != "yes":
        print("  Cancelled.")
        return

    try:
        print(f"\n  {DIM}Running STEUERN_ADAPTIONSWERTE_RUECKSETZEN...{RESET}", end=" ", flush=True)
        ecu.run_job(EGS_SGBD, "STEUERN_ADAPTIONSWERTE_RUECKSETZEN")
        print(f"{GREEN}OK{RESET}")
    except EdiabasError as e:
        print(f"{RED}Error: {e}{RESET}")
        return

    # Verify
    print()
    print_subheader("Verification")
    all_zero = True
    for suffix, label in UPSHIFT_NAMES:
        job = f"STATUS_ADAPTIONSWERTE_{suffix}"
        try:
            r = ecu.run_job(EGS_SGBD, job)
            if len(r) > 1 and r[1]:
                vals = {k: v for k, v in r[1].items() if k != "JOB_STATUS"}
                s = _summarize_adaptation_grid(vals)
                if s:
                    if s["max_abs"] > 0.01:
                        all_zero = False
                    print(f"    {label:>10s}  max |val| = {s['max_abs']:.2f}")
        except EdiabasError:
            pass

    if all_zero:
        print(f"\n    {GREEN}All shift adaptations reset to zero.{RESET}")
    else:
        print(f"\n    {YELLOW}Some values may not have cleared — try cycling ignition.{RESET}")

    print(f"\n  {DIM}Drive normally for 50-100 shift cycles to re-learn.{RESET}")


def cmd_monitor(ecu, sgbd, duration=60, csv_file=None):
    """Continuous sensor monitoring with trends."""
    print_header(f"MONITORING ({duration}s)")

    monitor_sensors = [
        ("STATUS_MOTORDREHZAHL", "STAT_MOTORDREHZAHL_WERT", "RPM", "{:>6.0f}", ""),
        ("STATUS_KUEHLMITTELTEMPERATUR", "STAT_KUEHLMITTELTEMPERATUR_WERT",
         "Cool", "{:>5.1f}C", "°C"),
        ("STATUS_MOTORTEMPERATUR", "STAT_MOTORTEMPERATUR_WERT",
         "Oil", "{:>5.1f}C", "°C"),
        ("STATUS_LADEDRUCK_IST", "STAT_LADEDRUCK_IST_WERT",
         "Boost", "{:>5.0f}hPa", "hPa"),
        ("STATUS_LMM_MASSE", "STAT_LMM_MASSE_WERT",
         "MAF", "{:>6.1f}kg/h", "kg/h"),
        ("STATUS_RAILDRUCK_IST", "STAT_RAILDRUCK_IST_WERT",
         "Rail", "{:>5.0f}bar", "bar"),
        ("STATUS_UBATT", "STAT_UBATT_WERT",
         "Batt", "{:>5.2f}V", "V"),
    ]

    # Print header
    hdr = f"  {'Time':>5s}"
    for _, _, label, _, _ in monitor_sensors:
        hdr += f"  {label:>9s}"
    print(hdr)
    print(f"  {'-' * (len(hdr) - 2)}")

    csv_writer = None
    csv_fh = None
    if csv_file:
        csv_fh = open(csv_file, "w", newline="")
        csv_writer = csv.writer(csv_fh)
        csv_writer.writerow(["time_s"] + [s[2] for s in monitor_sensors])

    prev_vals = {}
    start = time.time()
    try:
        while time.time() - start < duration:
            elapsed = time.time() - start
            line = f"  {elapsed:5.0f}s"
            csv_row = [f"{elapsed:.0f}"]

            for job, wert, label, fmt, _ in monitor_sensors:
                val = read_sensor(ecu, sgbd, job, wert)
                if val is not None:
                    # Battery: mV -> V for display
                    display_val = val / 1000 if label == "Batt" else val
                    formatted = fmt.format(display_val)

                    # Trend arrow
                    prev = prev_vals.get(label)
                    if prev is not None:
                        diff = display_val - prev
                        threshold = 0.5 if label in ("Cool", "Oil") else (
                            10 if label in ("RPM", "Boost", "Rail") else 0.3)
                        if diff > threshold:
                            arrow = f"{GREEN}^{RESET}"
                        elif diff < -threshold:
                            arrow = f"{CYAN}v{RESET}"
                        else:
                            arrow = " "
                    else:
                        arrow = " "
                    prev_vals[label] = display_val
                    line += f"  {formatted}{arrow}"
                    csv_row.append(f"{display_val:.4f}")
                else:
                    line += f"  {'n/a':>9s} "
                    csv_row.append("")

            print(line)
            if csv_writer:
                csv_writer.writerow(csv_row)

            time.sleep(2)
    except KeyboardInterrupt:
        print(f"\n  {DIM}Monitoring stopped.{RESET}")
    finally:
        if csv_fh:
            csv_fh.close()
            print(f"\n  CSV saved to: {csv_file}")


def cmd_clear_faults(ecu, sgbd):
    """Clear all fault codes (with confirmation)."""
    print_header("CLEAR FAULT CODES")

    # Show current faults first
    try:
        faults = ecu.read_faults(sgbd)
    except EdiabasError as e:
        print(f"  {RED}Error reading faults: {e}{RESET}")
        return

    if not faults:
        print(f"  {GREEN}No fault codes stored — nothing to clear.{RESET}")
        return

    print(f"  Currently {YELLOW}{len(faults)}{RESET} fault(s) stored:")
    for i, f in enumerate(faults, 1):
        code = f.get("F_ORT_NR", "?")
        location = translate_german(f.get("F_ORT_TEXT", "Unknown"))
        print(f"    [{i}] DTC {code} — {location}")

    print(f"\n  {BOLD}Clear all fault codes?{RESET}")
    answer = input(f"  Type 'yes' to confirm: ").strip().lower()
    if answer != "yes":
        print("  Cancelled.")
        return

    try:
        ecu.run_job(sgbd, "FS_LOESCHEN")
        print(f"\n  {GREEN}Fault codes cleared.{RESET}")
        # Verify
        time.sleep(1)
        faults = ecu.read_faults(sgbd)
        if not faults:
            print(f"  {GREEN}Verified: no faults stored.{RESET}")
        else:
            print(f"  {YELLOW}Note: {len(faults)} fault(s) returned immediately — "
                  f"these are active/persistent faults.{RESET}")
    except EdiabasError as e:
        print(f"  {RED}Error clearing faults: {e}{RESET}")


def cmd_reset_injector(ecu, sgbd, cylinders=None):
    """Reset injector NMK learned corrections (Nadelmengenkorrektur).

    After replacing an injector, the ECU's learned fuel quantity corrections
    must be reset so it re-learns from scratch. The ECU will re-adapt during
    normal driving (takes ~100-300 km of mixed driving to fully converge).

    cylinders: list of cylinder numbers to reset, or None for all.
    """
    print_header("RESET INJECTOR ADAPTATIONS (NMK)")

    # Show current NMK values first
    print_subheader("Current Learned Corrections")
    try:
        results = ecu.run_job(sgbd, "ABGLEICH_NMK_LESEN")
        if len(results) > 1:
            pressures = ["400BAR", "700BAR", "1000BAR"]
            nmk_data = {}
            for key, val in sorted(results[1].items()):
                if "NMK_ZYL" in key and "WERT" in key and isinstance(val, (int, float)):
                    parts = key.split("_")
                    cyl = None
                    pressure = None
                    for p in parts:
                        if p.startswith("ZYL"):
                            cyl = p[3:]
                        if "BAR" in p:
                            pressure = p
                    if cyl and pressure:
                        nmk_data.setdefault(cyl, {})[pressure] = val

            if nmk_data:
                header = f"    {'Cylinder':>12s}"
                for p in pressures:
                    header += f"  {p:>8s}"
                print(header)
                for cyl in sorted(nmk_data.keys()):
                    vals = nmk_data[cyl]
                    mark = " <--" if cylinders and int(cyl) in cylinders else ""
                    line = f"    {'Cyl ' + cyl:>12s}"
                    for p in pressures:
                        v = vals.get(p, 0)
                        line += f"  {v:>+8.1f}"
                    line += f"  {YELLOW}{mark}{RESET}" if mark else ""
                    print(line)

            learn_400 = results[1].get("STAT_NMK_CTLRN_400BAR_WERT")
            learn_700 = results[1].get("STAT_NMK_CTLRN_700BAR_WERT")
            if learn_400 is not None:
                print(f"\n    {DIM}Learning cycles: {learn_400:.0f} (400 bar), "
                      f"{learn_700:.0f} (700 bar){RESET}")
    except EdiabasError as e:
        print(f"    {RED}Error reading NMK: {e}{RESET}")
        return

    # Describe the operation
    if cylinders:
        cyl_str = ", ".join(str(c) for c in sorted(cylinders))
        print(f"\n  Operation: Reset NMK corrections for cylinder(s) {BOLD}{cyl_str}{RESET}")
        print(f"  {DIM}The ECU will re-learn fuel corrections for these cylinders during driving.{RESET}")
    else:
        print(f"\n  Operation: Reset NMK corrections for {BOLD}ALL cylinders{RESET}")
        print(f"  {DIM}The ECU will re-learn all fuel corrections from scratch during driving.{RESET}")

    print(f"  {DIM}Re-learning takes ~100-300 km of mixed driving to fully converge.{RESET}")

    # Confirmation
    print(f"\n  {BOLD}Reset injector adaptations?{RESET}")
    answer = input(f"  Type 'yes' to confirm: ").strip().lower()
    if answer != "yes":
        print("  Cancelled.")
        return

    # Execute reset
    try:
        if cylinders and len(cylinders) == 1:
            cyl_num = cylinders[0]
            params = f"INJSINGLE;{cyl_num}"
            print(f"\n  {DIM}Running ABGLEICH_NMK_SCHREIBEN with INJSINGLE, cyl {cyl_num}...{RESET}")
            ecu.run_job(sgbd, "ABGLEICH_NMK_SCHREIBEN", params)
            print(f"  {GREEN}Cylinder {cyl_num} NMK corrections reset.{RESET}")
        elif cylinders:
            # Multiple individual cylinders
            for cyl_num in sorted(cylinders):
                params = f"INJSINGLE;{cyl_num}"
                print(f"  {DIM}Resetting cylinder {cyl_num}...{RESET}", end=" ", flush=True)
                ecu.run_job(sgbd, "ABGLEICH_NMK_SCHREIBEN", params)
                print(f"{GREEN}OK{RESET}")
            print(f"\n  {GREEN}All specified cylinders reset.{RESET}")
        else:
            print(f"\n  {DIM}Running ABGLEICH_NMK_SCHREIBEN with INJALL...{RESET}")
            ecu.run_job(sgbd, "ABGLEICH_NMK_SCHREIBEN", "INJALL")
            print(f"  {GREEN}All cylinder NMK corrections reset.{RESET}")
    except EdiabasError as e:
        print(f"  {RED}Error: {e}{RESET}")
        return

    # Verify
    print()
    print_subheader("Verification — New NMK Values")
    try:
        time.sleep(1)
        results = ecu.run_job(sgbd, "ABGLEICH_NMK_LESEN")
        if len(results) > 1:
            header = f"    {'Cylinder':>12s}"
            for p in pressures:
                header += f"  {p:>8s}"
            print(header)
            for key, val in sorted(results[1].items()):
                if "NMK_ZYL" in key and "WERT" in key:
                    pass  # already parsed format above
            # Re-parse
            nmk_after = {}
            for key, val in sorted(results[1].items()):
                if "NMK_ZYL" in key and "WERT" in key and isinstance(val, (int, float)):
                    parts = key.split("_")
                    cyl = None
                    pressure = None
                    for p in parts:
                        if p.startswith("ZYL"):
                            cyl = p[3:]
                        if "BAR" in p:
                            pressure = p
                    if cyl and pressure:
                        nmk_after.setdefault(cyl, {})[pressure] = val
            for cyl in sorted(nmk_after.keys()):
                vals = nmk_after[cyl]
                line = f"    {'Cyl ' + cyl:>12s}"
                for p in pressures:
                    v = vals.get(p, 0)
                    line += f"  {v:>+8.1f}"
                print(line)
    except EdiabasError as e:
        print(f"    {RED}Error reading verification: {e}{RESET}")

    # Check counter status
    try:
        results = ecu.run_job(sgbd, "STATUS_INJEKTORTAUSCH")
        if len(results) > 1:
            info = results[1].get("STAT_INJEKTORTAUSCH_INFO", "")
            if "niO" in str(info):
                print(f"\n  {YELLOW}Note: Injector usage counter still flagged.{RESET}")
                print(f"  {DIM}This may clear after a drive cycle or ignition cycle.{RESET}")
            elif "iO" in str(info):
                print(f"\n  {GREEN}Injector usage counter: OK{RESET}")
    except EdiabasError:
        pass


def cmd_run_job(ecu, sgbd, job_name, params=""):
    """Run an arbitrary EDIABAS job and display all results."""
    print_header(f"JOB: {job_name}" + (f"({params})" if params else ""))
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
        description="BMW ECU Diagnostic CLI — terminal-based INPA/ISTA alternative",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
{BOLD}System commands:{RESET}
  --sensors          Live sensor data (grouped by system)
  --faults           Fault codes with English translation
  --injectors        Deep injector analysis per cylinder
  --turbo            Boost / turbo system analysis
  --cooling          Cooling system with thermostat check
  --fuel             Fuel system (rail pressure, fuel temp)
  --exhaust          Exhaust / DPF data
  --service          CBS, oil level, engine hours
  --config           ECU component configuration
  --health           Quick all-systems dashboard
  --trans            Transmission shift adaptation diagnostics

{BOLD}Actions:{RESET}
  --monitor [SECS]   Continuous monitoring (default 60s)
  --clear-faults     Clear all fault codes (with confirmation)
  --reset-injector [CYL...]  Reset injector NMK adaptations
  --reset-trans      Reset transmission shift adaptations
  --jobs             List all available ECU jobs
  --job JOB [PARAMS] Run a specific EDIABAS job

{BOLD}Options:{RESET}
  --sgbd SGBD        Override SGBD (default: auto-detect via D_MOTOR)
  --csv FILE         Export monitor data to CSV file

{BOLD}Examples:{RESET}
  python diag.py                          Full diagnostic report
  python diag.py --health                 Quick system check
  python diag.py --injectors              Injector deep-dive
  python diag.py --monitor 120            Monitor for 2 minutes
  python diag.py --monitor 300 --csv log.csv  Monitor with CSV export
  python diag.py --job STATUS_MESSWERTBLOCK_LESEN "JA;CoSCR_st"
        """,
    )
    parser.add_argument("--sgbd", default=None,
                        help="SGBD file name (default: auto-detect via D_MOTOR)")
    parser.add_argument("--sensors", action="store_true", help="Show live sensor data")
    parser.add_argument("--faults", action="store_true", help="Show fault codes")
    parser.add_argument("--injectors", action="store_true", help="Injector diagnostics")
    parser.add_argument("--turbo", action="store_true", help="Turbo/boost analysis")
    parser.add_argument("--cooling", action="store_true", help="Cooling system analysis")
    parser.add_argument("--fuel", action="store_true", help="Fuel system analysis")
    parser.add_argument("--exhaust", action="store_true", help="Exhaust/DPF data")
    parser.add_argument("--service", action="store_true", help="Service data (CBS, oil, hours)")
    parser.add_argument("--config", action="store_true", help="ECU component config")
    parser.add_argument("--health", action="store_true", help="Quick health dashboard")
    parser.add_argument("--monitor", nargs="?", const=60, type=int, metavar="SECS",
                        help="Continuous monitoring (default 60s)")
    parser.add_argument("--clear-faults", action="store_true", help="Clear all fault codes")
    parser.add_argument("--jobs", action="store_true", help="List all available jobs")
    parser.add_argument("--job", nargs="+", metavar=("JOB", "PARAMS"),
                        help="Run a specific job")
    parser.add_argument("--baseline", action="store_true",
                        help="Capture tuned baseline profile at idle")
    parser.add_argument("--trans", action="store_true",
                        help="Transmission (EGS) shift adaptation diagnostics")
    parser.add_argument("--reset-trans", action="store_true",
                        help="Reset transmission shift adaptations")
    parser.add_argument("--reset-injector", nargs="*", type=int, metavar="CYL",
                        help="Reset injector NMK adaptations (specify cylinders or omit for all)")
    parser.add_argument("--csv", metavar="FILE", help="Export monitor data to CSV")
    args = parser.parse_args()

    # Determine mode
    specific = any([args.sensors, args.faults, args.injectors, args.turbo,
                    args.cooling, args.fuel, args.exhaust, args.service,
                    args.config, args.health, args.trans, args.baseline,
                    args.monitor is not None, args.clear_faults, args.reset_trans,
                    args.reset_injector is not None, args.jobs, args.job])
    full_report = not specific

    print(f"{BOLD}bimmerdiag{RESET} — BMW ECU Diagnostic Tool")
    print(f"{DIM}github.com/madhakish/bimmerdiag{RESET}")

    with Ediabas() as ecu:
        # Detect or use specified SGBD
        sgbd = args.sgbd
        if not sgbd:
            print(f"\n{DIM}Auto-detecting ECU...{RESET}", end=" ", flush=True)
            sgbd = detect_sgbd(ecu)
            if not sgbd:
                print(f"\n{RED}ERROR: Could not detect ECU. "
                      f"Use --sgbd to specify manually.{RESET}")
                sys.exit(1)
        print(f"SGBD: {BOLD}{sgbd}{RESET}")

        # Route to commands
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

        if args.baseline:
            cmd_baseline(ecu, sgbd)
            return

        if args.clear_faults:
            cmd_clear_faults(ecu, sgbd)
            return

        if args.reset_injector is not None:
            cyls = args.reset_injector if args.reset_injector else None
            cmd_reset_injector(ecu, sgbd, cyls)
            return

        if args.monitor is not None:
            cmd_monitor(ecu, sgbd, args.monitor, csv_file=args.csv)
            return

        if args.health:
            cmd_health(ecu, sgbd)
            return

        if full_report:
            cmd_identify(ecu, sgbd)
            cmd_health(ecu, sgbd)
            cmd_sensors(ecu, sgbd)
            cmd_injectors(ecu, sgbd)
            cmd_faults(ecu, sgbd)
            cmd_ecu_config(ecu, sgbd)
        else:
            if args.sensors:
                cmd_sensors(ecu, sgbd)
            if args.faults:
                cmd_faults(ecu, sgbd)
            if args.injectors:
                cmd_injectors(ecu, sgbd)
            if args.turbo:
                cmd_turbo(ecu, sgbd)
            if args.cooling:
                cmd_cooling(ecu, sgbd)
            if args.fuel:
                cmd_fuel(ecu, sgbd)
            if args.exhaust:
                cmd_exhaust(ecu, sgbd)
            if args.service:
                cmd_service(ecu, sgbd)
            if args.config:
                cmd_ecu_config(ecu, sgbd)
            if args.trans:
                cmd_trans(ecu, sgbd)

        if args.reset_trans:
            cmd_reset_trans(ecu, sgbd)
            return


if __name__ == "__main__":
    main()
