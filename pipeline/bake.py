"""EMIT bake: flat, unlit colour from any source onto the new UV layout.

EMIT bakes the Emission socket with no lighting/pass-flag subtlety (the DIFFUSE
path the sibling used needs `use_pass_color=True` + direct/indirect off — EMIT
avoids that trap). It unifies all colour sources behind one temporary Emission
material per slot:

  * image  -> Image Texture sampled through a UV-Map node pinned to the OLD UV
              (rebake.py's trick: the source keeps sampling its original layout
              while the bake destination uses the NEW UV)
  * vertex -> ShaderNodeVertexColor(.layer_name)   (NOT ShaderNodeColorAttribute,
              which does not exist in 5.1.2)
  * solid  -> a flat Emission colour

Multi-material meshes get one Emission material PER slot (each pointing the SAME
target image node), so a single bake captures every face — no slot's texture is
silently dropped (don't join-to-dominant).
"""

import bpy

from . import prep
from ._context import ensure_active

_NEUTRAL = (0.8, 0.8, 0.8, 1.0)


def _solid_color(mat):
    col = prep._material_solid_color(mat)
    return tuple(col) if col is not None else _NEUTRAL


def _build_emission_material(name, target_img, temp, *, source_img=None,
                             old_uv=None, vertex_attr=None, solid=None):
    """A temp Emission material wired to one colour source, with `target_img` as
    the active (bake destination) node."""
    mat = bpy.data.materials.new(name)
    temp.materials.append(mat)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emit = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])

    if source_img is not None and old_uv is not None:
        src = nt.nodes.new("ShaderNodeTexImage")
        src.image = source_img
        uvmap = nt.nodes.new("ShaderNodeUVMap")
        uvmap.uv_map = old_uv
        nt.links.new(uvmap.outputs["UV"], src.inputs["Vector"])
        nt.links.new(src.outputs["Color"], emit.inputs["Color"])
    elif vertex_attr is not None:
        vc = nt.nodes.new("ShaderNodeVertexColor")
        vc.layer_name = vertex_attr
        nt.links.new(vc.outputs["Color"], emit.inputs["Color"])
    else:
        emit.inputs["Color"].default_value = solid if solid is not None else _NEUTRAL

    # Target image node LAST, made active+selected: this is the bake destination.
    tgt = nt.nodes.new("ShaderNodeTexImage")
    tgt.image = target_img
    for n in nt.nodes:
        n.select = False
    tgt.select = True
    nt.nodes.active = tgt
    return mat


def run(obj, settings, context, colour, old_uv, new_uv, temp):
    ensure_active(context, obj)
    mesh = obj.data
    mesh.uv_layers.active = mesh.uv_layers[new_uv]   # bake destination layout

    tex = max(16, settings.tex_size)
    # In cartoonize mode we bake at a SUPERSAMPLED resolution and let cartoonize
    # abstract + detail-preservingly downscale to tex_size; otherwise bake direct.
    cartoon = getattr(settings, "do_cartoonize", False)
    ss = max(1, int(getattr(settings, "supersample", 4))) if cartoon else 1
    bake_res = min(1024, tex * ss)
    img = bpy.data.images.new("lofi_bake", bake_res, bake_res, alpha=True)

    if colour.kind == prep.ColourSource.MATERIAL and len(obj.material_slots) > 0:
        # Capture per-slot sources BEFORE we overwrite the slots.
        slot_sources = [
            (prep.base_color_image(slot.material), _solid_color(slot.material))
            for slot in obj.material_slots
        ]
        for i, (src_img, col) in enumerate(slot_sources):
            if src_img is not None and old_uv is not None:
                mat = _build_emission_material(
                    f"lofi_emit_{i}", img, temp, source_img=src_img, old_uv=old_uv)
            else:
                mat = _build_emission_material(
                    f"lofi_emit_{i}", img, temp, solid=col)
            mesh.materials[i] = mat
    else:
        if colour.kind == prep.ColourSource.VERTEX:
            mat = _build_emission_material(
                "lofi_emit", img, temp, vertex_attr=colour.attr_name)
        else:
            mat = _build_emission_material(
                "lofi_emit", img, temp, solid=colour.color)
        mesh.materials.clear()
        mesh.materials.append(mat)

    # Cycles + device, then a flat unlit bake.
    from ..utils import bake_device
    device = bake_device.setup_cycles(context.scene, use_gpu=settings.use_gpu)
    bake_settings = context.scene.render.bake
    # EXTEND bleeds island-edge colour outward to fill the gaps between UV
    # islands, so NEAREST sampling at seams doesn't pick up the black atlas
    # background (a generous margin matters on a small, many-island atlas).
    bake_settings.margin_type = "EXTEND"
    bake_settings.margin = max(8, bake_res // 8)
    bake_settings.use_clear = True
    bake_settings.use_selected_to_active = False

    print(f"lofi.bake: EMIT {bake_res}px on device {device}, source={colour.kind}")
    context.scene.cycles.samples = 1
    bpy.ops.object.bake(type="EMIT")

    # Heal near-black holes: regions the photogrammetry never saw (e.g. a cut
    # end, the underside) have NO source texture and bake to flat black. Inpaint
    # them with surrounding colour. Iters must scale with resolution or big holes
    # stay black-cored (then DPID amplifies them into dark rings).
    _fill_black_holes(img, bake_res, iters=max(64, bake_res // 4))

    if cartoon:
        # Bake AO + cavity (Pointiness) as SEPARATE maps for cartoonize to use as
        # feature-popping shading (no multiply here). Order: AO before cavity,
        # because the cavity bake replaces the material slots.
        ao = _bake_ao_map(context, obj, bake_res, temp) if getattr(
            settings, "bake_shading", True) else None
        cavity = _bake_pointiness(context, obj, bake_res, temp) if getattr(
            settings, "cavity_strength", 0.0) > 0.0 else None
        return {"albedo": img, "ao": ao, "cavity": cavity, "res": bake_res}

    # Legacy (no cartoonize): optional AO multiplied straight into the albedo.
    if getattr(settings, "bake_shading", False):
        _bake_ao_into(context, obj, img, bake_res,
                      getattr(settings, "shading_strength", 0.9))
    floor = getattr(settings, "black_floor", 0.0)
    if floor > 0.0:
        _lift_blacks(img, bake_res, floor)
    return {"albedo": img, "ao": None, "cavity": None, "res": bake_res}


def _lift_blacks(img, res, floor):
    """Raise the black point so dark/unscanned regions read as dark-grey, not
    flat black blobs. (Cartoonize does its own lift; this is for the OFF path.)"""
    import numpy as np

    a = np.empty(res * res * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    a = a.reshape(-1, 4)
    a[:, :3] = floor + a[:, :3] * (1.0 - floor)
    img.pixels.foreach_set(a.ravel())
    img.update()


def _fill_black_holes(img, tex, thresh=0.085, iters=64):
    import numpy as np

    a = np.empty(tex * tex * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    a = a.reshape(tex, tex, 4)
    rgb = a[:, :, :3]
    hole = rgb.max(axis=2) < thresh
    n0 = int(hole.sum())
    if n0 == 0:
        return

    filled = rgb.copy()
    h = hole.copy()
    for _ in range(iters):
        if not h.any():
            break
        nb_sum = np.zeros_like(filled)
        nb_cnt = np.zeros((tex, tex), dtype=np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            valid = ~np.roll(h, (dy, dx), axis=(0, 1))
            nb_sum += np.roll(filled, (dy, dx), axis=(0, 1)) * valid[:, :, None]
            nb_cnt += valid
        fillable = h & (nb_cnt > 0)
        filled[fillable] = nb_sum[fillable] / nb_cnt[fillable][:, None]
        h[fillable] = False

    a[:, :, :3] = filled
    img.pixels.foreach_set(a.ravel())
    img.update()
    print(f"lofi.bake: inpainted {n0} near-black hole texels")


def _bake_ao_map(context, obj, res, temp):
    """Bake ambient occlusion into a NEW image (retargeting the materials' active
    bake-target node). Returns the AO image (tracked as temp)."""
    ao_img = bpy.data.images.new("lofi_ao", res, res, alpha=False)
    if temp is not None:
        temp.images.append(ao_img)
    for slot in obj.material_slots:
        nt = slot.material.node_tree
        if nt and nt.nodes.active and nt.nodes.active.type == "TEX_IMAGE":
            nt.nodes.active.image = ao_img
    context.scene.cycles.samples = 16          # AO needs a few samples to smooth
    context.scene.render.bake.margin_type = "EXTEND"
    bpy.ops.object.bake(type="AO")
    return ao_img


def _bake_pointiness(context, obj, res, temp):
    """Bake mesh cavity/curvature via Geometry > Pointiness (concave<0.5<convex).

    Pointiness clusters near 0.4, so a ColorRamp expands the useful band. A single
    geometric material on all slots suffices (pointiness is per-vertex, slot-
    independent), and material.py replaces the slots afterward anyway."""
    img = bpy.data.images.new("lofi_cavity", res, res, alpha=False)
    mat = bpy.data.materials.new("lofi_cavity_mat")
    if temp is not None:
        temp.images.append(img)
        temp.materials.append(mat)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emit = nt.nodes.new("ShaderNodeEmission")
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.30     # expand the ~0.4 cluster
    ramp.color_ramp.elements[1].position = 0.55
    nt.links.new(geo.outputs["Pointiness"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], emit.inputs["Color"])
    nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])
    tgt = nt.nodes.new("ShaderNodeTexImage")
    tgt.image = img
    for n in nt.nodes:
        n.select = False
    tgt.select = True
    nt.nodes.active = tgt

    obj.data.materials.clear()
    obj.data.materials.append(mat)
    for p in obj.data.polygons:
        p.material_index = 0
    context.scene.cycles.samples = 1
    bpy.ops.object.bake(type="EMIT")
    return img


def _bake_ao_into(context, obj, img, res, strength):
    """Legacy path: bake AO and multiply it straight into the albedo `img`."""
    import numpy as np

    ao_img = _bake_ao_map(context, obj, res, None)   # not tracked; removed below
    col = np.empty(res * res * 4, dtype=np.float32)
    img.pixels.foreach_get(col)
    col = col.reshape(-1, 4)
    aob = np.empty(res * res * 4, dtype=np.float32)
    ao_img.pixels.foreach_get(aob)
    ao = aob.reshape(-1, 4)[:, 0]
    ao = 1.0 - strength * (1.0 - ao)
    col[:, :3] *= ao[:, None]
    img.pixels.foreach_set(col.ravel())
    img.update()
    bpy.data.images.remove(ao_img)
    print(f"lofi.bake: multiplied baked AO (strength {strength}) into albedo")
