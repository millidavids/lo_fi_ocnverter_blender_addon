"""Standalone unit tests for the cartoonize numpy math (no Blender).

Loads pipeline/cartoonize.py by file path so importing it doesn't pull in the
bpy-dependent package. Run:
    /Applications/Blender.app/Contents/Resources/5.1/python/bin/python3.13 tests/test_cartoonize.py
"""

import importlib.util
import os

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "pipeline", "cartoonize.py")
_spec = importlib.util.spec_from_file_location("lofi_cartoonize", _SRC)
ct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ct)


def test_dpid_preserves_high_contrast_feature():
    # 4x4 block: mostly 0.2, one strongly-deviating pixel at 0.9
    blk = np.full((4, 4, 3), 0.2, dtype=np.float32)
    blk[0, 0, :] = 0.9
    box_mean = blk.mean()                       # ~0.244 -- what naive avg gives
    out = ct.dpid_downscale(blk, factor=4, lam=1.0)
    assert out.shape == (1, 1, 3)
    # DPID weights the deviating pixel up, so the result sits well above the mean
    assert out[0, 0, 0] > box_mean + 0.2, (out[0, 0, 0], box_mean)


def test_xdog_marks_a_step_edge():
    luma = np.full((16, 16), 0.2, dtype=np.float32)
    luma[:, 8:] = 0.8                            # vertical step edge
    ink = ct.xdog_edges(luma, sigma=1.0, eps=0.0, phi=15.0)
    assert ink.shape == luma.shape
    assert ink.min() < 0.95                      # an ink line appears at the edge
    assert ink[:, 0].mean() > 0.99               # flat far region stays un-inked


def test_posterize_bounds_levels():
    rng = np.random.RandomState(0)
    rgb = rng.rand(20, 20, 3).astype(np.float32)
    out = ct.posterize(rgb, 4)
    for c in range(3):
        assert len(np.unique(np.round(out[:, :, c], 5))) <= 4


def test_saturation_increases():
    rng = np.random.RandomState(1)
    rgb = (0.4 + 0.2 * rng.rand(16, 16, 3)).astype(np.float32)   # muted colours
    before = (rgb.max(2) - rgb.min(2)).mean()
    out = ct.boost_saturation_contrast(rgb, sat=1.8, contrast=1.0)
    after = (out.max(2) - out.min(2)).mean()
    assert after > before, (before, after)


def test_guided_smooth_flattens_noise_keeps_shape():
    rng = np.random.RandomState(2)
    rgb = np.clip(0.5 + 0.1 * rng.randn(32, 32, 3), 0, 1).astype(np.float32)
    out = ct.guided_smooth(rgb, sigma=2.0, eps=0.02, iters=2)
    assert out.shape == rgb.shape
    assert out.std() < rgb.std()                 # noise reduced


def test_posterize_value_preserves_grey():
    grey = np.full((8, 8, 3), 0.43, dtype=np.float32)
    out = ct.posterize_value(grey, 6)
    # stays neutral (no per-channel grey->colour banding)
    assert np.allclose(out[:, :, 0], out[:, :, 1])
    assert np.allclose(out[:, :, 1], out[:, :, 2])


def test_colourfulness_low_for_grey_high_for_colour():
    grey = np.full((8, 8, 3), 0.5, dtype=np.float32)
    assert ct.colourfulness(grey) < 0.02
    col = np.zeros((8, 8, 3), dtype=np.float32)
    col[:, :, 0] = 0.9
    col[:, :, 2] = 0.1
    assert ct.colourfulness(col) > 0.5


def test_stylize_does_not_manufacture_colour_on_monochrome():
    rng = np.random.RandomState(7)
    base = 0.5 + 0.05 * rng.randn(64, 64, 1)                  # near-grey marble-ish
    rgb = np.clip(np.repeat(base, 3, axis=2) + 0.02 * rng.randn(64, 64, 3),
                  0, 1).astype(np.float32)
    params = ct.params_from_settings(object())
    params["supersample"] = 4
    out = ct.stylize(rgb, params)
    # chroma-adaptive: a monochrome input must NOT become a colourful blotch
    assert ct.colourfulness(out) < 0.12, ct.colourfulness(out)


def test_stylize_outputs_target_size():
    rng = np.random.RandomState(3)
    rgb = rng.rand(64, 64, 3).astype(np.float32)
    params = ct.params_from_settings(object())   # all defaults via getattr
    params["supersample"] = 4
    out = ct.stylize(rgb, params)
    assert out.shape == (16, 16, 3)
    assert out.dtype == np.float32
    assert out.min() >= 0.0 and out.max() <= 1.0


def test_cartoonize_structure_no_colour_manufacture():
    rng = np.random.RandomState(5)
    base = 0.5 + 0.05 * rng.randn(48, 48, 1)
    rgb = np.clip(np.repeat(base, 3, axis=2) + 0.02 * rng.randn(48, 48, 3),
                  0, 1).astype(np.float32)
    out = ct.cartoonize_structure(rgb, ct.params_from_settings(object()))
    assert out.shape == rgb.shape
    assert out.min() >= 0.0 and out.max() <= 1.0
    # STRUCTURE has no saturation step, so a near-grey source stays near-grey
    assert ct.colourfulness(out) < 0.15, ct.colourfulness(out)


def test_grade_finish_chroma_adaptive():
    params = ct.params_from_settings(object())
    params["supersample"] = 1                      # no downscale; same size out
    mono = np.full((16, 16, 3), 0.5, dtype=np.float32)
    out_m = ct.grade_finish(mono.copy(), params)
    assert ct.colourfulness(out_m) < 0.1           # monochrome stays monochrome
    col = np.zeros((16, 16, 3), dtype=np.float32)
    col[:, :, 0] = 0.8
    col[:, :, 1] = 0.2
    out_c = ct.grade_finish(col.copy(), params)
    assert ct.colourfulness(out_c) > 0.2           # colourful keeps/gets colour


def test_cf_routing_depends_on_hole_fill():
    # A sparse atlas (mostly black background + a small colour island) reads as
    # cf~0 -> mono routing, which would WRONGLY desaturate a colourful object.
    # This documents WHY _fill_black_holes must flood object colour across the
    # background BEFORE grade_finish measures cf.
    sparse = np.zeros((32, 32, 3), dtype=np.float32)
    sparse[14:18, 14:18, 0] = 0.9                  # tiny red island, rest black
    assert ct.colourfulness(sparse) < 0.04         # would mis-route to mono
    filled = np.zeros((32, 32, 3), dtype=np.float32)
    filled[:, :, 0] = 0.9                          # object colour flooded everywhere
    assert ct.colourfulness(filled) > 0.15         # correct colourful routing


def test_delight_removes_low_freq_shading():
    # A single flat colour under a broad low-frequency shading ramp -> the ramp is
    # captured by the Retinex low-pass and divided out, leaving ~uniform luminance.
    # Inputs are sRGB-encoded (what the bake reads back).
    p = ct.params_from_settings(object())
    p["delight_strength"] = 1.0
    p["retinex_sigma"] = 10.0
    H = W = 128
    ramp = np.repeat(np.linspace(0.15, 1.0, W, dtype=np.float32)[None, :], H, axis=0)
    lin = np.full((H, W, 3), (0.5, 0.2, 0.2), np.float32) * ramp[:, :, None]
    ao = ct._linear_to_srgb(np.full((H, W), 0.8, np.float32))    # flat AO -> mean-1 no-op
    out = ct._srgb_to_linear(ct.delight(ct._linear_to_srgb(lin), ao, p))
    # measure the interior (the low-pass shading estimate is border-biased, negligible
    # on a real atlas but ~half a small tile)
    sl = slice(24, H - 24)
    luma_in = (lin[:, sl] * ct._LUMA).sum(2).mean(0)
    luma_out = (out[:, sl] * ct._LUMA).sum(2).mean(0)
    assert luma_out.std() < 0.3 * luma_in.std(), (luma_out.std(), luma_in.std())


def test_delight_preserves_albedo_edge():
    # De-light divides by a per-pixel scalar (shading), so a hard red|blue albedo edge
    # keeps its hue: left stays red-dominant, right stays blue-dominant.
    p = ct.params_from_settings(object())
    p["delight_strength"] = 1.0
    H = W = 64
    ramp = np.repeat(np.linspace(0.45, 1.0, W, dtype=np.float32)[None, :], H, axis=0)
    base = np.zeros((H, W, 3), np.float32)
    base[:, :W // 2] = (0.55, 0.12, 0.12)
    base[:, W // 2:] = (0.12, 0.12, 0.55)
    lin = base * ramp[:, :, None]
    ao = ct._linear_to_srgb(np.full((H, W), 0.8, np.float32))
    out = ct._srgb_to_linear(ct.delight(ct._linear_to_srgb(lin), ao, p))
    left = out[:, 6:W // 2 - 6].mean((0, 1))
    right = out[:, W // 2 + 6:-6].mean((0, 1))
    assert left[0] > left[2] and right[2] > right[0], (left, right)


def test_delight_ao_divide_lifts_occlusion():
    # A flat albedo darkened by an AO patch comes back ~uniform (occlusion divided out).
    p = ct.params_from_settings(object())
    p["delight_strength"] = 1.0
    p["retinex_sigma"] = 40.0
    H = W = 48
    ao_lin = np.full((H, W), 0.9, np.float32)
    ao_lin[18:30, 18:30] = 0.3                       # an occluded patch
    lin = np.full((H, W, 3), 0.5, np.float32) * ao_lin[:, :, None]
    out = ct._srgb_to_linear(ct.delight(ct._linear_to_srgb(lin), ct._linear_to_srgb(ao_lin), p))
    patch_in = lin[20:28, 20:28].mean() / lin[2:10, 2:10].mean()
    patch_out = out[20:28, 20:28].mean() / out[2:10, 2:10].mean()
    assert patch_out > 1.8 * patch_in, (patch_out, patch_in)   # occlusion lifted toward 1


def test_delight_retinex_without_ao():
    # De-light runs in source space with NO AO (Retinex luma flatten only) -> it still
    # removes a low-frequency shading ramp on a single-colour patch.
    p = ct.params_from_settings(object())
    p["delight_strength"] = 1.0
    p["retinex_sigma"] = 10.0
    H = W = 128
    ramp = np.repeat(np.linspace(0.15, 1.0, W, dtype=np.float32)[None, :], H, axis=0)
    lin = np.full((H, W, 3), (0.5, 0.2, 0.2), np.float32) * ramp[:, :, None]
    out = ct._srgb_to_linear(ct.delight(ct._linear_to_srgb(lin), None, p))
    sl = slice(24, H - 24)
    luma_in = (lin[:, sl] * ct._LUMA).sum(2).mean(0)
    luma_out = (out[:, sl] * ct._LUMA).sum(2).mean(0)
    assert luma_out.std() < 0.3 * luma_in.std(), (luma_out.std(), luma_in.std())


def test_delight_strength_zero_is_noop():
    p = ct.params_from_settings(object())
    p["delight_strength"] = 0.0
    rgb = np.clip(0.5 + 0.1 * np.random.RandomState(7).randn(16, 16, 3), 0, 1).astype(np.float32)
    assert np.array_equal(ct.delight(rgb, None, p), rgb)


# --- L0 flatten (iter-7) --------------------------------------------------- #
def test_l0_preserves_step_and_kills_noise():
    rng = np.random.RandomState(3)
    img = np.zeros((64, 64, 3), np.float32)
    img[:, 32:, :] = 1.0
    noisy = np.clip(img + rng.normal(0, 0.05, img.shape), 0, 1).astype(np.float32)
    out = ct.l0_smooth(noisy, lam=0.02)
    assert out[:, :30].mean() < 0.1 and out[:, 34:].mean() > 0.9      # step contrast kept
    assert out[:, :30].var() < 0.2 * noisy[:, :30].var()             # flat side flattened


def test_l0_reduces_gradient_transitions():
    rng = np.random.RandomState(4)
    img = np.zeros((48, 48, 3), np.float32)
    img[:, 24:, :] = 0.8
    noisy = np.clip(img + rng.normal(0, 0.06, img.shape), 0, 1).astype(np.float32)

    def trans(a):
        return int((np.abs(np.diff(a[:, :, 0], axis=1)) > 0.02).sum())
    assert trans(ct.l0_smooth(noisy, lam=0.02)) < 0.25 * trans(noisy)


def test_l0_is_not_posterize_on_ramp():
    # L0 penalizes the COUNT of gradients, not their magnitude: a clean ramp keeps many
    # levels (unlike posterize). Guards against mis-asserting "few unique levels".
    ramp = np.repeat(np.linspace(0, 1, 64, dtype=np.float32)[None, :], 64, axis=0)
    out = ct.l0_smooth(np.repeat(ramp[:, :, None], 3, axis=2), lam=0.02)
    assert len(np.unique(np.round(out[..., 0], 3))) > 12


def test_l0_lambda_zero_skips():
    assert ct._l0_lambda(0.0) is None
    assert ct._l0_lambda(0.5) is not None and ct._l0_lambda(0.5) > 0


# --- OKLCh grading (iter-7) ------------------------------------------------ #
def _oklch(rgb):
    return ct.colour.srgb_to_oklch(rgb)


def test_oklch_grade_boosts_chroma_preserves_hue():
    rng = np.random.RandomState(8)
    rgb = np.clip(0.3 + 0.4 * rng.rand(32, 32, 3), 0, 1).astype(np.float32)
    out = ct.boost_saturation_contrast(rgb, sat=1.4, contrast=1.0)
    ci, co = _oklch(rgb), _oklch(out)
    assert co[..., 1].mean() > ci[..., 1].mean()                     # chroma up
    mask = ci[..., 1] > 0.05
    dh = np.abs(np.arctan2(np.sin(co[..., 2] - ci[..., 2]),
                           np.cos(co[..., 2] - ci[..., 2])))
    assert float(dh[mask].mean()) < 0.05                             # hue preserved


def test_oklch_grade_monochrome_stays_grey():
    grey = np.full((16, 16, 3), 0.5, np.float32)
    out = ct.boost_saturation_contrast(grey, sat=2.0, contrast=1.0)
    assert np.abs(out[..., 0] - out[..., 1]).max() < 1e-2            # no false colour
    assert np.abs(out[..., 1] - out[..., 2]).max() < 1e-2


def test_oklch_contrast_does_not_darken_midgrey():
    # Pivoting contrast on mean-L (mid-grey sits at L=0.598, NOT 0.5) must leave a uniform
    # mid-grey field unchanged -- pivoting on 0.5 would darken it.
    grey = np.full((8, 8, 3), 0.5, np.float32)
    out = ct.boost_saturation_contrast(grey, sat=1.0, contrast=1.5)
    assert np.allclose(out, 0.5, atol=2e-2)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print("ALL_CARTOONIZE_TESTS_PASS")
