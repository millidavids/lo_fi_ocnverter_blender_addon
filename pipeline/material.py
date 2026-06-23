"""Final material: LIT PBR + nearest-filtered (iteration 6).

The asset is now game-ready and de-lit: the albedo carries NO baked shading, and
the material RESPONDS to lighting so the engine draws the shadows on the geometry
at runtime (à la Abiotic Factor). So we wire the base-colour Image Texture into a
Principled BSDF (Base Color), with a matte roughness and zero metallic.

Verified on 5.1.2: feeding the BSDF (not a raw RGBA socket) into Surface makes the
glTF exporter emit a standard PBR material (NO `KHR_materials_unlit`); roughness/
metallic export as set; and `interpolation='Closest'` keeps the base-colour sampler
at NEAREST (magFilter 9728) for the pixel look.
"""

import bpy

MAT_NAME = "lofi_material"


def run(obj, settings, context, image, temp):
    mat = bpy.data.materials.new(MAT_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (-300, 0)
    bsdf.inputs["Roughness"].default_value = 0.9      # matte; no specular hotspots
    bsdf.inputs["Metallic"].default_value = 0.0

    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.interpolation = "Closest"                     # -> NEAREST sampler (9728)
    tex.location = (-700, 0)

    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    obj.data.materials.clear()
    obj.data.materials.append(mat)
    # Collapsing multiple slots to one: reset stale per-face material indices.
    for p in obj.data.polygons:
        p.material_index = 0
    print("lofi.material: lit PBR Principled(Base Color=Image[Closest], rough 0.9)")
    return mat
