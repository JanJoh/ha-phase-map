#!/usr/bin/env python3
"""Draw the house's phase bus diagram from loads.json.

Main fuse on the left, one horizontal bus per phase (L1, L2, L3), each single-phase
load as a box hanging off its bus with its average power, plus an "Unmetered rest"
box (phase total minus all loads on it). 3-phase loads get one conductor per phase
in the phase colour, joined to its own bus with a dot and hopping over the other
buses with an arc. Layout (widths, positions) is computed from the data, so loads
can be added or removed by editing loads.json only.

Usage:  phase_bus.py [loads.json] [out.html]
"""
import json, sys, os
from html import escape

HERE = os.path.dirname(os.path.abspath(__file__))
src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "loads.json")
if len(sys.argv) <= 1 and not os.path.exists(src):
    src = os.path.join(HERE, "loads.example.json")
dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "phase-bus.html")
cfg = json.load(open(src))

PH = ["L1", "L2", "L3"]
BUS_Y = {"L1": 110, "L2": 290, "L3": 470}   # bus heights; boxes hang 34 px below each bus
BW, BH, GAP = 92, 58, 6                     # single-phase box size and spacing
X0 = 168                                     # first box
TP_W, TP_H, TP_GAP = 112, 104, 4             # 3-phase box size and spacing
TP_SP, HOP_R = 13, 5.5                       # conductor spacing and crossing-arc radius
BOX_TOP_3P = 596

tp = cfg.get("three_phase", [])
rows = {}
for p in PH:
    ph = cfg["phases"][p]
    loads = sorted(ph["loads"], key=lambda l: -l["w"])
    tp_here = sum(l.get("per_phase_w", {}).get(p, l["w"] / 3) for l in tp)   # 3-phase loads' share of this phase
    rest = ph.get("unmetered_w", ph["total_w"] - sum(l["w"] for l in loads) - tp_here)
    rows[p] = loads + [{"name": "Unmetered|rest", "w": round(rest), "rest": True}]
row_end = X0 + max(len(r) for r in rows.values()) * (BW + GAP) - GAP
tp_x0 = row_end + 30 + TP_W / 2
tp_centres = [tp_x0 + i * (TP_W + TP_GAP) for i in range(len(tp))]
W = int((tp_centres[-1] + TP_W / 2 if tp else row_end) + 16)
H = BOX_TOP_3P + TP_H + 12 if tp else BUS_Y["L3"] + 34 + BH + 20
BUS_END = W - 18

out = []
a = out.append
a(f'<svg viewBox="0 0 {W} {H}" role="img" aria-labelledby="t d" xmlns="http://www.w3.org/2000/svg">')
a('<title id="t">Phase bus diagram</title><desc id="d">Three phase buses from the main fuse, '
  'each load branching off its phase with its average power; 3-phase loads connect to all three phases.</desc>')
total = sum(cfg["phases"][p]["total_w"] for p in PH)
a(f'<rect class="fuse" x="18" y="{BUS_Y["L1"]-40}" width="104" height="{BUS_Y["L3"]-BUS_Y["L1"]+80}" rx="8"/>')
a(f'<text class="fuse-t" x="70" y="{BUS_Y["L1"]-14}" text-anchor="middle">Main fuse</text>')
a(f'<text class="fuse-s" x="70" y="{BUS_Y["L1"]+2}" text-anchor="middle">{escape(cfg["fuse"])}</text>')
a(f'<text class="fuse-s" x="70" y="{BUS_Y["L3"]+30}" text-anchor="middle">total ≈ {total/1000:.2f} kW</text>')

for i, p in enumerate(PH, 1):
    y, c = BUS_Y[p], f"var(--l{i})"
    a(f'<line class="bus" x1="122" y1="{y}" x2="{BUS_END}" y2="{y}" style="stroke:{c}"/>')
    a(f'<text class="ph" x="134" y="{y-12}" style="fill:{c}">{p}</text>')
    a(f'<text class="ph-avg" x="162" y="{y-12}">≈ {cfg["phases"][p]["total_w"]} W average</text>')
    for j, ld in enumerate(rows[p]):
        x = X0 + j * (BW + GAP); cx = x + BW / 2; top = y + 34
        rest = ld.get("rest")
        a(f'<line class="tap" x1="{cx}" y1="{y}" x2="{cx}" y2="{top}" style="stroke:{c}"/>')
        a(f'<circle class="dot" cx="{cx}" cy="{y}" r="4" style="fill:{c}"/>')
        a(f'<rect class="box{" rest" if rest else ""}" x="{x}" y="{top}" width="{BW}" height="{BH}" rx="6" style="stroke:{c}"/>')
        lines = ld["name"].split("|")
        ty = top + 17 if len(lines) == 2 else top + 24
        for k, ln in enumerate(lines[:2]):
            a(f'<text class="nm" x="{cx}" y="{ty+k*13}" text-anchor="middle">{escape(ln)}</text>')
        a(f'<text class="w{" zero" if ld["w"] == 0 else ""}" x="{cx}" y="{top+BH-8}" text-anchor="middle">{ld["w"]} W</text>')

for xc, ld in zip(tp_centres, tp):
    cls = "assumed" if ld.get("assumed") else "metered"
    for k, p in enumerate(PH):
        x = xc + (k - 1) * TP_SP
        d = f"M{x},{BUS_Y[p]}"
        for q in PH[k + 1:]:                      # hop over every bus below this phase's own
            yb = BUS_Y[q]
            d += f" V{yb-HOP_R} A{HOP_R},{HOP_R} 0 0 1 {x},{yb+HOP_R}"
        d += f" V{BOX_TOP_3P}"
        a(f'<path class="cond {cls}" d="{d}" style="stroke:var(--l{k+1})"/>')
    for k, p in enumerate(PH):
        a(f'<circle class="dot" cx="{xc+(k-1)*TP_SP}" cy="{BUS_Y[p]}" r="5" style="fill:var(--l{k+1})"/>')
    a(f'<rect class="box tp-{cls}" x="{xc-TP_W/2}" y="{BOX_TOP_3P}" width="{TP_W}" height="{TP_H}" rx="6"/>')
    txt = [("nm t3" + (" s" if cls == "assumed" else ""), ld["name"])] + [("nm", l) for l in ld.get("lines", [])] \
        + [("w" + (" zero" if ld["w"] == 0 else ""), f'{ld["w"]} W')] + [("nm small", n) for n in ld.get("notes", [])]
    for k, (c, t) in enumerate(txt[:6]):
        a(f'<text class="{c}" x="{xc}" y="{BOX_TOP_3P+19+k*16}" text-anchor="middle">{escape(t)}</text>')
a("</svg>")

notes = cfg.get("page_notes", [])
if tp:
    notes = ["<b>3-phase loads</b> (" + ", ".join(escape(l["name"]) for l in tp) + ") connect to all three phases: one line per "
             "phase in the phase's colour, joined to its own bus with a dot. A small arc means the line crosses a bus "
             "without connecting to it. Dashed lines are assumed, not measured."] + notes
page = open(os.path.join(HERE, "template.html")).read()
page = (page.replace("%%SVG%%", "\n".join(out)).replace("%%PERIOD%%", escape(cfg["period"]))
            .replace("%%MINW%%", str(min(W, 1100) - 100)).replace("%%MAXW%%", str(W))
            .replace("%%NOTES%%", "\n    ".join(f"<p>{n}</p>" for n in notes)))
open(dst, "w").write(page)
print(f"wrote {dst}: {sum(len(r) for r in rows.values())} single-phase boxes, {len(tp)} 3-phase loads, viewBox {W}x{H}")
