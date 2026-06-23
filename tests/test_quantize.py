"""Standalone unit tests for the median-cut quantizer (numpy only, no Blender).

Run with any Python that has numpy:
    python tests/test_quantize.py
or under Blender's bundled Python / pytest. The pixelate module is loaded
directly by file path so importing it doesn't pull in the bpy-dependent package.
"""

import importlib.util
import os

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIX = os.path.join(_HERE, "..", "pipeline", "pixelate.py")
_spec = importlib.util.spec_from_file_location("lofi_pixelate", _PIX)
pixelate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pixelate)


def _unique_rows(a):
    return np.unique(np.round(a, 6), axis=0)


def test_four_distinct_colors_preserved():
    colors = np.array([
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
        [1.0, 1.0, 1.0],
    ])
    rgb = np.repeat(colors, 25, axis=0)        # 100 px, 4 clusters
    out = pixelate.quantize_rgb(rgb, 4)
    assert len(_unique_rows(out)) == 4
    # each output row should equal one of the originals (exact clusters)
    for row in _unique_rows(out):
        assert np.any(np.all(np.isclose(colors, row, atol=1e-6), axis=1))


def test_gradient_reduced_to_n():
    grad = np.linspace(0.0, 1.0, 300)
    rgb = np.stack([grad, grad, grad], axis=1)  # 300 grey levels
    for n in (2, 8, 16, 64):
        out = pixelate.quantize_rgb(rgb, n)
        assert len(_unique_rows(out)) <= n
    # quantizing to >= input count keeps it lossless-ish (<= input uniques)
    out = pixelate.quantize_rgb(rgb, 1000)
    assert len(_unique_rows(out)) <= 300


def test_shape_and_dtype_preserved():
    rgb = np.random.RandomState(0).rand(50, 3).astype(np.float32)
    out = pixelate.quantize_rgb(rgb, 8)
    assert out.shape == rgb.shape
    assert out.dtype == rgb.dtype


def test_empty_and_single():
    empty = np.zeros((0, 3))
    assert pixelate.quantize_rgb(empty, 8).shape == (0, 3)
    one = np.array([[0.3, 0.6, 0.9]])
    out = pixelate.quantize_rgb(one, 8)
    assert np.allclose(out, one)


def test_palette_count_bound():
    rgb = np.random.RandomState(1).rand(500, 3)
    palette, labels = pixelate.median_cut_palette(rgb, 16)
    assert len(palette) <= 16
    assert labels.max() < len(palette)
    assert labels.min() >= 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print("ALL_QUANTIZE_TESTS_PASS")
