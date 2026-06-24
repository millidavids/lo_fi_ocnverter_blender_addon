"""Palettize the baked texture to N colours via median-cut (numpy only).

Blender bundles numpy but NOT Pillow, so we implement median-cut directly. The
core functions take/return plain numpy arrays and have NO Blender dependency, so
they're unit-tested standalone in tests/test_quantize.py. `run` is the thin
Blender wrapper that reads `image.pixels`, quantizes the opaque pixels, and
writes them back. Baking at the target size already gives pixel-art resolution.
"""

import numpy as np


def median_cut_palette(rgb, n_colors):
    """Median-cut `rgb` (N,3 floats in [0,1]) into <= n_colors boxes.

    Returns (palette (K,3), labels (N,)) where K <= n_colors.
    """
    n = len(rgb)
    if n == 0:
        return np.zeros((0, 3), dtype=rgb.dtype), np.zeros(0, dtype=np.intp)

    boxes = [np.arange(n)]
    while len(boxes) < n_colors:
        # Choose the splittable box with the largest single-channel extent.
        best_i, best_axis, best_extent = -1, 0, -1.0
        for i, b in enumerate(boxes):
            if len(b) < 2:
                continue
            px = rgb[b]
            extents = px.max(axis=0) - px.min(axis=0)
            axis = int(np.argmax(extents))
            if extents[axis] > best_extent:
                best_i, best_axis, best_extent = i, axis, float(extents[axis])
        if best_i < 0:
            break  # nothing left to split

        b = boxes.pop(best_i)
        order = np.argsort(rgb[b, best_axis], kind="stable")
        b = b[order]
        mid = len(b) // 2
        boxes.append(b[:mid])
        boxes.append(b[mid:])

    palette = np.zeros((len(boxes), 3), dtype=np.float64)
    labels = np.zeros(n, dtype=np.intp)
    for i, b in enumerate(boxes):
        if len(b) == 0:
            continue
        palette[i] = rgb[b].mean(axis=0)
        labels[b] = i
    return palette, labels


def quantize_rgb(rgb, n_colors, chunk=50000):
    """Return `rgb` remapped to its <= n_colors median-cut palette (same shape).

    Each pixel is assigned to its NEAREST palette colour, NOT its median-cut box mean.
    Box membership can hand a pixel a different-hue average (e.g. a duck's belly orange
    landing in a box that also spans the red bill -> the box mean is reddish), even when
    a closer same-hue palette entry exists. Nearest-colour assignment avoids that.

    The N×K nearest-colour search is CHUNKED: at hi-fi (2048² px × 256 float64 colours)
    a single broadcast would allocate ~26 GB. Process `chunk` pixels at a time instead."""
    palette, _ = median_cut_palette(rgb, n_colors)
    if len(palette) == 0:
        return rgb
    out = np.empty_like(rgb)
    pal = palette[None, :, :]                       # (1, K, 3)
    for i in range(0, len(rgb), chunk):
        block = rgb[i:i + chunk]
        d = ((block[:, None, :] - pal) ** 2).sum(axis=2)
        out[i:i + chunk] = palette[np.argmin(d, axis=1)]
    return out.astype(rgb.dtype)


def run(image, settings):
    n = max(2, settings.palette_colors)
    w, h = image.size
    flat = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    px = flat.reshape(-1, 4)

    mask = px[:, 3] > 0.0           # only quantize opaque (baked) texels
    if not mask.any():
        mask = np.ones(len(px), dtype=bool)
    px[mask, :3] = quantize_rgb(px[mask, :3], n)

    image.pixels.foreach_set(px.ravel())
    image.update()
    print(f"lofi.pixelate: quantized to <= {n} colours")
