"""Unit tests for the resolution slider->budget math (no Blender).

    /Applications/Blender.app/Contents/Resources/5.1/python/bin/python3.13 tests/test_resolution.py
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "pipeline", "resolution.py")
_spec = importlib.util.spec_from_file_location("lofi_resolution", _SRC)
rz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rz)


def test_geo_endpoints_and_monotonic():
    N = 200000
    lo, hi = rz.resolve_geo(N, 0.0), rz.resolve_geo(N, 1.0)
    assert lo == rz.GEO_MIN, lo
    assert abs(hi - int(N * rz.GEO_MAX_FRAC)) <= 2, hi          # ~= slightly less than original
    seq = [rz.resolve_geo(N, r / 20) for r in range(21)]
    assert all(b >= a for a, b in zip(seq, seq[1:])), seq        # monotonic non-decreasing


def test_geo_lowpoly_source_no_inversion():
    # a source at/below the floor must NOT invert (the bug the clamp fixes)
    seq = [rz.resolve_geo(8, r / 10) for r in range(11)]
    assert all(b >= a for a, b in zip(seq, seq[1:])), seq
    assert min(seq) == rz.GEO_MIN and max(seq) == rz.GEO_MIN, seq


def test_mat_endpoints_pow2_monotonic():
    T = 2048
    tex0, pal0 = rz.resolve_mat(T, 0.0)
    tex1, pal1 = rz.resolve_mat(T, 1.0)
    assert tex0 == rz.MAT_MIN and pal0 == rz.PAL_MIN, (tex0, pal0)
    assert tex1 == rz.MAT_MAX and pal1 == rz.PAL_MAX, (tex1, pal1)
    texes = [rz.resolve_mat(T, r / 20)[0] for r in range(21)]
    assert all(b >= a for a, b in zip(texes, texes[1:])), texes
    for t in texes:
        assert t & (t - 1) == 0, t                               # power of two


def test_mat_small_source_clamps():
    tex, pal = rz.resolve_mat(8, 0.5)                            # source below MAT_MIN
    assert tex == rz.MAT_MIN, tex
    assert rz.PAL_MIN <= pal <= rz.PAL_MAX


def test_effective_settings_overlay_and_nonmutating():
    class Base:
        def __init__(self):
            self.tri_budget = 1500
            self.tex_size = 128
            self.supersample = 4
    base = Base()
    eff = rz.EffectiveSettings(base, tri_budget=40, tex_size=512)
    assert eff.tri_budget == 40 and eff.tex_size == 512          # overridden
    assert eff.supersample == 4                                  # delegated
    assert base.tri_budget == 1500 and base.tex_size == 128      # base untouched


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print("ALL_RESOLUTION_TESTS_PASS")
