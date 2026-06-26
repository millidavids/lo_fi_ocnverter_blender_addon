"""Standalone unit tests for the median-cut quantizer (numpy only, no Blender).

Run with any Python that has numpy:
    python tests/test_quantize.py
or under Blender's bundled Python / pytest. The pixelate module is loaded
directly by file path so importing it doesn't pull in the bpy-dependent package.
"""

import importlib.util
import os
import tempfile

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


def test_chunked_matches_oneshot_on_large():
    # Larger than the default chunk -> exercises the chunked nearest-colour path.
    # The median-cut palette is deterministic, so chunk size must not change the result
    # (this locks in the hi-fi OOM fix: chunked == one-shot).
    rgb = np.random.RandomState(11).rand(160000, 3).astype(np.float32)
    one_shot = pixelate.quantize_rgb(rgb, 16, chunk=10_000_000)
    chunked = pixelate.quantize_rgb(rgb, 16, chunk=5000)
    assert chunked.shape == rgb.shape
    assert np.array_equal(chunked, one_shot)
    assert len(_unique_rows(chunked)) <= 16


def test_palette_count_bound():
    rgb = np.random.RandomState(1).rand(500, 3)
    palette, labels = pixelate.median_cut_palette(rgb, 16)
    assert len(palette) <= 16
    assert labels.max() < len(palette)
    assert labels.min() >= 0


# --- iter-7: OKLab default + legacy RGB path ------------------------------- #
def test_rgb_space_path_still_deterministic():
    # Legacy RGB-space quantization remains available and chunk-invariant.
    rgb = np.random.RandomState(5).rand(20000, 3).astype(np.float32)
    a = pixelate.quantize_rgb(rgb, 16, chunk=10_000_000, space="rgb")
    b = pixelate.quantize_rgb(rgb, 16, chunk=4000, space="rgb")
    assert np.array_equal(a, b)
    assert len(_unique_rows(a)) <= 16


def test_oklab_distinct_colours_separable():
    colors = np.array([[0.9, 0.1, 0.1], [0.1, 0.7, 0.2],
                       [0.15, 0.2, 0.85], [0.95, 0.95, 0.2]], np.float32)
    rgb = np.repeat(colors, 20, axis=0)
    out = pixelate.quantize_rgb(rgb, 4, space="oklab")
    assert len(_unique_rows(out)) == 4


def test_single_colour_oklab_no_div0():
    flat = np.full((200, 3), 0.3, np.float32)
    out = pixelate.quantize_rgb(flat, 8, space="oklab")
    assert out.shape == (200, 3) and np.isfinite(out).all()


# --- iter-7: fixed palettes ------------------------------------------------ #
def test_builtin_palette_sizes():
    assert pixelate._BUILTIN_PALETTES["PICO8"].shape == (16, 3)
    assert pixelate._BUILTIN_PALETTES["DB16"].shape == (16, 3)
    assert pixelate._BUILTIN_PALETTES["DB32"].shape == (32, 3)


def test_snap_uses_only_palette_members():
    pal = pixelate._BUILTIN_PALETTES["DB16"]
    rgb = np.random.RandomState(2).rand(400, 3).astype(np.float32)
    out = pixelate.snap_to_palette(rgb, pal)
    members = set(map(tuple, np.round(pal, 5)))
    assert all(tuple(np.round(o, 5)) in members for o in out)


def test_snap_handles_empty_input():
    pal = pixelate._BUILTIN_PALETTES["PICO8"]
    assert pixelate.snap_to_palette(np.zeros((0, 3), np.float32), pal).shape == (0, 3)


# --- iter-7: palette file parsing + errors --------------------------------- #
def _write(content, suffix):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.write(fd, content.encode())
    os.close(fd)
    return path


def test_load_palette_formats():
    hexf = _write("1D2B53\n#FF004D\nFFA300\n", ".hex")
    jasc = _write("JASC-PAL\n0100\n2\n29 43 83\n255 0 77\n", ".pal")
    gpl = _write("GIMP Palette\nName: t\nColumns: 4\n#\n29 43 83 a\n255 0 77 b\n", ".gpl")
    try:
        assert pixelate.load_palette_file(hexf).shape == (3, 3)
        assert pixelate.load_palette_file(jasc).shape == (2, 3)
        assert pixelate.load_palette_file(gpl).shape == (2, 3)
        assert np.allclose(pixelate.load_palette_file(jasc)[0],
                           [29 / 255, 43 / 255, 83 / 255], atol=1e-3)
    finally:
        for f in (hexf, jasc, gpl):
            os.remove(f)


def test_palette_file_errors():
    empty = _write("\n   \n", ".hex")
    try:
        raised = False
        try:
            pixelate.load_palette_file(empty)
        except ValueError:
            raised = True
        assert raised, "expected ValueError on empty palette"
    finally:
        os.remove(empty)
    raised = False
    try:
        pixelate.load_palette_file("/no/such/palette.hex")
    except ValueError:
        raised = True
    assert raised, "expected ValueError on missing palette"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print("ALL_QUANTIZE_TESTS_PASS")
