"""Normalize: centre on the world origin and scale to a target size.

Origin -> geometry bounds-centre, object -> world origin, then scale so the
longest bounding-box edge equals `target_size`. Orientation is left as-is
(gauge-free — we can't know which way is "up" for an arbitrary scan).
"""

import bpy

from ._context import ensure_active


def run(obj, settings, context):
    ensure_active(context, obj)

    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
    obj.location = (0.0, 0.0, 0.0)

    dims = obj.dimensions
    longest = max(dims.x, dims.y, dims.z)
    if longest > 0.0:
        factor = settings.target_size / longest
        obj.scale = (factor, factor, factor)

    bpy.ops.object.transform_apply(location=True, rotation=False, scale=True)
    print(f"lofi.normalize: dims now {tuple(round(d, 4) for d in obj.dimensions)}")
