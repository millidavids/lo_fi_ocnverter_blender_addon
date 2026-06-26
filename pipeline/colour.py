"""Colour-space maths shared by the cartoonize + pixelate stages (pure numpy, no bpy).

Why this module exists: the quantizer (`pixelate.py`) and the grader (`cartoonize.py`)
both need to leave sRGB and work in a *perceptually uniform* space. sRGB is not uniform —
equal RGB distances look unequal, so RGB median-cut produces muddy palettes and RGB-luma
saturation drifts hue (the classic blue->purple shift). OKLab (Björn Ottosson, 2020) fixes
that: its L/C/h axes are perceptually orthogonal, so we can quantise, recolour, and boost
chroma without shifting perceived hue.

This module is deliberately STATELESS and dependency-free (only numpy) so the two callers
can each `exec_module` it by file path in standalone unit tests without a package context
(see the import-fallback in cartoonize.py / pixelate.py). Keep it that way — no bpy, no
import-time caches.

Matrices are Ottosson's published constants; verified here numerically (sRGB red ->
oklab (0.628, 0.225, 0.126); round-trip error ~1e-6; mid-grey L = 0.598).
"""

import numpy as np

# linear sRGB <-> LMS (M1) and cube-rooted LMS' <-> Lab (M2), plus inverses.
_M1 = np.array([
    [0.4122214708, 0.5363325363, 0.0514459929],
    [0.2119034982, 0.6806995451, 0.1073969566],
    [0.0883024619, 0.2817188376, 0.6299787005]], dtype=np.float64)
_M2 = np.array([
    [0.2104542553,  0.7936177850, -0.0040720468],
    [1.9779984951, -2.4285922050,  0.4505937099],
    [0.0259040371,  0.7827717662, -0.8086757660]], dtype=np.float64)
_M2_INV = np.array([
    [1.0,  0.3963377774,  0.2158037573],
    [1.0, -0.1055613458, -0.0638541728],
    [1.0, -0.0894841775, -1.2914855480]], dtype=np.float64)
_M1_INV = np.array([
    [ 4.0767416621, -3.3077115913,  0.2309699292],
    [-1.2684380046,  2.6097574011, -0.3413193965],
    [-0.0041960863, -0.7034186147,  1.7076147010]], dtype=np.float64)

# OKLab lightness of sRGB mid-grey (0.5). Used as the default contrast pivot so a
# contrast boost about "mid" doesn't darken the image (pivoting on 0.5 would, because
# 0.5-grey sits at L=0.598, not 0.5).
MID_GREY_L = 0.5982


def srgb_to_linear(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92,
                    1.055 * (c ** (1.0 / 2.4)) - 0.055).astype(np.float32)


# --------------------------------------------------------------------------- #
# OKLab (Cartesian)
# --------------------------------------------------------------------------- #
def linear_srgb_to_oklab(rgb_lin):
    """(...,3) LINEAR sRGB -> OKLab. Uses np.cbrt (NOT **(1/3)) so out-of-gamut
    negative LMS values stay real instead of becoming NaN."""
    lms = rgb_lin.astype(np.float64) @ _M1.T
    return (np.cbrt(lms) @ _M2.T).astype(np.float32)


def oklab_to_linear_srgb(lab):
    """(...,3) OKLab -> LINEAR sRGB (may be out of [0,1]; caller clips/clamps)."""
    lms_ = lab.astype(np.float64) @ _M2_INV.T
    return ((lms_ ** 3) @ _M1_INV.T).astype(np.float32)


def srgb_to_oklab(rgb):
    """(...,3) gamma sRGB in [0,1] -> OKLab."""
    return linear_srgb_to_oklab(srgb_to_linear(rgb))


def oklab_to_srgb(lab):
    """(...,3) OKLab -> gamma sRGB, clipped to [0,1]."""
    return linear_to_srgb(np.clip(oklab_to_linear_srgb(lab), 0.0, 1.0))


# --------------------------------------------------------------------------- #
# OKLCh (cylindrical OKLab) — separates lightness / chroma / hue
# --------------------------------------------------------------------------- #
def oklab_to_oklch(lab):
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    return np.stack([L, np.hypot(a, b), np.arctan2(b, a)], axis=-1).astype(np.float32)


def oklch_to_oklab(lch):
    L, C, h = lch[..., 0], lch[..., 1], lch[..., 2]
    return np.stack([L, C * np.cos(h), C * np.sin(h)], axis=-1).astype(np.float32)


def gamut_clip_oklch(lch, eps=1e-4, iters=22):
    """Pull each pixel's CHROMA down (L and hue preserved) until the colour fits inside
    sRGB. This is the hue-faithful alternative to clamping RGB channels (which distorts
    hue and crushes detail). Vectorised per-pixel bisection on a chroma scale in [0,1];
    L is clamped to [0,1] first so the grey axis (chroma 0) is always a valid floor."""
    L = np.clip(lch[..., 0], 0.0, 1.0)
    C = lch[..., 1]
    h = lch[..., 2]
    lo = np.zeros_like(C)        # chroma 0 (grey) is always in gamut
    hi = np.ones_like(C)         # full requested chroma (maybe out of gamut)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        rl = oklab_to_linear_srgb(oklch_to_oklab(np.stack([L, C * mid, h], axis=-1)))
        inside = np.all((rl >= -eps) & (rl <= 1.0 + eps), axis=-1)
        lo = np.where(inside, mid, lo)
        hi = np.where(inside, hi, mid)
    return np.stack([L, C * lo, h], axis=-1).astype(np.float32)


def srgb_to_oklch(rgb):
    return oklab_to_oklch(srgb_to_oklab(rgb))


def oklch_to_srgb(lch):
    return oklab_to_srgb(oklch_to_oklab(lch))
