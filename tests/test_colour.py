"""Standalone unit tests for the OKLab/OKLCh colour maths (numpy only, no Blender).

Loaded by file path so importing it doesn't pull in the bpy-dependent package. Run:
    /Applications/Blender.app/Contents/Resources/5.1/python/bin/python3.13 tests/test_colour.py
or any python with numpy.
"""

import importlib.util
import os

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "pipeline", "colour.py")
_spec = importlib.util.spec_from_file_location("lofi_colour", _SRC)
col = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(col)


def test_red_matches_ottosson():
    # sRGB pure red -> OKLab, Ottosson's published reference value.
    lab = col.srgb_to_oklab(np.array([[1.0, 0.0, 0.0]], dtype=np.float32))[0]
    assert np.allclose(lab, [0.6280, 0.2249, 0.1258], atol=2e-3), lab


def test_srgb_oklab_roundtrip_identity():
    rng = np.random.default_rng(0)
    s = rng.random((2000, 3)).astype(np.float32)
    back = col.oklab_to_srgb(col.srgb_to_oklab(s))
    assert np.abs(back - s).max() < 1e-4, np.abs(back - s).max()


def test_mid_grey_lightness_is_pivot():
    # The contrast pivot must be the OKLab L of mid-grey (~0.598), NOT 0.5.
    L = col.srgb_to_oklab(np.array([[0.5, 0.5, 0.5]], dtype=np.float32))[0, 0]
    assert abs(float(L) - col.MID_GREY_L) < 2e-3, L


def test_oklch_roundtrip():
    rng = np.random.default_rng(1)
    lab = col.srgb_to_oklab(rng.random((500, 3)).astype(np.float32))
    back = col.oklch_to_oklab(col.oklab_to_oklch(lab))
    assert np.allclose(back, lab, atol=1e-5)


def test_gamut_clip_lands_in_range_and_keeps_hue():
    # Overdrive chroma far past the sRGB gamut, then clip.
    lch = col.srgb_to_oklch(np.array([[0.1, 0.3, 0.9]], dtype=np.float32))
    over = lch.copy()
    over[..., 1] *= 4.0
    clipped = col.gamut_clip_oklch(over)
    rl = col.oklab_to_linear_srgb(col.oklch_to_oklab(clipped))
    assert rl.min() >= -1e-3 and rl.max() <= 1.0 + 1e-3, (rl.min(), rl.max())
    assert np.allclose(clipped[..., 2], over[..., 2]), "hue must be preserved"
    assert clipped[..., 1] <= over[..., 1] + 1e-6, "chroma only reduced"


def test_in_gamut_colour_is_untouched_by_clip():
    # A modest in-gamut colour should keep ~all its chroma after clipping.
    lch = col.srgb_to_oklch(np.array([[0.4, 0.4, 0.4]], dtype=np.float32))
    clipped = col.gamut_clip_oklch(lch)
    assert abs(float(clipped[0, 1]) - float(lch[0, 1])) < 1e-3


def test_cbrt_handles_out_of_gamut_without_nan():
    # Negative LMS (wide-gamut / synthetic OKLab) must not produce NaN.
    lab = np.array([[0.5, 0.4, -0.4]], dtype=np.float32)
    out = col.oklab_to_srgb(lab)
    assert np.isfinite(out).all()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
