"""
Turn black-on-white line drawings into crisp VECTOR line art (SVG) in the site's ink colours,
with every line drawn at exactly the same thickness on screen.

How: each drawing is resized so 1 CSS px on the page = SCALE px (from its displayed size in
the layout), lines are thinned to a 1-px skeleton, the skeleton is traced into polylines,
smoothed (removes pixel stair-steps), simplified, and written as smooth Bézier paths with a
non-scaling LINE_CSS px stroke — so they stay sharp at any zoom / pixel density.
Every drawing is kept whole: cropped to its own lines plus a clear margin, never cropped
to a frame. Lines that run into the source canvas edge are stopped at an irregular,
sketch-like boundary just inside it.

Run:  python3 tools/colour_line_art.py <dir-with-line-pngs> [names…]
"""
import sys, os
import numpy as np
import cv2
from PIL import Image, ImageOps

HERE = os.path.dirname(__file__)
IMG = os.path.join(HERE, "..", "img")

SCALE = 2.5        # trace resolution: px per CSS px
LINE_CSS = 1       # line weight on screen, CSS px — identical 1px for every drawing
MIN_LEN_CSS = 2.0  # keep fine details, only drop 1-2px dust specks
SPUR_CSS = 1.2     # only prune 1-2px skeleton burrs, keep all real line ends and details
SMOOTH = 1.15      # light gaussian smoothing — kills pixel stair-steps without melting corners
SIMPLIFY = 0.18    # tight Douglas–Peucker tolerance — preserves crisp detail
PAD = 0.02         # clear margin around the lines, fraction of the longer side

# name → (displayed size in CSS px at a 1440px viewport, ("h"|"w", px) — matches the CSS —
#         and ink colour)
JOBS = {
    "stairwell": (("h", 870), "#5c6344"),   # olive, deepened to read on orange
    "bucket":    (("h", 365), "#ffffff"),
    "staircase": (("h", 411), "#a89a00"),   # yellow, deepened to read on mint
    "hallway":   (("h", 411), "#f49610"),
    "handrail":  (("h", 411), "#4f5638"),   # olive, deepened to read on khaki
    "courtyard": (("h", 381), "#a38c45"),   # sand, deepened to read on mint
    "fountain":  (("h", 324), "#a38c45"),
    "splash":    (("w", 550), "#5c6344"),
    "bubbles":   (("w", 1376), "#f49610"),
}


# ------------------------------------------------------------------ raster prep
def thin(b):
    """Zhang–Suen thinning on a 0/1 uint8 array."""
    img = b.copy().astype(np.uint8)
    while True:
        changed = False
        for step in (0, 1):
            p = np.pad(img, 1)
            P2, P3, P4 = p[:-2, 1:-1], p[:-2, 2:], p[1:-1, 2:]
            P5, P6, P7 = p[2:, 2:], p[2:, 1:-1], p[2:, :-2]
            P8, P9 = p[1:-1, :-2], p[:-2, :-2]
            nb = [P2, P3, P4, P5, P6, P7, P8, P9]
            B = sum(n.astype(np.int32) for n in nb)
            seq = nb + [P2]
            A = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.int32) for i in range(8))
            if step == 0:
                c = (P2 * P4 * P6 == 0) & (P4 * P6 * P8 == 0)
            else:
                c = (P2 * P4 * P8 == 0) & (P2 * P6 * P8 == 0)
            m = (img == 1) & (B >= 2) & (B <= 6) & (A == 1) & c
            if m.any():
                img[m] = 0
                changed = True
        if not changed:
            return img


def organic_trim(g, inset=0.05, seed=0):
    """If lines run into the canvas edge, stop them at an irregular, soft-cornered boundary
    just inside it, so the drawing ends like a sketch instead of a cropped rectangle."""
    a = np.asarray(g, dtype=np.uint8)
    H, W = a.shape
    e = max(3, int(0.006 * max(W, H)))
    ink = a < 150
    if not (ink[:e].any() or ink[-e:].any() or ink[:, :e].any() or ink[:, -e:].any()):
        return g
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    nx = (xx - W / 2) / (W / 2 * (1 - inset))
    ny = (yy - H / 2) / (H / 2 * (1 - inset))
    th = np.arctan2(ny, nx)
    wob = 1 + sum(rng.uniform(.01, .035) * np.sin(k * th + rng.uniform(0, 6.3)) for k in (3, 5, 8, 13))
    r = (np.abs(nx) ** 4 + np.abs(ny) ** 4) ** .25          # soft-cornered rectangle
    out = a.copy()
    out[r > (1 - .04) * wob] = 255
    return Image.fromarray(out)


# ------------------------------------------------------------------ skeleton → polylines
NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def trace(sk):
    """Walk a 1-px skeleton into polylines (lists of (x, y)).
    Diagonal steps are only taken when no 4-connected step creates a 3-clique (L-corner),
    so stair-steps on curves have degree 2 instead of looking like 3-way junctions."""
    on = set(zip(*np.nonzero(sk)))

    def nbrs(p):
        y, x = p
        n4 = {(dy, dx) for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)) if (y + dy, x + dx) in on}
        out = [(y + dy, x + dx) for dy, dx in n4]
        for dy, dx in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            if (y + dy, x + dx) in on and (dy, 0) not in n4 and (0, dx) not in n4:
                out.append((y + dy, x + dx))
        return out

    # Iterative spur pruning on the m-connected graph
    spur_lim = SPUR_CSS * SCALE
    for _ in range(3):
        deg = {p: len(nbrs(p)) for p in on}
        ends = [p for p, d in deg.items() if d == 1]
        removed = False
        for e in ends:
            if e not in on:
                continue
            chain = [e]
            prev, cur = None, e
            while True:
                nb = [q for q in nbrs(cur) if q != prev]
                if len(nb) != 1:
                    break
                nxt = nb[0]
                chain.append(nxt)
                if len(chain) > spur_lim or len(nbrs(nxt)) != 2:
                    break
                prev, cur = cur, nxt
            # Remove only if it terminated at a junction (deg >= 3) and is short
            if len(chain) <= spur_lim and len(nbrs(chain[-1])) >= 3:
                for p in chain[:-1]:
                    on.discard(p)
                removed = True
        if not removed:
            break

    deg = {p: len(nbrs(p)) for p in on}
    nodes = {p for p, d in deg.items() if d != 2}
    seen_edge = set()
    used = set()
    paths = []

    def walk(start, nxt):
        path = [start, nxt]
        prev, cur = start, nxt
        while cur not in nodes:
            used.add(cur)
            cand = [q for q in nbrs(cur) if q != prev and (q in nodes or q not in used)]
            if not cand:
                break
            prev, cur = cur, cand[0]
            path.append(cur)
            if cur == start:
                break
        return path

    for n in nodes:
        for q in nbrs(n):
            if (n, q) in seen_edge or (q not in nodes and q in used):
                continue
            path = walk(n, q)
            seen_edge.add((n, q))
            seen_edge.add((path[-1], path[-2]))
            is_free = (deg[n] == 1 and deg[path[-1]] == 1)
            is_spur = (deg[n] == 1 or deg[path[-1]] == 1)
            paths.append((path, is_free, is_spur))
    # closed loops with no nodes (e.g. bubbles)
    for p in list(on):
        if p in used or p in nodes:
            continue
        nb = nbrs(p)
        if not nb:
            continue
        used.add(p)
        path = walk(p, nb[0])
        paths.append((path + [p], True, False))
    return [([(x, y) for y, x in path], is_free, is_spur) for path, is_free, is_spur in paths]


def smooth_pts(pts, sigma):
    a = np.array(pts, float)
    if len(a) < 5:
        return a
    closed = np.hypot(*(a[0] - a[-1])) < 1.5
    r = int(3 * sigma)
    k = np.exp(-np.arange(-r, r + 1) ** 2 / (2 * sigma ** 2)); k /= k.sum()
    if closed:
        ext = np.concatenate([a[-r - 1:-1], a, a[1:r + 1]])
        out = np.stack([np.convolve(ext[:, i], k, "valid") for i in (0, 1)], 1)
    else:
        ext = np.concatenate([np.repeat(a[:1], r, 0), a, np.repeat(a[-1:], r, 0)])
        out = np.stack([np.convolve(ext[:, i], k, "valid") for i in (0, 1)], 1)
        out[0], out[-1] = a[0], a[-1]                     # keep the true ends
    return out


def to_path(a):
    """Polyline → smooth Catmull-Rom cubic Bézier path data."""
    if len(a) < 2:
        return ""
    f = lambda v: f"{v:.1f}"
    if len(a) == 2:
        return f"M{f(a[0][0])} {f(a[0][1])}L{f(a[1][0])} {f(a[1][1])}"
    p = np.concatenate([a[:1], a, a[-1:]])
    d = [f"M{f(a[0][0])} {f(a[0][1])}"]
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        c1 = p1 + (p2 - p0) / 6
        c2 = p2 - (p3 - p1) / 6
        d.append(f"C{f(c1[0])} {f(c1[1])} {f(c2[0])} {f(c2[1])} {f(p2[0])} {f(p2[1])}")
    return "".join(d)


def vectorise(gray, out_size):
    # Upscale 2x with cubic interpolation before thresholding so close parallel lines
    # (broom bristles, hatching, leaf veins) stay separated and never fuse into bead-loops.
    W0, H0 = gray.size
    UP = 2 if max(W0, H0) < 1800 else 1
    if UP > 1:
        gray = gray.resize((W0 * UP, H0 * UP), Image.BICUBIC)
    W1, H1 = gray.size
    W, H = out_size
    sx, sy = W / W1, H / H1
    g = np.asarray(gray, dtype=np.uint8)
    ink = (g < 170).astype(np.uint8)
    sk = thin(ink)
    out = []
    for path, is_free, is_spur in trace(sk):
        a = np.array(path, float)
        a[:, 0] *= sx
        a[:, 1] *= sy
        L = np.hypot(*np.diff(a, axis=0).T).sum() if len(a) > 1 else 0
        if is_spur and not is_free and L < SPUR_CSS * SCALE:
            continue                                      # tiny 1px skeleton burr
        if is_free and L < MIN_LEN_CSS * SCALE:
            continue                                      # tiny dust speck
        a = smooth_pts(a, SMOOTH)
        a = cv2.approxPolyDP(a.astype(np.float32).reshape(-1, 1, 2), SIMPLIFY, False).reshape(-1, 2)
        out.append(to_path(a.astype(float)))
    return out


def write_svg(name, paths, size, colour):
    W, H = size
    s = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
         f'width="{W / SCALE:.0f}" height="{H / SCALE:.0f}">'
         f'<path fill="none" stroke="{colour}" stroke-width="{LINE_CSS}" stroke-linecap="round" '
         f'stroke-linejoin="round" vector-effect="non-scaling-stroke" d="{"".join(paths)}"/></svg>')
    with open(os.path.join(IMG, name + "-line.svg"), "w") as fh:
        fh.write(s)
    print("wrote", name + "-line.svg", size, f"{len(s) // 1024} KB", len(paths), "lines")


def target(size_ar, disp):
    w0, h0 = size_ar
    axis, css = disp
    if axis == "h":
        h = round(css * SCALE); return (round(h * w0 / h0), h)
    w = round(css * SCALE); return (w, round(w * h0 / w0))


if __name__ == "__main__":
    src_dir = sys.argv[1]
    only = set(sys.argv[2:])
    for name, (disp, colour) in JOBS.items():
        if only and name not in only:
            continue
        p = os.path.join(src_dir, name + ".png")
        if not os.path.exists(p):
            print("skip", name); continue
        g = Image.open(p).convert("L")
        bb = ImageOps.invert(g).point(lambda v: 255 if v > 45 else 0).getbbox()
        g = g.crop(bb)
        m = max(6, round(PAD * max(g.size)))
        g = ImageOps.expand(g, border=m, fill=255)          # whole drawing + clear margin
        out_size = target(g.size, disp)
        write_svg(name, vectorise(g, out_size), out_size, colour)
