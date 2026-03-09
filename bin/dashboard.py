#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard.py — Live visual ECU diagnostic dashboard

Real-time terminal dashboard for BMW DDE7 diesel ECU diagnostics.
Full-screen, auto-refreshing, color-coded gauges and status.

Usage:
    python bin/dashboard.py              # Launch dashboard
    python bin/dashboard.py --sgbd D73N57B0  # Override SGBD

Requires: pip install rich
"""

import argparse
import sys
import time
from datetime import datetime

try:
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.live import Live
    from rich.align import Align
    from rich import box
except ImportError:
    print("\n  Dashboard requires 'rich': pip install rich\n")
    sys.exit(1)

from ediabas import Ediabas, EdiabasError

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
BORDER = "green"
BORDER_DIM = "dark_green"
TITLE = "bold bright_green"
LABEL = "bright_cyan"
VALUE = "bold bright_white"
DIM = "dim green"
OK = "bright_green"
WARN = "bright_yellow"
CRIT = "bright_red"
COLD = "bright_cyan"
GAUGE_ON = "bright_green"
GAUGE_OFF = "bright_black"

HEADER_ART = r"""[bold bright_green]  ██████╗ ██╗███╗   ███╗███╗   ███╗███████╗██████╗ [bold bright_cyan]██████╗ ██╗ █████╗  ██████╗[/]
[bold bright_green]  ██╔══██╗██║████╗ ████║████╗ ████║██╔════╝██╔══██╗[bold bright_cyan]██╔══██╗██║██╔══██╗██╔════╝[/]
[bold bright_green]  ██████╔╝██║██╔████╔██║██╔████╔██║█████╗  ██████╔╝[bold bright_cyan]██║  ██║██║███████║██║  ███╗[/]
[bold bright_green]  ██╔══██╗██║██║╚██╔╝██║██║╚██╔╝██║██╔══╝  ██╔══██╗[bold bright_cyan]██║  ██║██║██╔══██║██║   ██║[/]
[bold bright_green]  ██████╔╝██║██║ ╚═╝ ██║██║ ╚═╝ ██║███████╗██║  ██║[bold bright_cyan]██████╔╝██║██║  ██║╚██████╔╝[/]
[bold bright_green]  ╚═════╝ ╚═╝╚═╝     ╚═╝╚═╝     ╚═╝╚══════╝╚═╝  ╚═╝[bold bright_cyan]╚═════╝ ╚═╝╚═╝  ╚═╝ ╚═════╝[/]"""


# ---------------------------------------------------------------------------
# Gauge rendering
# ---------------------------------------------------------------------------
def gauge(value, min_v, max_v, width=20, cold=None, warn=None, crit=None):
    """Single-color gauge bar with threshold-based color."""
    if value is None:
        return Text("─" * width, style=DIM)
    ratio = max(0.0, min(1.0, (value - min_v) / (max_v - min_v)))
    filled = round(ratio * width)
    style = OK
    if crit is not None and value >= crit:
        style = CRIT
    elif warn is not None and value >= warn:
        style = WARN
    elif cold is not None and value <= cold:
        style = COLD
    t = Text()
    t.append("█" * filled, style=style)
    t.append("░" * (width - filled), style=GAUGE_OFF)
    return t


def rpm_gauge(rpm, width=55):
    """Multi-zone RPM tachometer: green → yellow → red."""
    if rpm is None:
        return Text("─" * width, style=DIM)
    max_rpm = 6000
    filled = round(max(0, min(1, rpm / max_rpm)) * width)
    green_end = round(3000 / max_rpm * width)
    yellow_end = round(4500 / max_rpm * width)
    t = Text()
    for i in range(width):
        if i < filled:
            if i < green_end:
                t.append("█", style="bright_green")
            elif i < yellow_end:
                t.append("█", style="bright_yellow")
            else:
                t.append("█", style="bright_red")
        else:
            t.append("░", style="bright_black")
    return t


def cyl_gauge(value, width=8):
    """Small gauge for cylinder roughness (0-10 rpm range)."""
    if value is None:
        return Text("─" * width, style=DIM)
    absval = abs(value)
    filled = round(max(0, min(1, absval / 10)) * width)
    style = OK if absval < 3 else (WARN if absval < 8 else CRIT)
    t = Text()
    t.append("▓" * filled, style=style)
    t.append("░" * (width - filled), style=GAUGE_OFF)
    return t


def val_text(value, fmt=".1f", suffix="", style=VALUE):
    """Format a value for display, handling None."""
    if value is None:
        return Text("---", style=DIM)
    return Text(f"{value:{fmt}}{suffix}", style=style)


def status_icon(value, ok_lo, ok_hi, warn_lo=None, warn_hi=None):
    """Return a colored status icon based on thresholds."""
    if value is None:
        return Text(" ", style=DIM)
    if (warn_lo is not None and value < warn_lo) or \
       (warn_hi is not None and value > warn_hi):
        return Text("█", style=CRIT)
    if value < ok_lo or value > ok_hi:
        return Text("█", style=WARN)
    return Text("█", style=OK)


# ---------------------------------------------------------------------------
# Dashboard state
# ---------------------------------------------------------------------------
class Dashboard:
    def __init__(self, ecu, sgbd):
        self.ecu = ecu
        self.sgbd = sgbd
        self.d = {}
        self.prev = {}
        self.faults = []
        self.roughness = {}
        self.fuel_corr = {}
        self.cycle = 0
        self.poll_ms = 0
        self.last_update = None
        self.error = None

    def poll(self):
        """Read sensor data from ECU."""
        self.cycle += 1
        self.error = None
        t0 = time.time()

        try:
            # Every cycle: fast-changing values (~9 calls)
            self._r("rpm", "STATUS_MOTORDREHZAHL", "STAT_MOTORDREHZAHL_WERT")
            self._r("batt", "STATUS_UBATT", "STAT_UBATT_WERT")
            self._r("boost", "STATUS_LADEDRUCK_IST", "STAT_LADEDRUCK_IST_WERT")
            self._r("boost_t", "STATUS_LADEDRUCK_SOLL", "STAT_LADEDRUCK_SOLL_WERT")
            self._r("rail", "STATUS_RAILDRUCK_IST", "STAT_RAILDRUCK_IST_WERT")
            self._r("rail_t", "STATUS_RAILDRUCK_SOLL", "STAT_RAILDRUCK_SOLL_WERT")
            self._r("maf", "STATUS_LMM_MASSE", "STAT_LMM_MASSE_WERT")
            self._r("air", "STATUS_LUFTMASSE_IST", "STAT_LUFTMASSE_IST_WERT")
            self._r("air_t", "STATUS_LUFTMASSE_SOLL", "STAT_LUFTMASSE_SOLL_WERT")

            # Every 3rd cycle: temperatures (~7 calls)
            if self.cycle % 3 == 1:
                self._r("cool", "STATUS_KUEHLMITTELTEMPERATUR",
                         "STAT_KUEHLMITTELTEMPERATUR_WERT")
                self._r("oil", "STATUS_MOTORTEMPERATUR", "STAT_MOTORTEMPERATUR_WERT")
                self._r("intake", "STATUS_AN_LUFTTEMPERATUR",
                         "STAT_AN_LUFTTEMPERATUR_WERT")
                self._r("charge", "STATUS_LADELUFTTEMPERATUR",
                         "STAT_LADELUFTTEMPERATUR_WERT")
                self._r("ambient", "STATUS_UMGEBUNGSTEMPERATUR",
                         "STAT_UMGEBUNGSTEMPERATUR_WERT")
                self._r("fuel_t", "STATUS_KRAFTSTOFFTEMPERATUR",
                         "STAT_KRAFTSTOFFTEMPERATUR_WERT")
                self._r("baro", "STATUS_ATMOSPHAERENDRUCK",
                         "STAT_ATMOSPHAERENDRUCK_WERT")

            # Every 10th cycle: injectors + faults
            if self.cycle % 10 == 1:
                self._poll_injectors()
                self._poll_faults()

        except Exception as e:
            self.error = str(e)

        self.poll_ms = int((time.time() - t0) * 1000)
        self.last_update = datetime.now()

    def _r(self, key, job, result):
        try:
            val = self.ecu.read_value(self.sgbd, job, result)
            if val is not None:
                self.prev[key] = self.d.get(key)
                self.d[key] = val
        except EdiabasError:
            pass

    def _poll_injectors(self):
        try:
            results = self.ecu.run_job(self.sgbd, "STATUS_LAUFUNRUHE_DREHZAHL")
            if len(results) > 1:
                for k, v in results[1].items():
                    if "ZYL" in k and "WERT" in k and isinstance(v, (int, float)):
                        cyl = k.split("ZYL")[1].split("_")[0]
                        self.roughness[cyl] = v
        except EdiabasError:
            pass
        try:
            results = self.ecu.run_job(self.sgbd, "STATUS_LAUFUNRUHE_LLR_MENGE")
            if len(results) > 1:
                for k, v in results[1].items():
                    if "ZYL" in k and "WERT" in k and isinstance(v, (int, float)):
                        cyl = k.split("ZYL")[1].split("_")[0]
                        self.fuel_corr[cyl] = v
        except EdiabasError:
            pass

    def _poll_faults(self):
        try:
            self.faults = self.ecu.read_faults(self.sgbd)
        except EdiabasError:
            pass

    def g(self, key, default=None):
        return self.d.get(key, default)

    def trend(self, key):
        """Return trend arrow character."""
        cur = self.d.get(key)
        prev = self.prev.get(key)
        if cur is None or prev is None:
            return Text(" ", style=DIM)
        diff = cur - prev
        if abs(diff) < 0.1:
            return Text("─", style=DIM)
        if diff > 0:
            return Text("▲", style=OK if key != "cool" or cur < 100 else WARN)
        return Text("▼", style=COLD if key == "cool" and cur < 80 else DIM)

    # -------------------------------------------------------------------
    # Rendering
    # -------------------------------------------------------------------
    def render(self):
        """Build the full dashboard display."""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=8),
            Layout(name="rpm", size=4),
            Layout(name="middle"),
            Layout(name="fuel", size=4),
            Layout(name="cylinders", size=6),
            Layout(name="faults", size=5),
            Layout(name="footer", size=1),
        )
        layout["middle"].split_row(
            Layout(name="temps"),
            Layout(name="boost"),
        )

        layout["header"] = self._render_header()
        layout["rpm"] = self._render_rpm()
        layout["middle"]["temps"] = self._render_temps()
        layout["middle"]["boost"] = self._render_boost()
        layout["fuel"] = self._render_fuel()
        layout["cylinders"] = self._render_cylinders()
        layout["faults"] = self._render_faults()
        layout["footer"] = self._render_footer()

        return layout

    def _render_header(self):
        ts = self.last_update.strftime("%H:%M:%S") if self.last_update else "--:--:--"
        info = Text()
        info.append(f"  E70 xDrive35d", style="bold bright_white")
        info.append(f"  │  ", style=DIM)
        info.append(f"DDE7.3", style=LABEL)
        info.append(f"  │  ", style=DIM)
        info.append(f"{self.sgbd}", style=LABEL)
        info.append(f"  │  ", style=DIM)
        info.append(f"{ts}", style=VALUE)

        content = Text.from_markup(HEADER_ART)
        content.append("\n")
        content.append_text(info)

        return Panel(content, border_style=BORDER, box=box.DOUBLE,
                     style="on black")

    def _render_rpm(self):
        rpm = self.g("rpm")
        batt = self.g("batt")
        batt_v = batt / 1000 if batt else None

        t = Table.grid(expand=True, padding=(0, 1))
        t.add_column(width=5, justify="right")
        t.add_column()
        t.add_column(width=12, justify="right")

        # RPM row
        rpm_text = Text()
        if rpm is not None:
            rpm_text.append(f"{rpm:>5.0f} ", style="bold bright_white")
            rpm_text.append("rpm", style=LABEL)
        else:
            rpm_text.append("  --- rpm", style=DIM)

        t.add_row(
            Text("RPM", style=LABEL),
            rpm_gauge(rpm),
            rpm_text,
        )

        # Scale row
        scale = Text()
        marks = [0, 1000, 2000, 3000, 4000, 5000, 6000]
        positions = [round(m / 6000 * 55) for m in marks]
        line = [" "] * 56
        for m, p in zip(marks, positions):
            s = str(m // 1000) + "k" if m > 0 else "0"
            for j, c in enumerate(s):
                if p + j < len(line):
                    line[p + j] = c
        scale.append("".join(line), style=DIM)

        batt_text = Text()
        if batt_v is not None:
            color = OK if 12.0 <= batt_v <= 14.8 else (WARN if batt_v >= 11.5 else CRIT)
            batt_text.append(f"BATT {batt_v:.1f}V", style=color)
        else:
            batt_text.append("BATT ---", style=DIM)

        t.add_row(Text("", style=DIM), scale, batt_text)

        return Panel(t, title="[bright_cyan]ENGINE[/]",
                     border_style=BORDER, box=box.HEAVY, style="on black")

    def _render_temps(self):
        temps = [
            ("COOL", "cool", 0, 130, 60, 105, 115),
            ("OIL", "oil", 0, 150, 60, 120, 135),
            ("IN", "intake", -30, 60, None, None, None),
            ("CHG", "charge", -30, 80, None, None, None),
            ("AMB", "ambient", -30, 50, None, None, None),
            ("FUEL", "fuel_t", -20, 80, None, 60, 80),
        ]
        t = Table.grid(padding=(0, 1))
        t.add_column(width=5, justify="right")  # label
        t.add_column(width=15)                   # gauge
        t.add_column(width=7, justify="right")   # value
        t.add_column(width=1)                    # trend
        t.add_column(width=1)                    # status

        for label, key, lo, hi, cold_t, warn_t, crit_t in temps:
            val = self.g(key)
            t.add_row(
                Text(label, style=LABEL),
                gauge(val, lo, hi, width=15,
                      cold=cold_t, warn=warn_t, crit=crit_t),
                val_text(val, ".0f", "°C"),
                self.trend(key),
                status_icon(val, cold_t or lo, warn_t or hi,
                            cold_t, crit_t) if (cold_t or warn_t or crit_t) else Text(""),
            )

        return Panel(t, title="[bright_cyan]TEMPERATURES[/]",
                     border_style=BORDER, box=box.HEAVY, style="on black")

    def _render_boost(self):
        boost = self.g("boost")
        boost_t = self.g("boost_t")
        baro = self.g("baro")
        maf = self.g("maf")
        air = self.g("air")
        air_t = self.g("air_t")

        t = Table.grid(padding=(0, 1))
        t.add_column(width=5, justify="right")
        t.add_column(width=15)
        t.add_column(width=20, justify="right")

        # Boost actual
        boost_val = Text()
        if boost is not None:
            boost_val.append(f"{boost:.0f}", style=VALUE)
            if boost_t:
                boost_val.append(f" / {boost_t:.0f}", style=DIM)
            boost_val.append(" hPa", style=LABEL)
        else:
            boost_val.append("--- hPa", style=DIM)
        t.add_row(Text("BOOST", style=LABEL),
                  gauge(boost, 800, 2500, 15), boost_val)

        # Boost deviation
        if boost and boost_t and boost_t > 0:
            pct = (boost - boost_t) / boost_t * 100
            color = OK if abs(pct) < 5 else (WARN if abs(pct) < 15 else CRIT)
            dev = Text(f"DEV {pct:+.1f}%", style=color)
        else:
            dev = Text("", style=DIM)
        # Baro
        baro_t = Text()
        if baro:
            baro_t.append(f"BARO {baro:.0f} hPa", style=DIM)
        t.add_row(Text("", style=DIM), dev, baro_t)

        # MAF
        maf_val = Text()
        if maf is not None:
            maf_val.append(f"{maf:.1f}", style=VALUE)
            maf_val.append(" kg/h", style=LABEL)
        else:
            maf_val.append("--- kg/h", style=DIM)
        t.add_row(Text("MAF", style=LABEL),
                  gauge(maf, 0, 800, 15), maf_val)

        # Air mass deviation
        if air and air_t and air_t > 0:
            pct = (air - air_t) / air_t * 100
            color = OK if abs(pct) < 5 else (WARN if abs(pct) < 15 else CRIT)
            air_dev = Text(f"AIR MASS {pct:+.1f}%", style=color)
            if pct < -15:
                air_dev.append(" ◄ CHECK", style=CRIT)
        else:
            air_dev = Text("", style=DIM)
        t.add_row(Text("", style=DIM), air_dev, Text(""))

        return Panel(t, title="[bright_cyan]BOOST / TURBO[/]",
                     border_style=BORDER, box=box.HEAVY, style="on black")

    def _render_fuel(self):
        rail = self.g("rail")
        rail_t = self.g("rail_t")
        fuel_t = self.g("fuel_t")

        t = Table.grid(expand=True, padding=(0, 1))
        t.add_column(width=5, justify="right")
        t.add_column()
        t.add_column(width=25, justify="right")

        # Rail pressure bar (full width)
        rail_val = Text()
        if rail is not None:
            rail_val.append(f"{rail:.0f}", style=VALUE)
            if rail_t:
                rail_val.append(f" / {rail_t:.0f}", style=DIM)
            rail_val.append(" bar", style=LABEL)
            if rail_t and rail_t > 0:
                pct = (rail - rail_t) / rail_t * 100
                color = OK if abs(pct) < 5 else (WARN if abs(pct) < 10 else CRIT)
                rail_val.append(f"  {pct:+.1f}%", style=color)
        else:
            rail_val.append("--- bar", style=DIM)

        t.add_row(
            Text("RAIL", style=LABEL),
            gauge(rail, 0, 1800, width=35, warn=1600, crit=1800),
            rail_val,
        )

        # Fuel temp + info line
        info = Text()
        if fuel_t is not None:
            info.append(f"FUEL TEMP {fuel_t:.0f}°C", style=DIM)
        t.add_row(Text("", style=DIM), info, Text(""))

        return Panel(t, title="[bright_cyan]FUEL SYSTEM[/]",
                     border_style=BORDER, box=box.HEAVY, style="on black")

    def _render_cylinders(self):
        t = Table.grid(padding=(0, 2))
        # 6 columns: 3 pairs of (cyl_info, spacer)
        for _ in range(3):
            t.add_column(width=4, justify="right")  # CYL N
            t.add_column(width=8)                    # gauge
            t.add_column(width=7, justify="right")   # value
            t.add_column(width=4)                    # status

        rows = [["1", "2", "3"], ["4", "5", "6"]]
        for row_cyls in rows:
            cells = []
            for cyl in row_cyls:
                rpm_dev = self.roughness.get(cyl)
                fuel = self.fuel_corr.get(cyl)
                absval = abs(rpm_dev) if rpm_dev is not None else 0

                cells.append(Text(f"CYL{cyl}", style=LABEL))
                cells.append(cyl_gauge(rpm_dev))

                val = Text()
                if rpm_dev is not None:
                    val.append(f"{rpm_dev:+.1f}", style=VALUE)
                else:
                    val.append("---", style=DIM)
                cells.append(val)

                if rpm_dev is not None:
                    if absval > 8:
                        cells.append(Text("FAIL", style=CRIT))
                    elif absval > 3:
                        cells.append(Text("WARN", style=WARN))
                    else:
                        cells.append(Text(" OK ", style=OK))
                else:
                    cells.append(Text(" -- ", style=DIM))
            t.add_row(*cells)

        # Summary line
        if self.roughness:
            vals = [abs(v) for v in self.roughness.values()]
            worst = max(vals)
            mean = sum(vals) / len(vals)
            summary = Text()
            summary.append(f"  Mean: {mean:.1f} rpm", style=DIM)
            summary.append(f"  │  Worst: {worst:.1f} rpm", style=DIM)
            if self.fuel_corr:
                worst_fuel = max(abs(v) for v in self.fuel_corr.values())
                summary.append(f"  │  Max fuel corr: {worst_fuel:.2f} mg", style=DIM)
            t.add_row(summary, *[Text("")] * 11)

        return Panel(t, title="[bright_cyan]CYLINDER BALANCE[/]  [dim](idle roughness rpm)[/]",
                     border_style=BORDER, box=box.HEAVY, style="on black")

    def _render_faults(self):
        t = Text()
        n = len(self.faults)
        if n == 0:
            t.append("  NO STORED FAULTS", style=OK)
        else:
            t.append(f"  {n} FAULT(S) STORED\n", style=WARN)
            for i, f in enumerate(self.faults[:4]):
                code = f.get("F_ORT_NR", "?")
                loc = str(f.get("F_ORT_TEXT", ""))[:45]
                t.append(f"  {code:>5s}  ", style=VALUE)
                t.append(f"{loc}", style=DIM)
                if i < min(n, 4) - 1:
                    t.append("\n")
            if n > 4:
                t.append(f"\n  ... and {n - 4} more", style=DIM)

        title_style = CRIT if n > 0 else OK
        return Panel(t, title=f"[bright_cyan]FAULTS[/]  [{title_style}]({n})[/]",
                     border_style=BORDER if n == 0 else "yellow",
                     box=box.HEAVY, style="on black")

    def _render_footer(self):
        ts = self.last_update.strftime("%H:%M:%S") if self.last_update else "--:--:--"
        t = Text()
        t.append(f" ░ {ts} ", style=DIM)
        t.append(f"░ Cycle {self.cycle} ", style=DIM)
        t.append(f"░ {self.poll_ms}ms/poll ", style=DIM)
        if self.error:
            t.append(f"░ ERR: {self.error[:40]} ", style=CRIT)
        t.append(f"░ Ctrl+C quit ", style=DIM)
        t.append("░" * 40, style="dark_green")
        return t


# ---------------------------------------------------------------------------
# SGBD Detection
# ---------------------------------------------------------------------------
def detect_sgbd(ecu):
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
# Splash screen
# ---------------------------------------------------------------------------
def splash(console, sgbd):
    """Show connecting animation."""
    console.clear()
    console.print(Text.from_markup(HEADER_ART))
    console.print()
    console.print(f"  [bright_cyan]SGBD:[/] [bold bright_white]{sgbd}[/]")
    console.print(f"  [bright_cyan]Starting live dashboard...[/]")
    console.print()
    # Quick visual flourish
    for i in range(30):
        bar = "█" * (i + 1) + "░" * (29 - i)
        console.print(f"\r  [bright_green]{bar}[/]", end="")
        time.sleep(0.02)
    console.print()
    time.sleep(0.3)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="BIMMERDIAG — Live ECU Dashboard")
    parser.add_argument("--sgbd", default=None, help="Override SGBD")
    args = parser.parse_args()

    console = Console()

    console.clear()
    console.print(Text.from_markup(HEADER_ART))
    console.print()

    with Ediabas() as ecu:
        sgbd = args.sgbd
        if not sgbd:
            console.print("  [bright_cyan]Auto-detecting ECU...[/]", end=" ")
            sgbd = detect_sgbd(ecu)
            if not sgbd:
                console.print("[bright_red]FAILED[/]")
                console.print("  [red]Could not detect ECU. Use --sgbd[/]")
                sys.exit(1)
            console.print(f"[bright_green]{sgbd}[/]")

        splash(console, sgbd)

        dash = Dashboard(ecu, sgbd)

        # Initial full poll
        dash.poll()

        with Live(dash.render(), console=console, screen=True,
                  refresh_per_second=4) as live:
            try:
                while True:
                    dash.poll()
                    live.update(dash.render())
            except KeyboardInterrupt:
                pass

    console.clear()
    console.print(f"\n  [bright_green]BIMMERDIAG[/] dashboard closed.\n")


if __name__ == "__main__":
    main()
