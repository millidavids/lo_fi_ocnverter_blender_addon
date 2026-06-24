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

import numpy as np
import bpy

from ._context import ensure_active


def _representative_uv(mesh, n_real):
    """A texel representative of the DOMINANT surface (the body), for the cap colour.

    NOT the mean UV: averaging coordinates across disjoint UV islands lands on a
    meaningless point — often a minor feature (e.g. a duck's red bill) or empty UV
    space — which then bakes the cap that colour and bleeds it onto nearby real
    geometry. Instead take the centre of the densest UV histogram bin: the largest
    island, i.e. the body, whose colour is the safe choice for invented geometry."""
    uvdata = mesh.uv_layers.active.data
    bins = 64
    hist = np.zeros((bins, bins), dtype=np.int64)
    step = max(1, n_real // 4000)
    for pi in range(0, n_real, step):
        for li in mesh.polygons[pi].loop_indices:
            uv = uvdata[li].uv
            iu = min(bins - 1, max(0, int(uv[0] * bins)))
            iv = min(bins - 1, max(0, int(uv[1] * bins)))
            hist[iu, iv] += 1
    if hist.sum() == 0:
        return (0.5, 0.5)
    iu, iv = np.unravel_index(int(np.argmax(hist)), hist.shape)
    return ((iu + 0.5) / bins, (iv + 0.5) / bins)


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
