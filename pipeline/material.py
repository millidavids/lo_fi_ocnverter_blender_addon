"""Final material: unlit + nearest-filtered.

CRITICAL (verified on 5.1.2): wire the Image Texture's **Color** output DIRECTLY
into Material Output's **Surface** — no Emission node, no Principled BSDF. The
glTF exporter's `detect_shadeless_material()` only treats a material as unlit
(`KHR_materials_unlit`) when a colour/RGBA socket feeds Surface; an Emission
*shader* into Surface exports as a standard PBR material with an emissive
texture. Interpolation='Closest' => NEAREST sampler (magFilter 9728).
"""

import bpy

MAT_NAME = "lofi_material"


def run(obj, settings, context, image, temp):
    mat = bpy.data.materials.new(MAT_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.interpolation = "Closest"          # -> NEAREST sampler (9728)
    tex.location = (-300, 0)

    # Color (RGBA) straight into Surface -> exporter flags KHR_materials_unlit.
    nt.links.new(tex.outputs["Color"], out.inputs["Surface"])

    obj.data.materials.clear()
    obj.data.materials.append(mat)
    # Collapsing multiple slots to one: reset stale per-face material indices.
    for p in obj.data.polygons:
        p.material_index = 0
    print("lofi.material: unlit Image(Closest) -> Surface")
    return mat
