"""Resolve the two user-facing resolution sliders into concrete budgets.

`geo_resolution` and `mat_resolution` are 0..1 and scaled RELATIVE to the source's
own resolution: 0 = a few triangles / a tiny texture, 1 ~= slightly-less-than the
original. The mapping is log-interpolated so the slider feels even across orders of
magnitude, and the high endpoint is clamped >= the low one so an already-low-poly /
small-texture source can't invert (a naive lerp(ln(MIN), ln(source)) is non-monotonic
when source <= MIN).

The resolve_*/lerp math is pure (only `math`) so it's unit-tested without Blender;
`source_tris`/`source_tex_res` read a bpy object (lazy `prep` import).
"""

import math

GEO_MIN = 12          # "a few triangles"
GEO_MAX_FRAC = 0.9    # 100% slider ~= slightly less than the original
MAT_MIN = 16          # matches bake.py's max(16, tex_size) floor
MAT_MAX = 2048        # hi-fi ceiling (also the tex_size property max)
PAL_MIN = 4
PAL_MAX = 256


def _lerp(a, b, t):
    return a + (b - a) * min(1.0, max(0.0, t))


def _pow2(x):
    return int(2 ** round(math.log2(max(1.0, x))))


def resolve_geo(source_tris, r):
    """Triangle budget for geometry slider r in [0,1], relative to `source_tris`."""
    lo = GEO_MIN
    hi = max(lo, int(source_tris * GEO_MAX_FRAC))
    if hi <= lo:
        return lo
    return int(round(math.exp(_lerp(math.log(lo), math.log(hi), r))))


def resolve_mat(source_tex, r):
    """(texture_size, palette_colors) for material slider r in [0,1]. Texture is a
    power of two relative to the source texture; palette is a fixed 4..256 range
    (more colours at higher fidelity)."""
    lo = MAT_MIN
    hi = max(lo, min(int(source_tex), MAT_MAX))
    tex = lo if hi <= lo else _pow2(math.exp(_lerp(math.log(lo), math.log(hi), r)))
    tex = max(MAT_MIN, min(MAT_MAX, tex))
    pal = int(round(math.exp(_lerp(math.log(PAL_MIN), math.log(PAL_MAX), r))))
    pal = max(PAL_MIN, min(PAL_MAX, pal))
    return tex, pal


def source_tris(obj):
    """Triangulated face count of the source mesh (n-gons counted as n-2 tris)."""
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def source_tex_res(obj, default=MAT_MAX):
    """Largest base-colour image dimension across the object's materials, or
    `default` when there's no source texture (vertex-colour / solid sources)."""
    from . import prep
    best = 0
    for slot in obj.material_slots:
        if slot.material is None:
            continue
        img = prep.base_color_image(slot.material)
        if img is not None and img.size[0] and img.size[1]:
            best = max(best, int(img.size[0]), int(img.size[1]))
    return best or default


class EffectiveSettings:
    """Read-only overlay over the live `LoFiSettings` PropertyGroup: returns the
    overridden keys (the slider-resolved budgets), else delegates to the base. Never
    mutates the scene settings (so the UI sliders aren't clobbered)."""

    def __init__(self, base, **overrides):
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_ov", overrides)

    def __getattr__(self, name):
        ov = object.__getattribute__(self, "_ov")
        if name in ov:
            return ov[name]
        return getattr(object.__getattribute__(self, "_base"), name)
