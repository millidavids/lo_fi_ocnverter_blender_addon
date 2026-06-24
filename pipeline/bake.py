"""Selected-to-active bake: transfer the (cartoonized) source from the HIGH-poly
onto the LOW-poly's clean UV atlas.

Iteration 4: instead of a same-mesh bake (which sampled the source through the
decimated mesh's collapsed/distorted UVs and chopped it into per-island seams),
we bake hi->lo. Every low-poly texel ray-samples the true high-poly surface
through its undistorted original UVs, so the albedo transfers accurately and the
AO/cavity form-shading carries real hi-poly detail.

Material decomposition (verified on 5.1.2): the HI-poly carries the emission
SOURCE only (no target node); the LO-poly (active) carries the active Image
Texture target node only. EMIT/AO/Pointiness in selected-to-active all read the
hi-poly (selected) geometry.

Sources, all unified behind a temp Emission material on the hi-poly:
  * image  -> Image Texture sampled through a UV-Map node pinned to the OLD UV
  * vertex -> ShaderNodeVertexColor(.layer_name)
  * solid  -> a flat Emission colour
Multi-material meshes get one emitter material per hi-poly slot.
"""

import bpy

from . import prep
from ._context import ensure_object_mode

_NEUTRAL = (0.8, 0.8, 0.8, 1.0)


def _solid_color(mat):
    col = prep._material_solid_color(mat)
    return tuple(col) if col is not None else _NEUTRAL


def _build_emitter_material(name, temp, *, source_img=None, old_uv=None,
                            vertex_attr=None, solid=None):
    """A temp Emission material wired to ONE colour source (NO target node).
    Lives on the hi-poly; its emission is what the s2a bake samples."""
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
    return mat


def _build_target_material(name, target_img, temp):
    """A temp material on the LO-poly whose active node is the bake target image."""
    mat = bpy.data.materials.new(name)
    temp.materials.append(mat)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    nt.nodes.new("ShaderNodeOutputMaterial")
    tgt = nt.nodes.new("ShaderNodeTexImage")
    tgt.image = target_img
    for n in nt.nodes:
        n.select = False
    tgt.select = True
    nt.nodes.active = tgt
    return mat, tgt


def _setup_hi_emitters(hipoly, colour, old_uv, cart_params, temp):
    """Put per-slot emitter materials (cartoonized source / vertex / solid) on the
    hi-poly. Returns whether the source was cartoonized (decoupled structure)."""
    mesh = hipoly.data
    structure = False
    if colour.kind == prep.ColourSource.MATERIAL and len(hipoly.material_slots) > 0:
        slot_sources = [
            (prep.base_color_image(s.material), _solid_color(s.material))
            for s in hipoly.material_slots
        ]
        for i, (src_img, col) in enumerate(slot_sources):
            if src_img is not None and old_uv is not None:
                if cart_params is not None:
                    from . import cartoonize
                    src_img = cartoonize.cartoonize_source_copy(src_img, cart_params, temp)
                    structure = True
                mat = _build_emitter_material(f"lofi_emit_{i}", temp,
                                              source_img=src_img, old_uv=old_uv)
            else:
                mat = _build_emitter_material(f"lofi_emit_{i}", temp, solid=col)
            mesh.materials[i] = mat
    else:
        if colour.kind == prep.ColourSource.VERTEX:
            mat = _build_emitter_material("lofi_emit", temp, vertex_attr=colour.attr_name)
        else:
            mat = _build_emitter_material("lofi_emit", temp, solid=colour.color)
        mesh.materials.clear()
        mesh.materials.append(mat)
    return structure


def _select_for_bake(context, hipoly, lopoly):
    ensure_object_mode(context)
    for o in context.scene.objects:
        o.select_set(False)
    hipoly.select_set(True)
    lopoly.select_set(True)
    context.view_layer.objects.active = lopoly      # active = bake destination


def run(hipoly, lopoly, settings, context, colour, old_uv, new_uv, temp):
    """Bake the hi-poly's (cartoonized) appearance onto the lo-poly's new UV atlas.
    Returns {albedo, ao, cavity, res, structure_cartoonized}."""
    tex = max(16, settings.tex_size)
    cartoon = getattr(settings, "do_cartoonize", False)
    # Supersample the bake then detail-preservingly downscale to `tex`, bounded by
    # MAX_BAKE so a hi-fi material (tex up to 2048) doesn't blow up. At tex==MAX_BAKE the
    # supersample collapses to 1 (no DPID downscale) — fine, a 2048 texture barely needs
    # downscaling. (Was a hard min(1024, ...) that clamped hi-fi materials.)
    MAX_BAKE = 2048
    ss = max(1, int(getattr(settings, "supersample", 4))) if cartoon else 1
    ss = max(1, min(ss, MAX_BAKE // tex))
    bake_res = min(MAX_BAKE, tex * ss)

    cart_params = None
    if cartoon:
        from . import cartoonize
        cart_params = cartoonize.params_from_settings(settings)
        cart_params["source_res"] = bake_res    # hi-fi material -> sample a hi-fi source
    structure_cartoonized = _setup_hi_emitters(hipoly, colour, old_uv, cart_params, temp)

    lopoly.data.uv_layers.active = lopoly.data.uv_layers[new_uv]
    albedo = bpy.data.images.new("lofi_bake", bake_res, bake_res, alpha=True)
    tgt_mat, tgt_node = _build_target_material("lofi_target", albedo, temp)
    lopoly.data.materials.clear()
    lopoly.data.materials.append(tgt_mat)
    for p in lopoly.data.polygons:
        p.material_index = 0

    from ..utils import bake_device
    device = bake_device.setup_cycles(context.scene, use_gpu=settings.use_gpu)
    bk = context.scene.render.bake
    bk.use_selected_to_active = True
    bk.cage_extrusion = getattr(settings, "cage_extrusion", 0.05)
    bk.margin_type = "EXTEND"
    bk.margin = max(8, bake_res // 8)
    bk.use_clear = True

    _select_for_bake(context, hipoly, lopoly)
    print(f"lofi.bake: hi->lo EMIT {bake_res}px device={device} source={colour.kind} "
          f"(hi {len(hipoly.data.polygons)} tris -> lo {len(lopoly.data.polygons)})")
    context.scene.cycles.samples = 1
    bpy.ops.object.bake(type="EMIT")
    _fill_black_holes(albedo, bake_res, iters=max(64, bake_res // 4))

    if not cartoon:
        # Off / legacy: optional AO multiply + black-floor lift, no aux maps.
        if getattr(settings, "bake_shading", False):
            ao = bpy.data.images.new("lofi_ao", bake_res, bake_res, alpha=False)
            temp.images.append(ao)
            tgt_node.image = ao
            context.scene.cycles.samples = 16
            bpy.ops.object.bake(type="AO")
            _multiply_ao_into(albedo, ao, bake_res, getattr(settings, "shading_strength", 0.9))
        floor = getattr(settings, "black_floor", 0.0)
        if floor > 0.0:
            _lift_blacks(albedo, bake_res, floor)
        return {"albedo": albedo, "ao": None, "cavity": None, "res": bake_res,
                "structure_cartoonized": False}

    # Cartoonize path (iter-6): de-light happens PRE-bake in the coherent source space
    # (cartoonize.cartoonize_source_copy), so no aux AO/cavity bake is needed here.
    return {"albedo": albedo, "ao": None, "cavity": None, "res": bake_res,
            "structure_cartoonized": structure_cartoonized}


# --------------------------------------------------------------------------- #
# numpy helpers (no Blender objects beyond the image)
# --------------------------------------------------------------------------- #
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


def _multiply_ao_into(img, ao_img, tex, strength):
    import numpy as np

    col = np.empty(tex * tex * 4, dtype=np.float32)
    img.pixels.foreach_get(col)
    col = col.reshape(-1, 4)
    aob = np.empty(tex * tex * 4, dtype=np.float32)
    ao_img.pixels.foreach_get(aob)
    ao = aob.reshape(-1, 4)[:, 0]
    col[:, :3] *= (1.0 - strength * (1.0 - ao))[:, None]
    img.pixels.foreach_set(col.ravel())
    img.update()


def _lift_blacks(img, tex, floor):
    import numpy as np

    a = np.empty(tex * tex * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    a = a.reshape(-1, 4)
    a[:, :3] = floor + a[:, :3] * (1.0 - floor)
    img.pixels.foreach_set(a.ravel())
    img.update()
