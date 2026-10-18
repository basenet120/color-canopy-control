"""Find the 270 lights on the 10-inch lattice and each panel's 7/8 orientation. Usage: python3 -I lattice.py img.rgb lights.json"""

import json
import sys

import numpy as np

W, H = 5100, 3300
a = np.fromfile(sys.argv[1], dtype=np.uint8).reshape(H, W, 3).astype(np.float32)
OX, OY = 275.5, 530.5
PPI = 300 / 36
avg = a.mean(axis=2)
chroma = a.max(axis=2) - a.min(axis=2)
grey = (avg > 60) & (avg < 150) & (chroma < 30)


def score(xin, yin):
    px = OX + xin * PPI
    py = OY + yin * PPI
    X, Y = round(px), round(py)
    return int(grey[Y - 7 : Y + 8, X - 7 : X + 8].sum())


res = {}
lights = []
for r in range(3):
    for c in range(3):
        mx = 6 + 40 * c
        my = 6 + 75 * r
        pat = []
        for k, lx in enumerate((5, 15, 25, 35)):
            s8 = [score(mx + lx, my + ly) for ly in np.arange(2.5, 73, 10)]
            s7 = [score(mx + lx, my + ly) for ly in np.arange(7.5, 68, 10)]
            is8 = min(s8) > min(s7)
            pat.append(8 if is8 else 7)
            ys = np.arange(2.5, 73, 10) if is8 else np.arange(7.5, 68, 10)
            for ly in ys:
                lights.append(
                    {
                        "panel": f"P-0{3 * r + c + 1}",
                        "r": r,
                        "c": c,
                        "col": k,
                        "x": mx + lx,
                        "y": my + float(ly),
                    }
                )
            if k == 0:
                lo = (min(s8), min(s7))
        res[f"P-0{3 * r + c + 1}"] = pat
        print(f"P-0{3 * r + c + 1} (row {r}, col {c}) columns 5/15/25/35 in = {pat}  sum={sum(pat)}")
print("total lights", len(lights))
with open(sys.argv[2], "w") as fh:
    json.dump(lights, fh)
# sanity: grey score at a non-light point vs light point
print("score at a light", score(6 + 5, 6 + 7.5), " at a blank point", score(6 + 10, 6 + 20))
