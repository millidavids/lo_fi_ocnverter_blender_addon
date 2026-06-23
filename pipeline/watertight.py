"""Watertight: cap open boundaries before decimating.

A reconstruction is open wherever no photo saw the surface (e.g. the underside
of an object that sat on a table). `fill_holes(sides=0)` caps every boundary
loop regardless of size (the default 30 only closes tiny ones, leaving a big
base opening). A closed mesh also decimates far more predictably.

The caps are invented geometry no photo saw — their inherited (old) UVs are
garbage and would bake to a chaotic smear of whatever texels they happen to hit.
So we collapse each cap face's source-UVs onto one representative texel, baking
the cap to a single FLAT colour (the plan's intent for a hidden base/back).
"""

import bpy

from ._context import ensure_active


def _representative_uv(mesh, n_real):
    """Mean UV of the real (pre-cap) faces — a texel likely on actual content."""
    uvdata = mesh.uv_layers.active.data
    su = sv = 0.0
    count = 0
    step = max(1, n_real // 2000)
    for pi in range(0, n_real, step):
        for li in mesh.polygons[pi].loop_indices:
            uv = uvdata[li].uv
            su += uv[0]
            sv += uv[1]
            count += 1
    if count == 0:
        return (0.5, 0.5)
    return (min(1.0, max(0.0, su / count)), min(1.0, max(0.0, sv / count)))


def _flatten_cap_uvs(mesh, n_real):
    """Set every cap face's source-UV loops to one texel -> flat baked colour."""
    if mesh.uv_layers.active is None:
        return
    target = _representative_uv(mesh, n_real)
    uvdata = mesh.uv_layers.active.data
    for pi in range(n_real, len(mesh.polygons)):
        for li in mesh.polygons[pi].loop_indices:
            uvdata[li].uv = target


def run(obj, settings, context):
    ensure_active(context, obj)
    mesh = obj.data
    n_real = len(mesh.polygons)        # fill_holes only APPENDS faces

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.fill_holes(sides=0)        # 0 = all holes, any size
    bpy.ops.object.mode_set(mode="OBJECT")

    n_caps = len(mesh.polygons) - n_real
    if n_caps > 0:
        _flatten_cap_uvs(mesh, n_real)
    print(f"lofi.watertight: filled {n_caps} cap faces, faces now {len(mesh.polygons)}")


def voxel_remesh(obj, settings, context, voxel_size=0.02):
    """Opt-in fallback for badly-broken meshes: makes the surface watertight but
    discards the original topology + UVs (re-UV + re-bake regenerate those)."""
    ensure_active(context, obj)
    mod = obj.modifiers.new(name="LoFiRemesh", type="REMESH")
    mod.mode = "VOXEL"
    mod.voxel_size = voxel_size
    bpy.ops.object.modifier_apply(modifier=mod.name)
