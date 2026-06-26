"""Palettize the baked texture (numpy only). Three sources of palette:

  AUTO                 -> median-cut a palette FROM the image (the default).
  PICO8 / DB16 / DB32  -> snap to a built-in curated retro palette.
  CUSTOM               -> snap to a user .hex / .pal / .gpl palette file.

All clustering and nearest-colour matching happen in **OKLab** (perceptually uniform), not
RGB: equal RGB distances don't look equally different, so RGB median-cut yields muddy
palettes and RGB nearest-match drifts hue. OKLab fixes both. The output written back is
still sRGB-encoded (same space the bake produced), so the rest of the pipeline is unchanged.

Blender bundles numpy but NOT Pillow, so median-cut is implemented directly. The core
functions take/return plain numpy arrays and have NO Blender dependency, so they're
unit-tested standalone in tests/test_quantize.py; `run` is the thin Blender wrapper.
"""

import numpy as np

# Shared OKLab maths. Package import normally; path-load fallback for standalone tests.
try:
    from . import colour
except ImportError:
    import importlib.util as _il, os as _os, sys as _sys
    _cp = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "colour.py")
    _cs = _il.spec_from_file_location("lofi_colour", _cp)
    colour = _il.module_from_spec(_cs)
    _sys.modules["lofi_colour"] = colour
    _cs.loader.exec_module(colour)


# --------------------------------------------------------------------------- #
# working-space helpers (sRGB <-> the space we cluster/match in)
# --------------------------------------------------------------------------- #
def _to_space(rgb, space):
    return colour.srgb_to_oklab(rgb) if space == "oklab" else rgb.astype(np.float32)


def _from_space(coords, space):
    return colour.oklab_to_srgb(coords) if space == "oklab" else coords.astype(np.float32)


def _nearest_map(coords, pal_coords, pal_out, chunk=50000):
    """For each row of `coords`, find the nearest `pal_coords` row and emit the matching
    `pal_out` row. CHUNKED: a single (N,K,3) broadcast on a hi-fi texture (2048² px × 256
    colours) would allocate tens of GB."""
    out = np.empty((len(coords), pal_out.shape[1]), dtype=pal_out.dtype)
    pc = pal_coords[None, :, :]
    for i in range(0, len(coords), chunk):
        block = coords[i:i + chunk]
        d = ((block[:, None, :] - pc) ** 2).sum(axis=2)
        out[i:i + chunk] = pal_out[np.argmin(d, axis=1)]
    return out


# --------------------------------------------------------------------------- #
# AUTO: median-cut a palette out of the image
# --------------------------------------------------------------------------- #
def median_cut_palette(coords, n_colors):
    """Median-cut `coords` (N,3 floats) into <= n_colors boxes in whatever space the
    coords are in. Returns (palette (K,3), labels (N,)) where K <= n_colors."""
    n = len(coords)
    if n == 0:
        return np.zeros((0, 3), dtype=coords.dtype), np.zeros(0, dtype=np.intp)

    boxes = [np.arange(n)]
    while len(boxes) < n_colors:
        # Choose the splittable box with the largest single-channel extent.
        best_i, best_axis, best_extent = -1, 0, -1.0
        for i, b in enumerate(boxes):
            if len(b) < 2:
                continue
            px = coords[b]
            extents = px.max(axis=0) - px.min(axis=0)
            axis = int(np.argmax(extents))
            if extents[axis] > best_extent:
                best_i, best_axis, best_extent = i, axis, float(extents[axis])
        if best_i < 0:
            break  # nothing left to split (e.g. a single-colour image)

        b = boxes.pop(best_i)
        order = np.argsort(coords[b, best_axis], kind="stable")
        b = b[order]
        mid = len(b) // 2
        boxes.append(b[:mid])
        boxes.append(b[mid:])

    palette = np.zeros((len(boxes), 3), dtype=np.float64)
    labels = np.zeros(n, dtype=np.intp)
    for i, b in enumerate(boxes):
        if len(b) == 0:
            continue
        palette[i] = coords[b].mean(axis=0)
        labels[b] = i
    return palette, labels


def quantize_rgb(rgb, n_colors, chunk=50000, space="oklab"):
    """Return `rgb` (N,3 sRGB in [0,1]) remapped to its <= n_colors median-cut palette.

    Cluster + nearest-match in `space` (default OKLab), but emit sRGB palette colours.
    Each pixel is assigned to its NEAREST palette colour, NOT its median-cut box mean:
    box membership can hand a pixel a different-hue average (a duck's belly orange landing
    in a box that also spans the red bill), even when a closer same-hue entry exists."""
    if len(rgb) == 0:
        return rgb
    coords = _to_space(rgb, space)
    pal_coords, _ = median_cut_palette(coords, n_colors)
    if len(pal_coords) == 0:
        return rgb
    pal_srgb = _from_space(pal_coords, space)
    return _nearest_map(coords, pal_coords, pal_srgb, chunk).astype(rgb.dtype)


# --------------------------------------------------------------------------- #
# FIXED: snap to a given palette
# --------------------------------------------------------------------------- #
def snap_to_palette(rgb, palette, chunk=50000, space="oklab"):
    """Remap each `rgb` pixel to its nearest entry in `palette` (both N,3 sRGB in [0,1]),
    matched in `space` (default OKLab so the match is perceptual). Output uses only the
    palette's own colours -- the whole point of a fixed/curated palette."""
    if len(rgb) == 0 or len(palette) == 0:
        return rgb
    coords = _to_space(rgb, space)
    pal_coords = _to_space(palette.astype(np.float32), space)
    return _nearest_map(coords, pal_coords, palette.astype(rgb.dtype), chunk).astype(rgb.dtype)


def _hex_to_rgb01(token):
    t = token.strip().lstrip("#")
    return [int(t[0:2], 16) / 255.0, int(t[2:4], 16) / 255.0, int(t[4:6], 16) / 255.0]


def _palette_from_hexes(hexes):
    return np.array([_hex_to_rgb01(x) for x in hexes.split()], dtype=np.float32)


# Curated retro palettes (verified hex). PICO-8 (Lexaloffle); DB16/DB32 (DawnBringer).
_BUILTIN_PALETTES = {
    "PICO8": _palette_from_hexes(
        "000000 1D2B53 7E2553 008751 AB5236 5F574F C2C3C7 FFF1E8 "
        "FF004D FFA300 FFEC27 00E436 29ADFF 83769C FF77A8 FFCCAA"),
    "DB16": _palette_from_hexes(
        "140C1C 442434 30346D 4E4A4E 854C30 346524 D04648 757161 "
        "597DCE D27D2C 8595A1 6DAA2C D2AA99 6DC2CA DAD45E DEEED6"),
    "DB32": _palette_from_hexes(
        "000000 222034 45283C 663931 8F563B DF7126 D9A066 EEC39A "
        "FBF236 99E550 6ABE30 37946E 4B692F 524B24 323C39 3F3F74 "
        "306082 5B6EE1 639BFF 5FCDE4 CBDBFC FFFFFF 9BADB7 847E87 "
        "696A6A 595652 76428A AC3232 D95763 D77BBA 8F974A 8A6F30"),
}


def _is_int_triple(parts):
    return len(parts) >= 3 and all(p.lstrip("-").isdigit() for p in parts[:3])


def load_palette_file(path):
    """Parse a palette file into an (P,3) float32 array in [0,1]. Supports Lospec `.hex`
    (one 6-digit hex per line), JASC `.pal` (JASC-PAL / version / count / 'R G B' lines),
    and GIMP `.gpl` ('r g b name' lines). Format is sniffed from content (tolerant of a
    mislabelled extension). Raises a clear ValueError on empty / unparseable input."""
    try:
        with open(path, "r", errors="ignore") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError as e:
        raise ValueError(f"palette file unreadable: {path} — {e}")
    if not lines:
        raise ValueError(f"palette file empty: {path}")

    head = lines[0].upper()
    cols = []
    if head.startswith("JASC-PAL") or head.startswith("GIMP PALETTE"):
        for ln in lines[1:]:                 # both are 'R G B [...]' integer rows
            if ln.startswith("#") or ln.lower().startswith(("name:", "columns:")):
                continue
            parts = ln.split()
            if _is_int_triple(parts):
                cols.append([int(parts[0]), int(parts[1]), int(parts[2])])
        if cols:
            return np.array(cols, dtype=np.float32) / 255.0
    # else: hex-per-line (also tolerate raw 'R G B' integer rows / '#'-prefixed hex)
    for ln in lines:
        tok = ln.lstrip("#").strip()
        if len(tok.split()) == 1 and len(tok) >= 6 and \
                all(c in "0123456789abcdefABCDEF" for c in tok[:6]):
            cols.append(_hex_to_rgb01(tok))
        elif _is_int_triple(ln.split()):
            p = ln.split()
            cols.append([int(p[0]) / 255.0, int(p[1]) / 255.0, int(p[2]) / 255.0])
    if not cols:
        raise ValueError(f"palette file has no parseable colours: {path}")
    return np.array(cols, dtype=np.float32)


def resolve_palette(mode, settings):
    """Mode -> (P,3) sRGB palette. Built-ins are returned directly; CUSTOM loads a file
    (resolving a Blender '//' relative path). Raises ValueError on a misconfiguration."""
    if mode in _BUILTIN_PALETTES:
        return _BUILTIN_PALETTES[mode]
    if mode == "CUSTOM":
        path = (getattr(settings, "custom_palette_path", "") or "").strip()
        if not path:
            raise ValueError("palette mode CUSTOM but no custom palette file set")
        try:
            import bpy
            path = bpy.path.abspath(path)
        except Exception:  # noqa: BLE001  (non-Blender / headless contexts)
            pass
        return load_palette_file(path)
    raise ValueError(f"unknown palette_mode: {mode}")


def run(image, settings):
    mode = getattr(settings, "palette_mode", "AUTO")
    n = max(2, settings.palette_colors)
    w, h = image.size
    flat = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    px = flat.reshape(-1, 4)

    mask = px[:, 3] > 0.0           # only quantize opaque (baked) texels
    if not mask.any():
        mask = np.ones(len(px), dtype=bool)

    if mode == "AUTO":
        px[mask, :3] = quantize_rgb(px[mask, :3], n)
        label = f"<= {n} colours (median-cut, OKLab)"
    else:
        palette = resolve_palette(mode, settings)
        px[mask, :3] = snap_to_palette(px[mask, :3], palette)
        label = f"{mode} palette ({len(palette)} colours)"

    image.pixels.foreach_set(px.ravel())
    image.update()
    print(f"lofi.pixelate: quantized to {label}")
