# VENDORED FOR REFERENCE — do not run directly.
# Source: ../3d_model_generator  scripts/rebake.py  @ git rev 87459cf
# (deleted in that repo's pivot commit 312c9a2; recovered from history).
# This add-on's pipeline/bake.py + pipeline/uv.py + utils/bake_device.py adapt
# the patterns here (UV-pin trick, smart_project, Metal/CPU device setup) — but
# switch DIFFUSE->EMIT and handle multi-material / vertex-colour sources.

"""Headless Blender re-UV + texture rebake for the lo-fi pipeline.

Invoked by `modelgen --rebake` as:
    blender --background --python rebake.py -- <in.obj> <out.obj> <tex_size>

Imports a low-poly OBJ (with its original UVs + texture), Smart-UV-unwraps it
into a *new* UV layer, and bakes the original texture onto that clean layout —
fixing the uneven texel density of a decimated kept-atlas mesh. Writes the
re-UV'd mesh as <out.obj> (+ .mtl) and the baked texture as <out>.png.

Cycles, GPU (Metal) when available, else CPU. Targets Blender 4.x/5.x.
"""

import os
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
in_obj, out_obj, tex_size = argv[0], argv[1], int(argv[2])
out_tex = os.path.splitext(out_obj)[0] + ".png"

# --- import ---------------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.obj_import(filepath=in_obj)
obj = next(o for o in bpy.context.scene.objects if o.type == "MESH")
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
obj.active_material_index = 0

mat = obj.data.materials[0]
mat.use_nodes = True
nodes, links = mat.node_tree.nodes, mat.node_tree.links

# Pin the source texture to the ORIGINAL UV layer so it keeps sampling correctly
# even after we make a new UV layer active for the bake target.
src_uv = obj.data.uv_layers[0].name
src_tex = next((n for n in nodes if n.type == "TEX_IMAGE"), None)
if src_tex is not None:
    uvmap = nodes.new("ShaderNodeUVMap")
    uvmap.uv_map = src_uv
    links.new(uvmap.outputs["UV"], src_tex.inputs["Vector"])

# --- new UV layer + Smart UV unwrap (bake destination layout) -------------
new_uv = obj.data.uv_layers.new(name="baked")
obj.data.uv_layers.active = new_uv
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(island_margin=0.02)
bpy.ops.object.mode_set(mode="OBJECT")

# --- Cycles: try Metal GPU, fall back to CPU ------------------------------
scene = bpy.context.scene
scene.render.engine = "CYCLES"
try:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "METAL"
    prefs.refresh_devices()
    for d in prefs.devices:
        d.use = True
    scene.cycles.device = "GPU"
except Exception as exc:  # noqa: BLE001
    print(f"rebake: GPU unavailable ({exc}); using CPU")
    scene.cycles.device = "CPU"
scene.cycles.samples = 1
scene.render.bake.use_pass_direct = False
scene.render.bake.use_pass_indirect = False
scene.render.bake.margin = max(2, tex_size // 64)

# --- bake target node: created + made active+selected right before baking --
img = bpy.data.images.new("baked", tex_size, tex_size)
bake_node = nodes.new("ShaderNodeTexImage")
bake_node.image = img
bake_node.location = (400, 0)
for n in nodes:
    n.select = False
bake_node.select = True
nodes.active = bake_node

bpy.ops.object.bake(type="DIFFUSE")

# --- save the baked image + point base colour at it, then export ----------
img.filepath_raw = out_tex
img.file_format = "PNG"
img.save()

bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
if bsdf is not None:
    links.new(bake_node.outputs["Color"], bsdf.inputs["Base Color"])
obj.data.uv_layers.active = new_uv  # export writes the baked UVs

bpy.ops.wm.obj_export(
    filepath=out_obj,
    export_selected_objects=True,
    export_uv=True,
    export_materials=True,
    path_mode="COPY",
)
print("rebake: done")
