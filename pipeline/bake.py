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
    # The baked image is part of the delivered clone (its final material samples
    # it, and it's embedded on export) — NOT a temp datablock, so don't track it.
    img = bpy.data.images.new("lofi_bake", tex, tex, alpha=True)

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
    bake_settings.margin = max(8, tex // 8)
    bake_settings.use_clear = True
    bake_settings.use_selected_to_active = False

    print(f"lofi.bake: EMIT {tex}px on device {device}, source={colour.kind}")
    context.scene.cycles.samples = 1
    bpy.ops.object.bake(type="EMIT")

    # Heal near-black holes: regions the photogrammetry never saw (e.g. a cut
    # end, the underside) have NO source texture and bake to flat black. Inpaint
    # them with surrounding colour so they don't read as blank-spot errors.
    _fill_black_holes(img, tex)

    if getattr(settings, "bake_shading", False):
        _bake_ao_into(context, obj, img, tex, getattr(settings, "shading_strength", 0.9))
    return img


def _fill_black_holes(img, tex, thresh=0.06, iters=64):
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


def _bake_ao_into(context, obj, img, tex, strength):
    """Bake ambient occlusion and multiply it into `img`, so the unlit asset
    still reads as 3D form (an authentic PS1 'baked lighting' move)."""
    import numpy as np

    ao_img = bpy.data.images.new("lofi_ao", tex, tex, alpha=False)
    # Re-point every material's active (bake-target) node at the AO image.
    for slot in obj.material_slots:
        nt = slot.material.node_tree
        if nt and nt.nodes.active and nt.nodes.active.type == "TEX_IMAGE":
            nt.nodes.active.image = ao_img

    context.scene.cycles.samples = 16          # AO needs a few samples to smooth
    bpy.ops.object.bake(type="AO")

    col = np.empty(tex * tex * 4, dtype=np.float32)
    img.pixels.foreach_get(col)
    col = col.reshape(-1, 4)
    aob = np.empty(tex * tex * 4, dtype=np.float32)
    ao_img.pixels.foreach_get(aob)
    ao = aob.reshape(-1, 4)[:, 0]                 # AO is greyscale
    ao = 1.0 - strength * (1.0 - ao)              # dial strength 0..1
    col[:, :3] *= ao[:, None]
    img.pixels.foreach_set(col.ravel())
    img.update()
    bpy.data.images.remove(ao_img)
    print(f"lofi.bake: multiplied baked AO (strength {strength}) into albedo")
