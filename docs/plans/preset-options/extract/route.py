"""Trace the strand routing between light runs per panel. Usage: python3 -I route.py img.rgb lights.json"""

import json
import sys

import numpy as np

W, H = 5100, 3300
a = np.fromfile(sys.argv[1], dtype=np.uint8).reshape(H, W, 3).astype(np.float32)
OX, OY = 275.5, 530.5
PPI = 300 / 36
avg = a.mean(axis=2)
chroma = a.max(axis=2) - a.min(axis=2)
line = (avg > 120) & (avg < 215) & (chroma < 25)  # the light-grey strand line
with open(sys.argv[2]) as fh:
    L = json.load(fh)


def frac(p, q):
    (x1, y1), (x2, y2) = p, q
    n = 60
    hit = 0
    tot = 0
    for t in np.linspace(0.2, 0.8, n):
        x = OX + (x1 + (x2 - x1) * t) * PPI
        y = OY + (y1 + (y2 - y1) * t) * PPI
        X, Y = round(x), round(y)
        tot += 1
        hit += bool(line[Y - 1 : Y + 2, X - 1 : X + 2].any())
    return hit / tot


for pn in [f"P-0{i}" for i in range(1, 10)]:
    ls = [l for l in L if l["panel"] == pn]
    cols = {k: sorted([(l["x"], l["y"]) for l in ls if l["col"] == k], key=lambda p: p[1]) for k in range(4)}
    out = []
    # within-column continuity
    within = [np.mean([frac(cols[k][i], cols[k][i + 1]) for i in range(len(cols[k]) - 1)]) for k in range(4)]
    for k in range(3):
        top = frac(cols[k][0], cols[k + 1][0])
        bot = frac(cols[k][-1], cols[k + 1][-1])
        out.append(f"c{k}-c{k + 1}: top {top:.2f} bottom {bot:.2f}")
    print(pn, "within", [round(w, 2) for w in within], "|", " ; ".join(out))
