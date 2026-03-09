#!/usr/bin/env python3
"""
sweep.py — Multi-ECU bus scanner for BMW diagnostics

Sweeps all known ECU addresses on the car, reports what's alive,
what's dead, and pulls identification + fault counts from every
responding module. Works across K-line (E39) and DCAN (E70+).

This is the diagnostic starting point — run this first to map
what's on the bus before diving into module-specific diagnostics.

Usage:
    python bin/sweep.py                    # Auto-detect vehicle, sweep all
    python bin/sweep.py --vehicle e39      # Force E39 module set
    python bin/sweep.py --vehicle e70      # Force E70 module set
    python bin/sweep.py --faults           # Also read fault counts per module
    python bin/sweep.py --deep             # Full ident + faults + status
    python bin/sweep.py --ibus             # E39 only: sweep IBUS modules only
    python bin/sweep.py --json             # Machine-readable output
    python bin/sweep.py --module ike       # Probe a single module
"""

import argparse
import json
import sys
import time
from ediabas import Ediabas, EdiabasError

# ---------------------------------------------------------------------------
# Terminal colors (same conventions as diag.py)
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


# ---------------------------------------------------------------------------
# Module Registries
#
# Each module: (alias, name, sgbd, bus, description)
#
# SGBD names are the .prg/.grp files in the EDIABAS ECU directory.
# Not every module is present on every car — that's what sweep detects.
#
# For group files (D_MOTOR, D_KOMBI, etc.), EDIABAS resolves the actual
# variant SGBD internally. For direct SGBD files (D73N57B0, etc.),
# we talk to a specific ECU variant.
# ---------------------------------------------------------------------------

# E39 modules (1997-2003 5-series)
# Mix of IBUS, KBUS, DBUS, and PT-CAN modules
# Uses 20-pin diagnostic connector under hood + OBD2 port
E39_MODULES = [
    # (alias, display_name, sgbd, bus, description)

    # Powertrain
    ("dme",   "DME",           "D_0012",   "dbus",  "Engine control (DME addr 0x12)"),
    ("dme2",  "DME (alt)",     "D_MOTOR",  "dbus",  "Engine control (group file)"),
    ("egs",   "EGS",           "D_EGS",    "dbus",  "Automatic transmission"),

    # Body — IBUS
    ("ike",   "IKE",           "D_KOMBI",  "ibus",  "Instrument cluster"),
    ("lcm",   "LCM",           "D_0060",   "ibus",  "Light control module"),
    ("gm",    "GM/ZKE",        "D_ZKE",    "kbus",  "General module — locks, windows, remote"),
    ("ihka",  "IHKA",          "D_KLIMA",  "ibus",  "HVAC climate control"),
    ("rad",   "Radio/HU",      "D_00F0",   "ibus",  "Head unit — radio, IBUS hub"),
    ("nav",   "Navigation",    "D_007F",   "ibus",  "Navigation computer"),
    ("cdc",   "CD Changer",    "D_0018",   "ibus",  "Trunk CD changer"),
    ("mfl",   "MFL",           "D_MFL",    "ibus",  "Steering wheel buttons"),
    ("pdc",   "PDC",           "D_PDC",    "ibus",  "Park distance control"),
    ("shd",   "SHD",           "D_SHD",    "ibus",  "Sunroof module"),
    ("bmbt",  "BMBT",          "D_BMBT",   "ibus",  "Board monitor buttons/display"),
    ("dspc",  "RLS",           "D_RLS",    "ibus",  "Rain/light sensor"),

    # Chassis
    ("abs",   "ABS/DSC",       "D_BREM",   "dbus",  "ABS / stability control"),
    ("srs",   "SRS",           "D_SRS",    "dbus",  "Airbag module"),
    ("ews",   "EWS",           "D_EWS",    "kbus",  "Electronic immobilizer"),

    # Comfort
    ("sitz",  "Seat Module",   "D_SITZ",   "kbus",  "Power seat memory"),
    ("shzh",  "Seat Heating",  "D_SHZ",    "kbus",  "Seat heater control"),
]

# E70 modules (2007-2013 X5, and similar E-series with DCAN)
# All CAN-based, uses standard OBD2 port
E70_MODULES = [
    # Powertrain
    ("dme",   "DME/DDE",       "D_MOTOR",  "ptcan",   "Engine control"),
    ("egs",   "EGS",           "GS19",     "ptcan",   "Automatic transmission"),
    ("vdm",   "VDM",           "D_VDM",    "ptcan",   "Transfer case (xDrive)"),

    # Body
    ("cas",   "CAS",           "D_CAS",    "kcan",    "Car access system — key, immobilizer, start"),
    ("frm",   "FRM",           "D_FRM",    "kcan",    "Footwell module — lights, wipers"),
    ("jbe",   "JBE",           "D_JBE",    "kcan",    "Junction box electronics"),
    ("cic",   "CIC/HU",        "D_CIC",    "mostcan", "Head unit — nav, media"),
    ("ihka",  "IHKA",          "D_KLIMA",  "kcan",    "HVAC climate control"),
    ("kombi", "KOMBI",         "D_KOMBI",  "kcan",    "Instrument cluster"),
    ("pdc",   "PDC",           "D_PDC",    "kcan",    "Park distance control"),
    ("sz",    "SZ",            "D_SZ",     "kcan",    "Center console switch center"),
    ("trsvc", "SZL",           "D_SZL",    "kcan",    "Steering column switch cluster"),
    ("hkl",   "HKL",           "D_HKL",    "kcan",    "Tailgate lift"),

    # Chassis
    ("dsc",   "DSC",           "D_BREM",   "ptcan",   "Dynamic stability control"),
    ("edc",   "EDC",           "D_EDC",    "ptcan",   "Electronic damper control"),
    ("ehc",   "EHC",           "D_EHC",    "ptcan",   "Electronic height control (air susp)"),
    ("srs",   "SRS",           "D_SRS",    "ptcan",   "Airbag module"),
    ("eps",   "EPS",           "D_EPS",    "ptcan",   "Electric power steering"),
    ("emf",   "EMF",           "D_EMF",    "ptcan",   "Electronic parking brake"),
]

# Combined / all known modules (for auto-detect fallback)
ALL_MODULES = E39_MODULES + E70_MODULES


def get_module_list(vehicle=None, bus_filter=None):
    """Get the module list for a vehicle, optionally filtered by bus."""
    if vehicle == "e39":
        modules = E39_MODULES
    elif vehicle == "e70":
        modules = E70_MODULES
    else:
        modules = ALL_MODULES

    if bus_filter:
        modules = [m for m in modules if m[3] == bus_filter]

    return modules


# ---------------------------------------------------------------------------
# Sweep Logic
# ---------------------------------------------------------------------------
def probe_module(ecu, sgbd, timeout_ms=5000):
    """
    Probe a single module via IDENT job.

    Returns:
        (alive: bool, ident: dict, error: str)
    """
    try:
        results = ecu.run_job(sgbd, "IDENT", timeout_ms=timeout_ms)
        # IDENT returns set 0 (system) and set 1+ (data)
        ident = {}
        for result_set in results[1:]:
            ident.update(result_set)
        # Remove noise
        ident.pop("JOB_STATUS", None)
        return True, ident, ""
    except EdiabasError as e:
        return False, {}, str(e)


def read_fault_count(ecu, sgbd, timeout_ms=5000):
    """
    Read fault count from a module. Returns (count, error_str).
    Returns -1 if can't read.
    """
    try:
        results = ecu.run_job(sgbd, "FS_LESEN", timeout_ms=timeout_ms)
        # Count result sets with fault data (skip set 0, skip status-only sets)
        fault_sets = [r for r in results[1:]
                      if "F_ORT_NR" in r or "F_ORT_TEXT" in r]
        return len(fault_sets), ""
    except EdiabasError as e:
        return -1, str(e)


def sweep(ecu, modules, read_faults=False, deep=False, timeout_ms=5000):
    """
    Sweep all modules in the list.

    Yields: (alias, name, sgbd, bus, desc, alive, ident, fault_count, errors)
    """
    seen_sgbd = set()

    for alias, name, sgbd, bus, desc in modules:
        # Skip duplicate SGBDs (e.g. if both E39 and E70 lists overlap)
        if sgbd in seen_sgbd:
            continue
        seen_sgbd.add(sgbd)

        alive, ident, err = probe_module(ecu, sgbd, timeout_ms)

        fault_count = -1
        if alive and (read_faults or deep):
            fault_count, ferr = read_fault_count(ecu, sgbd, timeout_ms)
            if ferr and not err:
                err = ferr

        yield (alias, name, sgbd, bus, desc, alive, ident, fault_count, err)


# ---------------------------------------------------------------------------
# Output Formatting
# ---------------------------------------------------------------------------
def format_ident_short(ident):
    """Extract the most useful ident fields for display."""
    parts = []

    # Try common field names across different SGBD variants
    for key in ["ID_BMW_NR", "ID_HW_NR", "BMW_NR"]:
        if key in ident:
            parts.append(str(ident[key]).strip())
            break

    for key in ["ID_LIEF_TEXT", "ID_LIEF", "HERSTELLER"]:
        if key in ident:
            val = str(ident[key]).strip()
            if val and val not in parts:
                parts.append(val)
            break

    # Software version
    for key in ["ID_SW_NR_FSV", "ID_SW_NR", "SW_NR"]:
        if key in ident:
            parts.append(f"SW:{ident[key]}")
            break

    # SGBD variant (what EDIABAS resolved to)
    for key in ["VARIANTE", "ID_VARIANTE"]:
        if key in ident:
            parts.append(f"[{ident[key]}]")
            break

    return " | ".join(parts) if parts else ""


def print_sweep_table(results, show_faults=False):
    """Print sweep results as a formatted table."""
    alive_list = []
    dead_list = []

    for r in results:
        alias, name, sgbd, bus, desc, alive, ident, fault_count, err = r
        if alive:
            alive_list.append(r)
        else:
            dead_list.append(r)

    # Header
    print(f"\n{'=' * 78}")
    print(f"  {BOLD}BUS SWEEP RESULTS{RESET}")
    print(f"{'=' * 78}")

    # Responding modules
    if alive_list:
        print(f"\n  {GREEN}{BOLD}RESPONDING ({len(alive_list)}){RESET}\n")

        # Column widths
        w_alias = 7
        w_name = 14
        w_bus = 7
        w_faults = 8 if show_faults else 0
        w_info = 30

        header = f"  {'Module':>{w_alias}}  {'Name':<{w_name}}  {'Bus':<{w_bus}}"
        if show_faults:
            header += f"  {'Faults':>{w_faults}}"
        header += f"  {'Info'}"
        print(f"  {DIM}{header}{RESET}")
        print(f"  {DIM}{'-' * 76}{RESET}")

        for alias, name, sgbd, bus, desc, alive, ident, fault_count, err in alive_list:
            info = format_ident_short(ident)
            line = f"  {GREEN}{'[OK]':>6}{RESET}  {BOLD}{name:<{w_name}}{RESET}  {bus:<{w_bus}}"

            if show_faults:
                if fault_count == 0:
                    fstr = f"{GREEN}{'0':>{w_faults - 1}}{RESET} "
                elif fault_count > 0:
                    fstr = f"{YELLOW}{str(fault_count):>{w_faults - 1}}{RESET} "
                else:
                    fstr = f"{DIM}{'?':>{w_faults - 1}}{RESET} "
                line += f"  {fstr}"

            if info:
                line += f"  {DIM}{info}{RESET}"
            print(line)

    # Dead/timeout modules
    if dead_list:
        print(f"\n  {RED}{BOLD}TIMEOUT ({len(dead_list)}){RESET}\n")
        for alias, name, sgbd, bus, desc, alive, ident, fault_count, err in dead_list:
            # Classify the error
            if "NOT_FOUND" in err or "OBJECT" in err:
                reason = "SGBD not found"
                color = DIM
            elif "IFH" in err:
                reason = "no response"
                color = RED
            else:
                reason = err.split(":")[-1].strip()[:30] if err else "timeout"
                color = RED

            print(f"  {color}{'[--]':>6}  {name:<14}  {bus:<7}  {reason}{RESET}")

    # Summary
    print(f"\n{'=' * 78}")
    total = len(alive_list) + len(dead_list)
    print(f"  {GREEN}{len(alive_list)} responding{RESET}  "
          f"{RED}{len(dead_list)} timeout{RESET}  "
          f"{DIM}{total} probed{RESET}")

    if show_faults:
        total_faults = sum(r[7] for r in alive_list if r[7] > 0)
        modules_with_faults = sum(1 for r in alive_list if r[7] > 0)
        if total_faults > 0:
            print(f"  {YELLOW}{total_faults} total fault(s) across "
                  f"{modules_with_faults} module(s){RESET}")
        else:
            print(f"  {GREEN}No faults detected across responding modules{RESET}")
    print()


def print_sweep_json(results):
    """Print sweep results as JSON."""
    data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "modules": [],
    }
    for alias, name, sgbd, bus, desc, alive, ident, fault_count, err in results:
        entry = {
            "alias": alias,
            "name": name,
            "sgbd": sgbd,
            "bus": bus,
            "description": desc,
            "status": "responding" if alive else "timeout",
        }
        if alive:
            entry["ident"] = ident
            if fault_count >= 0:
                entry["fault_count"] = fault_count
        else:
            entry["error"] = err
        data["modules"].append(entry)

    print(json.dumps(data, indent=2, default=str))


def print_deep_report(results):
    """Print detailed per-module report (--deep mode)."""
    print_sweep_table(results, show_faults=True)

    alive = [r for r in results if r[5]]
    if not alive:
        return

    print(f"\n{'=' * 78}")
    print(f"  {BOLD}DETAILED MODULE INFORMATION{RESET}")
    print(f"{'=' * 78}")

    for alias, name, sgbd, bus, desc, _, ident, fault_count, err in alive:
        print(f"\n  {BOLD}{CYAN}┌─ {name} ({sgbd}) ─ {desc}{RESET}")

        if ident:
            for key, val in sorted(ident.items()):
                print(f"  {CYAN}│{RESET}  {key:35s}  {val}")

        if fault_count > 0:
            print(f"  {CYAN}│{RESET}")
            print(f"  {CYAN}│{RESET}  {YELLOW}{fault_count} fault(s) stored — "
                  f"run diag.py --faults for details{RESET}")
        elif fault_count == 0:
            print(f"  {CYAN}│{RESET}  {GREEN}No faults{RESET}")

        print(f"  {CYAN}└{'─' * 50}{RESET}")


def print_single_module(alias, name, sgbd, bus, desc, alive, ident, fault_count, err):
    """Detailed output for single module probe."""
    print(f"\n  {BOLD}{name}{RESET} ({sgbd})")
    print(f"  {desc}")
    print(f"  Bus: {bus}")
    print()

    if not alive:
        print(f"  {RED}NOT RESPONDING{RESET}")
        if err:
            print(f"  Error: {err}")
        print()
        print(f"  Possible causes:")
        print(f"    - Module not present on this vehicle")
        print(f"    - Module is dead / not powered")
        print(f"    - Bus communication failure (wiring, another module dragging bus down)")
        print(f"    - Wrong SGBD for this module variant")
        print(f"    - Cable not connected or wrong mode (check OLD/NEW switch)")
        return

    print(f"  {GREEN}RESPONDING{RESET}\n")

    if ident:
        print(f"  {BOLD}Identification:{RESET}")
        for key, val in sorted(ident.items()):
            print(f"    {key:35s}  {val}")

    if fault_count >= 0:
        print()
        if fault_count == 0:
            print(f"  {GREEN}No fault codes stored.{RESET}")
        else:
            print(f"  {YELLOW}{fault_count} fault code(s) stored.{RESET}")
            print(f"  {DIM}Run: python bin/diag.py --faults --sgbd {sgbd}{RESET}")
    print()


# ---------------------------------------------------------------------------
# Vehicle Auto-Detection
# ---------------------------------------------------------------------------
def detect_vehicle(ecu):
    """
    Try to auto-detect which vehicle platform we're talking to.

    Strategy:
    - Try D_MOTOR (works on most BMWs) — the resolved VARIANTE tells us the ECU
    - Try platform-specific modules to confirm (CAS = E70+, IKE = E39)
    """
    print(f"  {DIM}Auto-detecting vehicle...{RESET}", end=" ", flush=True)

    # Try engine ECU first — almost always responds
    engine_variante = None
    try:
        results = ecu.run_job("D_MOTOR", "IDENT", timeout_ms=5000)
        for r in results:
            if "VARIANTE" in r:
                engine_variante = r["VARIANTE"]
                break
    except EdiabasError:
        pass

    if engine_variante:
        # DDE7 variants (N57, M57TU2) = E70/E90 era
        if any(x in engine_variante for x in ["N57", "N47", "N55", "N54", "N63", "N20"]):
            print(f"E70-era (DCAN) — engine: {engine_variante}")
            return "e70", engine_variante
        # Older DME/DDE variants = E39 era
        if any(x in engine_variante for x in ["M57", "M52", "M54", "M62", "M73"]):
            print(f"E39-era (K-line) — engine: {engine_variante}")
            return "e39", engine_variante

    # Fallback: try platform-specific modules
    # CAS only exists on E70+ platforms
    try:
        ecu.run_job("D_CAS", "IDENT", timeout_ms=3000)
        print(f"E70-era (found CAS)")
        return "e70", engine_variante
    except EdiabasError:
        pass

    # IKE (instrument cluster) with D_KOMBI on old protocol = E39
    try:
        ecu.run_job("D_KOMBI", "IDENT", timeout_ms=3000)
        print(f"E39-era (found IKE/KOMBI)")
        return "e39", engine_variante
    except EdiabasError:
        pass

    print(f"{YELLOW}could not detect — use --vehicle to specify{RESET}")
    return None, engine_variante


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="BMW multi-ECU bus scanner — find what's alive, what's dead",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
{BOLD}Examples:{RESET}
  python bin/sweep.py                      Auto-detect vehicle, sweep all modules
  python bin/sweep.py --vehicle e39        Force E39 module set
  python bin/sweep.py --vehicle e70        Force E70 module set
  python bin/sweep.py --faults             Include fault count per module
  python bin/sweep.py --deep               Full ident + faults for all modules
  python bin/sweep.py --ibus               E39 IBUS modules only
  python bin/sweep.py --module ike         Probe single module by alias
  python bin/sweep.py --json               JSON output for scripting
  python bin/sweep.py --timeout 10000      Increase timeout for flaky connections

{BOLD}Vehicle platforms:{RESET}
  e39    1997-2003 5-series (K-line, 20-pin + OBD2, cable on OLD)
  e70    2007-2013 X5 and similar DCAN-era (OBD2 only, cable on NEW)

{BOLD}What this tells you:{RESET}
  If a module shows TIMEOUT, either it's not installed on your car,
  or it's dead/not communicating. Cross-reference against what your
  car should have. A module that should respond but doesn't is your
  diagnostic starting point.

  Example: radio shows TIMEOUT but you know it's installed?
  → Head unit is dead or dragging the bus down.
        """,
    )
    parser.add_argument("--vehicle", choices=["e39", "e70"],
                        help="Vehicle platform (default: auto-detect)")
    parser.add_argument("--faults", action="store_true",
                        help="Read fault count from each responding module")
    parser.add_argument("--deep", action="store_true",
                        help="Full ident + faults + details for all modules")
    parser.add_argument("--ibus", action="store_true",
                        help="E39 only: sweep IBUS modules only")
    parser.add_argument("--module", metavar="ALIAS",
                        help="Probe a single module by alias (e.g., ike, rad, dme)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--timeout", type=int, default=5000,
                        help="Per-module timeout in ms (default: 5000)")

    args = parser.parse_args()

    print(f"{BOLD}bimmerdiag sweep{RESET} — BMW multi-ECU bus scanner")
    print(f"{DIM}github.com/madhakish/bimmerdiag{RESET}\n")

    with Ediabas() as ecu:
        # Determine vehicle
        vehicle = args.vehicle
        engine_var = None
        if not vehicle:
            vehicle, engine_var = detect_vehicle(ecu)
            if not vehicle and not args.module:
                print(f"\n  {RED}Could not detect vehicle platform.{RESET}")
                print(f"  Use --vehicle e39 or --vehicle e70 to specify.")
                sys.exit(1)

        # Single module probe
        if args.module:
            alias = args.module.lower()
            # Search all module lists for the alias
            found = None
            search_lists = [E39_MODULES, E70_MODULES] if not vehicle else [
                E39_MODULES if vehicle == "e39" else E70_MODULES
            ]
            for mlist in search_lists:
                for m in mlist:
                    if m[0] == alias:
                        found = m
                        break
                if found:
                    break

            if not found:
                print(f"  {RED}Unknown module alias: {alias}{RESET}")
                print(f"\n  Known aliases:")
                mlist = get_module_list(vehicle)
                for a, n, s, b, d in mlist:
                    print(f"    {a:8s}  {n:14s}  {d}")
                sys.exit(1)

            a, n, s, b, d = found
            alive, ident, err = probe_module(ecu, s, args.timeout)
            fc = -1
            if alive:
                fc, _ = read_fault_count(ecu, s, args.timeout)
            print_single_module(a, n, s, b, d, alive, ident, fc, err)
            return

        # Build module list
        bus_filter = "ibus" if args.ibus else None
        if args.ibus and vehicle != "e39":
            print(f"  {YELLOW}--ibus only applies to E39. Ignoring.{RESET}")
            bus_filter = None

        modules = get_module_list(vehicle, bus_filter)

        if not modules:
            print(f"  {RED}No modules to scan.{RESET}")
            sys.exit(1)

        total = len(modules)
        show_faults = args.faults or args.deep

        # Progress indicator
        if not args.json:
            if vehicle:
                print(f"  Vehicle: {BOLD}{vehicle.upper()}{RESET}")
            if engine_var:
                print(f"  Engine:  {BOLD}{engine_var}{RESET}")
            print(f"  Probing {total} modules (timeout: {args.timeout}ms)...\n")

        # Execute sweep
        results = []
        for i, result in enumerate(sweep(ecu, modules, show_faults, args.deep,
                                          args.timeout)):
            alias, name, sgbd, bus, desc, alive, ident, fc, err = result
            results.append(result)

            # Live progress (non-JSON)
            if not args.json:
                status = f"{GREEN}OK{RESET}" if alive else f"{RED}--{RESET}"
                faults_str = ""
                if alive and fc > 0:
                    faults_str = f"  {YELLOW}({fc} faults){RESET}"
                elif alive and fc == 0:
                    faults_str = ""

                # Resolved SGBD variant (if group file resolved to something)
                resolved = ""
                if alive and ident:
                    for key in ["VARIANTE", "ID_VARIANTE"]:
                        if key in ident and ident[key] != sgbd:
                            resolved = f" → {ident[key]}"
                            break

                print(f"  [{i+1:2d}/{total}]  {status}  {name:<14s}  "
                      f"{sgbd}{resolved}{faults_str}")

        # Final output
        if args.json:
            print_sweep_json(results)
        elif args.deep:
            print_deep_report(results)
        else:
            print_sweep_table(results, show_faults)


if __name__ == "__main__":
    main()
