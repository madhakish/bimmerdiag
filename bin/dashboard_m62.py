#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard_m62.py — Live M62 idle hunting dashboard

Real-time terminal dashboard for diagnosing ICV hunting / idle stumble
on the E39 540i M62 (ME 5.2 / DME528). Shows scrolling time-series graphs
for ICV duty cycle, RPM, lambda integrators, and fuel trims.

The graphs make the sawtooth hunting pattern immediately visible —
you'll see the ICV duty wind up, the RPM dip, the recovery surge,
and the integrators chasing.

Usage:
    python bin/dashboard_m62.py
    python bin/dashboard_m62.py --sgbd DME528
    python bin/dashboard_m62.py --csv idle_capture.csv

Requires: pip install rich
"""

import argparse
import csv
import sys
import time
from collections import deque
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
# Theme (consistent with dashboard.py)
# ---------------------------------------------------------------------------
BORDER = "green"
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

# Graph colors for each trace
TRACE_ICV = "bright_yellow"
TRACE_RPM = "bright_green"
TRACE_INT_B1 = "bright_cyan"
TRACE_INT_B2 = "bright_magenta"
TRACE_ADD_B1 = "bright_blue"
TRACE_ADD_B2 = "bright_red"

# Graph dimensions
GRAPH_WIDTH = 70    # columns of history
GRAPH_HEIGHT = 10   # rows per graph

HEADER_ART = r"""[bold bright_green]  ██████╗ ██╗███╗   ███╗███╗   ███╗███████╗██████╗ [bold bright_cyan]██████╗ ██╗ █████╗  ██████╗[/]
[bold bright_green]  ██████╔╝██║██╔████╔██║██╔████╔██║█████╗  ██████╔╝[bold bright_cyan]██║  ██║██║███████║██║  ███╗[/]
[bold bright_green]  ██╔══██╗██║██║╚██╔╝██║██║╚██╔╝██║██╔══╝  ██╔══██╗[bold bright_cyan]██║  ██║██║██╔══██║██║   ██║[/]
[bold bright_green]  ██████╔╝██║██║ ╚═╝ ██║██║ ╚═╝ ██║███████╗██║  ██║[bold bright_cyan]██████╔╝██║██║  ██║╚██████╔╝[/]"""


# ---------------------------------------------------------------------------
# Scrolling graph renderer
# ---------------------------------------------------------------------------
GRAPH_CHARS = " ▁▂▃▄▅▆▇█"


def render_graph(history, height, width, min_val, max_val, style, label="",
                 show_bounds=True, warn_lo=None, warn_hi=None):
    """
    Render a scrolling ASCII time-series graph.

    Uses Unicode block characters for sub-row resolution.
    Returns a Text object.
    """
    t = Text()
    val_range = max_val - min_val
    if val_range == 0:
        val_range = 1

    # Get last `width` values, pad with None if not enough
    data = list(history)
    if len(data) < width:
        data = [None] * (width - len(data)) + data
    else:
        data = data[-width:]

    # Build the graph row by row (top to bottom)
    for row in range(height):
        # This row represents values from row_top to row_bottom
        row_top = max_val - (row / height) * val_range
        row_bottom = max_val - ((row + 1) / height) * val_range

        # Y-axis label on first and last row
        if row == 0 and show_bounds:
            lbl = f"{max_val:>6.0f} │"
        elif row == height - 1 and show_bounds:
            lbl = f"{min_val:>6.0f} │"
        elif show_bounds:
            lbl = f"       │"
        else:
            lbl = "│"
        t.append(lbl, style=DIM)

        # Plot each column
        for col in range(width):
            val = data[col]
            if val is None:
                t.append(" ", style=GAUGE_OFF)
                continue

            # How full is this cell (0.0 to 1.0)?
            if val >= row_top:
                fill = 1.0
            elif val <= row_bottom:
                fill = 0.0
            else:
                fill = (val - row_bottom) / (row_top - row_bottom)

            char_idx = int(fill * (len(GRAPH_CHARS) - 1))
            char = GRAPH_CHARS[char_idx]

            # Color based on warning thresholds
            cell_style = style
            if warn_hi is not None and val > warn_hi:
                cell_style = CRIT
            elif warn_lo is not None and val < warn_lo:
                cell_style = CRIT

            t.append(char, style=cell_style)

        t.append("\n")

    # Bottom axis
    if show_bounds and label:
        axis = f"       └{'─' * width}"
        t.append(axis, style=DIM)
        t.append(f"  {label}", style=LABEL)

    return t


def render_dual_graph(hist1, hist2, height, width, min_val, max_val,
                       style1, style2, label1="", label2="",
                       label=""):
    """Render two traces overlaid on the same graph."""
    t = Text()
    val_range = max_val - min_val
    if val_range == 0:
        val_range = 1

    d1 = list(hist1)
    d2 = list(hist2)
    if len(d1) < width:
        d1 = [None] * (width - len(d1)) + d1
    else:
        d1 = d1[-width:]
    if len(d2) < width:
        d2 = [None] * (width - len(d2)) + d2
    else:
        d2 = d2[-width:]

    for row in range(height):
        row_top = max_val - (row / height) * val_range
        row_bottom = max_val - ((row + 1) / height) * val_range

        if row == 0:
            lbl = f"{max_val:>+6.0f} │"
        elif row == height - 1:
            lbl = f"{min_val:>+6.0f} │"
        else:
            lbl = f"       │"
        t.append(lbl, style=DIM)

        for col in range(width):
            v1 = d1[col]
            v2 = d2[col]

            def fill_for(val):
                if val is None:
                    return 0.0
                if val >= row_top:
                    return 1.0
                elif val <= row_bottom:
                    return 0.0
                return (val - row_bottom) / (row_top - row_bottom)

            f1 = fill_for(v1)
            f2 = fill_for(v2)

            # Show whichever trace has more fill, prefer non-zero
            if f1 > 0 and f2 > 0:
                # Both present — show the one with more fill, use combined color
                if f1 >= f2:
                    idx = int(f1 * (len(GRAPH_CHARS) - 1))
                    t.append(GRAPH_CHARS[idx], style=style1)
                else:
                    idx = int(f2 * (len(GRAPH_CHARS) - 1))
                    t.append(GRAPH_CHARS[idx], style=style2)
            elif f1 > 0:
                idx = int(f1 * (len(GRAPH_CHARS) - 1))
                t.append(GRAPH_CHARS[idx], style=style1)
            elif f2 > 0:
                idx = int(f2 * (len(GRAPH_CHARS) - 1))
                t.append(GRAPH_CHARS[idx], style=style2)
            else:
                t.append(" ", style=GAUGE_OFF)

        t.append("\n")

    # Legend
    axis = f"       └{'─' * width}"
    t.append(axis, style=DIM)
    t.append(f"  ", style=DIM)
    t.append(f"█ {label1} ", style=style1)
    t.append(f"█ {label2}", style=style2)

    return t


# ---------------------------------------------------------------------------
# Inline gauge helpers
# ---------------------------------------------------------------------------
def gauge(value, min_v, max_v, width=20, warn=None, crit=None):
    if value is None:
        return Text("─" * width, style=DIM)
    ratio = max(0.0, min(1.0, (value - min_v) / (max_v - min_v)))
    filled = round(ratio * width)
    style = OK
    if crit is not None and value >= crit:
        style = CRIT
    elif warn is not None and value >= warn:
        style = WARN
    t = Text()
    t.append("█" * filled, style=style)
    t.append("░" * (width - filled), style=GAUGE_OFF)
    return t


def val_text(value, fmt=".1f", suffix="", style=VALUE):
    if value is None:
        return Text("---", style=DIM)
    return Text(f"{value:{fmt}}{suffix}", style=style)


def trend_arrow(cur, prev, threshold=0.5):
    if cur is None or prev is None:
        return Text(" ", style=DIM)
    diff = cur - prev
    if abs(diff) < threshold:
        return Text("─", style=DIM)
    if diff > 0:
        return Text("▲", style=WARN)
    return Text("▼", style=COLD)


# ---------------------------------------------------------------------------
# Dashboard state
# ---------------------------------------------------------------------------
class M62Dashboard:
    def __init__(self, ecu, sgbd, history_len=GRAPH_WIDTH):
        self.ecu = ecu
        self.sgbd = sgbd
        self.d = {}
        self.prev = {}
        self.cycle = 0
        self.poll_ms = 0
        self.last_update = None
        self.error = None
        self.start_time = time.time()

        # Rolling history for graphs
        self.h_rpm = deque(maxlen=history_len)
        self.h_icv = deque(maxlen=history_len)
        self.h_int_b1 = deque(maxlen=history_len)
        self.h_int_b2 = deque(maxlen=history_len)
        self.h_add_b1 = deque(maxlen=history_len)
        self.h_add_b2 = deque(maxlen=history_len)

        # Stats tracking
        self.rpm_min = None
        self.rpm_max = None
        self.icv_min = None
        self.icv_max = None
        self.stumble_count = 0
        self._last_rpm = None
        self._in_dip = False

        self.faults = []

    def poll(self):
        self.cycle += 1
        self.error = None
        t0 = time.time()

        try:
            # Every cycle: fast-changing idle values
            self._r("rpm", "STATUS_MOTORDREHZAHL", "STAT_MOTORDREHZAHL_WERT")
            self._r("icv", "STATUS_LL_REGLER", "STATUS_LL_REGLER_WERT")
            self._r("int_b1", "STATUS_LAMBDA_INTEGRATOR_1",
                     "STAT_LAMBDA_INTEGRATOR_1_WERT")
            self._r("int_b2", "STATUS_LAMBDA_INTEGRATOR_2",
                     "STAT_LAMBDA_INTEGRATOR_2_WERT")

            # Every other cycle: slower-changing values
            if self.cycle % 2 == 0:
                self._r("add_b1", "STATUS_LAMBDA_ADD_1",
                         "STAT_LAMBDA_ADD_1_WERT")
                self._r("add_b2", "STATUS_LAMBDA_ADD_2",
                         "STAT_LAMBDA_ADD_2_WERT")
                self._r("mul_b1", "STATUS_LAMBDA_MUL_1",
                         "STAT_LAMBDA_MUL_1_WERT")
                self._r("mul_b2", "STATUS_LAMBDA_MUL_2",
                         "STAT_LAMBDA_MUL_2_WERT")

            # Every 3rd cycle: temps and battery
            if self.cycle % 3 == 0:
                self._r("batt", "STATUS_UBATT", "STAT_UBATT_WERT")
                self._r("coolant", "STATUS_MOTORTEMPERATUR",
                         "STAT_MOTORTEMPERATUR_WERT")
                self._r("maf", "STATUS_LMM", "STATUS_LMM_WERT")
                self._r("throttle", "STATUS_DKP_VOLT",
                         "STATUS_DKP_VOLT_WERT")

            # Every 20th cycle: faults
            if self.cycle % 20 == 1:
                try:
                    results = self.ecu.run_job(self.sgbd, "FS_LESEN")
                    self.faults = [r for r in results[1:] if "F_ORT_NR" in r]
                except EdiabasError:
                    pass

        except Exception as e:
            self.error = str(e)

        self.poll_ms = int((time.time() - t0) * 1000)
        self.last_update = datetime.now()

        # Update history
        self.h_rpm.append(self.g("rpm"))
        self.h_icv.append(self.g("icv"))
        self.h_int_b1.append(self.g("int_b1"))
        self.h_int_b2.append(self.g("int_b2"))
        self.h_add_b1.append(self.g("add_b1"))
        self.h_add_b2.append(self.g("add_b2"))

        # Track stats
        rpm = self.g("rpm")
        icv = self.g("icv")
        if rpm is not None:
            if self.rpm_min is None or rpm < self.rpm_min:
                self.rpm_min = rpm
            if self.rpm_max is None or rpm > self.rpm_max:
                self.rpm_max = rpm

            # Stumble detection: RPM dip > 80rpm below recent average
            if self._last_rpm is not None:
                if rpm < self._last_rpm - 80 and not self._in_dip:
                    self.stumble_count += 1
                    self._in_dip = True
                elif rpm > self._last_rpm - 30:
                    self._in_dip = False
            self._last_rpm = rpm

        if icv is not None:
            if self.icv_min is None or icv < self.icv_min:
                self.icv_min = icv
            if self.icv_max is None or icv > self.icv_max:
                self.icv_max = icv

    def _r(self, key, job, result):
        try:
            val = self.ecu.read_value(self.sgbd, job, result)
            if val is not None:
                self.prev[key] = self.d.get(key)
                self.d[key] = val
        except EdiabasError:
            pass

    def g(self, key, default=None):
        return self.d.get(key, default)

    def trend(self, key, threshold=0.5):
        return trend_arrow(self.d.get(key), self.prev.get(key), threshold)

    # -------------------------------------------------------------------
    # Rendering
    # -------------------------------------------------------------------
    def render(self):
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=6),
            Layout(name="status", size=5),
            Layout(name="graphs"),
            Layout(name="footer", size=1),
        )
        layout["graphs"].split_column(
            Layout(name="rpm_graph", size=GRAPH_HEIGHT + 3),
            Layout(name="icv_graph", size=GRAPH_HEIGHT + 3),
            Layout(name="trim_graph", size=GRAPH_HEIGHT + 3),
        )

        layout["header"] = self._render_header()
        layout["status"] = self._render_status()
        layout["graphs"]["rpm_graph"] = self._render_rpm_graph()
        layout["graphs"]["icv_graph"] = self._render_icv_graph()
        layout["graphs"]["trim_graph"] = self._render_trim_graph()
        layout["footer"] = self._render_footer()

        return layout

    def _render_header(self):
        ts = self.last_update.strftime("%H:%M:%S") if self.last_update else "--:--:--"
        elapsed = time.time() - self.start_time
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)

        info = Text()
        info.append(f"  E39 540i M62", style="bold bright_white")
        info.append(f"  │  ", style=DIM)
        info.append(f"MS41.0 (Siemens)", style=LABEL)
        info.append(f"  │  ", style=DIM)
        info.append(f"{self.sgbd}", style=LABEL)
        info.append(f"  │  ", style=DIM)
        info.append(f"{ts}", style=VALUE)
        info.append(f"  │  ", style=DIM)
        info.append(f"{mins:02d}:{secs:02d}", style=LABEL)

        content = Text.from_markup(HEADER_ART)
        content.append("\n")
        content.append_text(info)

        return Panel(content, title="[bold bright_green]IDLE HUNTING ANALYZER[/]",
                     border_style=BORDER, box=box.DOUBLE, style="on black")

    def _render_status(self):
        t = Table.grid(expand=True, padding=(0, 2))
        t.add_column(width=20)
        t.add_column(width=20)
        t.add_column(width=20)
        t.add_column(width=20)

        # Row 1: Engine vitals
        rpm = self.g("rpm")
        icv = self.g("icv")
        coolant = self.g("coolant")
        batt = self.g("batt")
        batt_v = batt  # MS41 returns voltage in V directly

        rpm_t = Text()
        rpm_t.append("RPM ", style=LABEL)
        if rpm is not None:
            rpm_t.append(f"{rpm:.0f}", style=VALUE)
            rpm_t.append_text(self.trend("rpm", 15))
        else:
            rpm_t.append("---", style=DIM)

        icv_t = Text()
        icv_t.append("ICV ", style=LABEL)
        if icv is not None:
            color = OK if icv < 45 else (WARN if icv < 60 else CRIT)
            icv_t.append(f"{icv:.1f}%", style=color)
            icv_t.append_text(self.trend("icv", 1))
        else:
            icv_t.append("---", style=DIM)

        cool_t = Text()
        cool_t.append("COOL ", style=LABEL)
        if coolant is not None:
            color = OK if 80 <= coolant <= 105 else (WARN if coolant < 80 else CRIT)
            cool_t.append(f"{coolant:.0f}°C", style=color)
        else:
            cool_t.append("---", style=DIM)

        batt_t = Text()
        batt_t.append("BATT ", style=LABEL)
        if batt_v is not None:
            color = OK if 12.0 <= batt_v <= 14.8 else WARN
            batt_t.append(f"{batt_v:.1f}V", style=color)
        else:
            batt_t.append("---", style=DIM)

        t.add_row(rpm_t, icv_t, cool_t, batt_t)

        # Row 2: Trims
        add_b1 = self.g("add_b1")
        add_b2 = self.g("add_b2")
        mul_b1 = self.g("mul_b1")
        mul_b2 = self.g("mul_b2")

        def trim_text(label, v1, v2, l1="B1", l2="B2"):
            txt = Text()
            txt.append(f"{label} ", style=LABEL)
            if v1 is not None:
                c = OK if abs(v1) < 5 else (WARN if abs(v1) < 10 else CRIT)
                txt.append(f"{l1}:{v1:+.1f}", style=c)
            else:
                txt.append(f"{l1}:---", style=DIM)
            txt.append(" ", style=DIM)
            if v2 is not None:
                c = OK if abs(v2) < 5 else (WARN if abs(v2) < 10 else CRIT)
                txt.append(f"{l2}:{v2:+.1f}", style=c)
            else:
                txt.append(f"{l2}:---", style=DIM)
            return txt

        add_t = trim_text("ADD", add_b1, add_b2)
        mul_t = trim_text("MUL", mul_b1, mul_b2)

        # Stats
        stats_t = Text()
        stats_t.append("STUMBLES ", style=LABEL)
        if self.stumble_count > 0:
            stats_t.append(f"{self.stumble_count}", style=CRIT)
        else:
            stats_t.append("0", style=OK)

        swing_t = Text()
        if self.icv_min is not None and self.icv_max is not None:
            sw = self.icv_max - self.icv_min
            color = OK if sw < 5 else (WARN if sw < 10 else CRIT)
            swing_t.append("ICV SWING ", style=LABEL)
            swing_t.append(f"{sw:.1f}%", style=color)
        else:
            swing_t.append("ICV SWING ", style=LABEL)
            swing_t.append("---", style=DIM)

        t.add_row(add_t, mul_t, stats_t, swing_t)

        return Panel(t, title="[bright_cyan]STATUS[/]",
                     border_style=BORDER, box=box.HEAVY, style="on black")

    def _render_rpm_graph(self):
        # Auto-scale RPM around the data
        vals = [v for v in self.h_rpm if v is not None]
        if vals:
            center = sum(vals) / len(vals)
            lo = max(0, center - 200)
            hi = center + 200
        else:
            lo, hi = 500, 900

        graph = render_graph(
            self.h_rpm, GRAPH_HEIGHT, GRAPH_WIDTH,
            min_val=lo, max_val=hi,
            style=TRACE_RPM,
            label=f"RPM (auto-scaled {lo:.0f}-{hi:.0f})",
            warn_lo=lo + 30,
        )
        return Panel(graph, title="[bright_green]ENGINE RPM[/]",
                     border_style=BORDER, box=box.HEAVY, style="on black")

    def _render_icv_graph(self):
        graph = render_graph(
            self.h_icv, GRAPH_HEIGHT, GRAPH_WIDTH,
            min_val=0, max_val=80,
            style=TRACE_ICV,
            label="ICV DUTY CYCLE %",
            warn_hi=55,
        )
        return Panel(graph, title="[bright_yellow]ICV DUTY CYCLE — hunting shows as sawtooth[/]",
                     border_style=BORDER, box=box.HEAVY, style="on black")

    def _render_trim_graph(self):
        graph = render_dual_graph(
            self.h_int_b1, self.h_int_b2,
            GRAPH_HEIGHT, GRAPH_WIDTH,
            min_val=-15, max_val=15,
            style1=TRACE_INT_B1, style2=TRACE_INT_B2,
            label1="Bank 1 (cyl 1-4)", label2="Bank 2 (cyl 5-8)",
            label="LAMBDA INTEGRATOR %",
        )
        return Panel(graph, title="[bright_cyan]LAMBDA INTEGRATORS — rapid oscillation = hunting[/]",
                     border_style=BORDER, box=box.HEAVY, style="on black")

    def _render_footer(self):
        t = Text()
        t.append(f"░ Poll: {self.poll_ms}ms ", style=DIM)
        t.append(f"░ Cycle: {self.cycle} ", style=DIM)
        if self.faults:
            t.append(f"░ DTCs: {len(self.faults)} ", style=CRIT)
        else:
            t.append(f"░ DTCs: 0 ", style=OK)
        if self.rpm_min is not None:
            t.append(f"░ RPM: {self.rpm_min:.0f}-{self.rpm_max:.0f} ", style=LABEL)
        if self.error:
            t.append(f"░ ERR: {self.error[:30]} ", style=CRIT)
        t.append(f"░ Ctrl+C quit ", style=DIM)
        t.append("░" * 30, style="dark_green")
        return t

    def csv_row(self):
        """Return a dict for CSV export."""
        return {
            "timestamp": datetime.now().isoformat(),
            "rpm": self.g("rpm"),
            "icv": self.g("icv"),
            "int_b1": self.g("int_b1"),
            "int_b2": self.g("int_b2"),
            "add_b1": self.g("add_b1"),
            "add_b2": self.g("add_b2"),
            "mul_b1": self.g("mul_b1"),
            "mul_b2": self.g("mul_b2"),
            "coolant": self.g("coolant"),
            "maf": self.g("maf"),
            "throttle": self.g("throttle"),
        }


# ---------------------------------------------------------------------------
# SGBD Detection
# ---------------------------------------------------------------------------
def detect_sgbd(ecu):
    try:
        results = ecu.run_job("D_0012", "IDENT")
        if len(results) > 0 and "VARIANTE" in results[0]:
            return results[0]["VARIANTE"]
    except EdiabasError:
        pass
    for sgbd in ["DM528DS0", "DM52M620", "DM52M621"]:
        try:
            ecu.run_job(sgbd, "IDENT")
            return sgbd
        except EdiabasError:
            continue
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="BIMMERDIAG — M62 Idle Hunting Dashboard")
    parser.add_argument("--sgbd", default=None, help="Override SGBD (default: auto-detect)")
    parser.add_argument("--csv", metavar="FILE", help="Export live data to CSV")
    args = parser.parse_args()

    console = Console()
    console.clear()
    console.print(Text.from_markup(HEADER_ART))
    console.print()
    console.print("  [bright_cyan]M62 IDLE HUNTING ANALYZER[/]")
    console.print()

    with Ediabas() as ecu:
        sgbd = args.sgbd
        if not sgbd:
            console.print("  [bright_cyan]Detecting M62 ECU...[/]", end=" ")
            sgbd = detect_sgbd(ecu)
            if not sgbd:
                console.print("[bright_red]FAILED[/]")
                console.print("  [red]Could not detect ECU. Try --sgbd DM528DS0[/]")
                sys.exit(1)
            console.print(f"[bright_green]{sgbd}[/]")

        console.print(f"  [bright_cyan]SGBD:[/] [bold bright_white]{sgbd}[/]")
        console.print()

        # Splash
        for i in range(30):
            bar = "█" * (i + 1) + "░" * (29 - i)
            console.print(f"\r  [bright_green]{bar}[/]", end="")
            time.sleep(0.02)
        console.print()
        time.sleep(0.3)

        # CSV setup
        csv_fh = None
        csv_writer = None
        if args.csv:
            csv_fields = ["timestamp", "rpm", "icv", "int_b1", "int_b2",
                          "add_b1", "add_b2", "mul_b1", "mul_b2",
                          "coolant", "maf", "throttle"]
            csv_fh = open(args.csv, "w", newline="")
            csv_writer = csv.DictWriter(csv_fh, fieldnames=csv_fields)
            csv_writer.writeheader()

        dash = M62Dashboard(ecu, sgbd)
        dash.poll()

        with Live(dash.render(), console=console, screen=True,
                  refresh_per_second=4) as live:
            try:
                while True:
                    dash.poll()
                    live.update(dash.render())
                    if csv_writer:
                        csv_writer.writerow(dash.csv_row())
            except KeyboardInterrupt:
                pass

        if csv_fh:
            csv_fh.close()
            console.print(f"\n  [bright_green]CSV saved:[/] {args.csv}")

    console.clear()
    console.print()
    console.print(f"  [bright_green]BIMMERDIAG[/] M62 idle analyzer closed.")
    if dash.stumble_count > 0:
        console.print(f"  [bright_yellow]Detected {dash.stumble_count} stumble event(s) "
                       f"during session.[/]")
    if dash.icv_min is not None:
        sw = dash.icv_max - dash.icv_min
        console.print(f"  ICV range: {dash.icv_min:.1f}% — {dash.icv_max:.1f}% "
                       f"(total swing: {sw:.1f}%)")
    if dash.rpm_min is not None:
        console.print(f"  RPM range: {dash.rpm_min:.0f} — {dash.rpm_max:.0f}")
    console.print()


if __name__ == "__main__":
    main()
