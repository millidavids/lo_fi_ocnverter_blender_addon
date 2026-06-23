"""Decimate to a triangle budget.

Triangulate FIRST, then compute ratio against the post-triangulate tri count
(not the pre-triangulate quad count — a reviewer-flagged bug in the sibling).
Decimate (Collapse) `ratio` only approximates a tri target, so we evaluate the
modifier (without applying) and nudge the ratio toward the budget within a ±15%
band, capped at 3 iterations (collapse compounds error and can oscillate), then
apply once.
"""

import bpy

from ._context import ensure_active

_BAND = 0.15
_MAX_ITERS = 3


def run(obj, settings, context):
    ensure_active(context, obj)

    # Triangulate first.
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.quads_convert_to_tris(quad_method="BEAUTY", ngon_method="BEAUTY")
    bpy.ops.object.mode_set(mode="OBJECT")

    current = len(obj.data.polygons)
    target = max(4, settings.tri_budget)
    if current <= target:
        print(f"lofi.decimate: {current} tris already <= budget {target}; skipping")
        return {"tris": current, "decimated": False}

    mod = obj.modifiers.new("LoFiDecimate", type="DECIMATE")
    mod.decimate_type = "COLLAPSE"
    ratio = target / current
    mod.ratio = ratio

    n = current
    for _ in range(_MAX_ITERS):
        depsgraph = context.evaluated_depsgraph_get()
        n = len(obj.evaluated_get(depsgraph).data.polygons)
        if n == 0 or abs(n - target) <= _BAND * target:
            break
        ratio = min(1.0, max(1e-5, ratio * target / max(1, n)))
        mod.ratio = ratio

    bpy.ops.object.modifier_apply(modifier=mod.name)
    final = len(obj.data.polygons)
    if final == 0:
        raise ValueError("decimate produced an empty mesh (budget too low?)")
    print(f"lofi.decimate: {current} -> {final} tris (target {target})")
    return {"tris": final, "decimated": True}
