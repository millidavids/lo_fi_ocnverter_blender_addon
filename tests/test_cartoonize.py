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


def test_stylize_outputs_target_size():
    rng = np.random.RandomState(3)
    rgb = rng.rand(64, 64, 3).astype(np.float32)
    params = ct.params_from_settings(object())   # all defaults via getattr
    params["supersample"] = 4
    out = ct.stylize(rgb, params)
    assert out.shape == (16, 16, 3)
    assert out.dtype == np.float32
    assert out.min() >= 0.0 and out.max() <= 1.0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print("ALL_CARTOONIZE_TESTS_PASS")
