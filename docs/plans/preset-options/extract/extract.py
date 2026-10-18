"""Extract the fins from VWBD A-20-1 (see ../README.md §1.5). Usage: python3 -I extract.py img.rgb fins_raw.json"""

import json
import sys
from collections import deque

import numpy as np

W, H = 5100, 3300
a = np.fromfile(sys.argv[1], dtype=np.uint8).reshape(H, W, 3).astype(np.float32)
OX, OY = 275.5, 530.5  # plan outer border (px), = canopy apron corner
PPI = 300 / 36  # 1" = 3'  at 300 dpi -> 8.333 px per inch
x0, x1, y0, y1 = 278, 1374, 533, 2504
img = a[y0:y1, x0:x1]
REF = {
    "light-blue": (166, 206, 227),
    "dark-blue": (31, 120, 180),
    "light-green": (178, 223, 138),
    "dark-green": (80, 152, 44),
    "cyan": (104, 200, 192),
    "pink": (248, 154, 153),
    "red": (227, 26, 28),
    "light-orange": (253, 191, 111),
    "dark-orange": (250, 127, 10),
    "lavender": (202, 178, 214),
    "purple": (106, 61, 154),
}
DEPTH = {
    "light-blue": 14,
    "dark-blue": 15,
    "light-green": 16,
    "dark-green": 17,
    "cyan": 18,
    "pink": 19,
    "red": 20,
    "light-orange": 21,
    "dark-orange": 22,
    "lavender": 23,
    "purple": 24.5,
}
KEYN = {
    "light-blue": 160,
    "dark-blue": 88,
    "light-green": 52,
    "dark-green": 44,
    "cyan": 48,
    "pink": 40,
    "red": 38,
    "light-orange": 42,
    "dark-orange": 38,
    "lavender": 48,
    "purple": 52,
}
names = list(REF)
refs = np.array([REF[n] for n in names], dtype=np.float32)
flat = img.reshape(-1, 3)
d = np.sqrt(((flat[:, None, :] - refs[None, :, :]) ** 2).sum(axis=2))
best = d.argmin(axis=1)
bd = d.min(axis=1)
mx = flat.max(axis=1)
mn = flat.min(axis=1)
chroma = mx - mn
ok = (bd < 48) & (chroma > 24)
lab = np.where(ok, best, -1).reshape(img.shape[:2])
h, w = lab.shape
seen = np.zeros((h, w), bool)
fins = []
for ci, name in enumerate(names):
    mask = lab == ci
    ys, xs = np.nonzero(mask)
    for sy, sx in zip(ys, xs):
        if seen[sy, sx]:
            continue
        q = deque([(sy, sx)])
        seen[sy, sx] = True
        pts = []
        while q:
            y, x = q.popleft()
            pts.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w and not seen[yy, xx] and mask[yy, xx]:
                        seen[yy, xx] = True
                        q.append((yy, xx))
        if len(pts) < 25:
            continue
        P = np.array(pts, dtype=np.float64)
        c = P.mean(axis=0)
        U, Sv, Vt = np.linalg.svd(P - c, full_matrices=False)
        axis = Vt[0]
        proj = (P - c) @ axis
        perp = (P - c) @ Vt[1]
        L = proj.max() - proj.min()
        Wd = perp.max() - perp.min()
        fins.append(
            {
                "color": name,
                "n": len(pts),
                "cy": c[0] + y0,
                "cx": c[1] + x0,
                "L": L,
                "Wd": Wd,
                "ay": axis[0],
                "ax": axis[1],
            }
        )
with open(sys.argv[2], "w") as fh:
    json.dump(fins, fh)
from collections import Counter

cnt = Counter(f["color"] for f in fins)
for n in names:
    Ls = [f["L"] for f in fins if f["color"] == n]
    print(
        f"{n:13} comps={cnt[n]:4} key={KEYN[n]:4}  L(px) med={np.median(Ls) if Ls else 0:.1f} min={min(Ls) if Ls else 0:.1f} max={max(Ls) if Ls else 0:.1f}"
    )
print("total", len(fins), "key total", sum(KEYN.values()))
