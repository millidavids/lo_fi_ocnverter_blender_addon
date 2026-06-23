"""Re-UV: add a fresh UV map and Smart-UV-Project the (now low-poly) mesh.

Done AFTER decimate — unwrapping 1.1M faces is needlessly slow; at ~1500 tris
it's instant. Keeps the original UV map (returned) so `bake.py` can still sample
the source texture through it via the UV-Map-node-pin trick.
"""

import bpy

from ._context import ensure_active

NEW_UV_NAME = "lofi_baked"


def run(obj, settings, context):
    ensure_active(context, obj)
    mesh = obj.data

    old_uv = None
    if mesh.uv_layers.active is not None:
        old_uv = mesh.uv_layers.active.name
    elif len(mesh.uv_layers) > 0:
        old_uv = mesh.uv_layers[0].name

    new = mesh.uv_layers.get(NEW_UV_NAME) or mesh.uv_layers.new(name=NEW_UV_NAME)
    # Capture the name NOW: the edit-mode operator below invalidates this RNA
    # reference (it would silently re-point at a different layer afterwards).
    new_uv = new.name
    mesh.uv_layers.active = new

    margin = getattr(settings, "uv_pack_margin", 0.02)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(island_margin=margin)
    # Tighten packing (rotate islands, even spacing) so the small texture isn't
    # wasted on loose islands -> more texels per island.
    try:
        bpy.ops.uv.pack_islands(rotate=True, margin=margin)
    except (RuntimeError, TypeError) as exc:        # signature drift / no UVs
        print(f"lofi.uv: pack_islands skipped ({exc})")
    bpy.ops.object.mode_set(mode="OBJECT")

    # Re-assert the active layer by name (the reference is stale post-operator).
    mesh.uv_layers.active = mesh.uv_layers[new_uv]
    print(f"lofi.uv: old='{old_uv}' new='{new_uv}'")
    return old_uv, new_uv
