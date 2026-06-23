"""Prep: apply transforms, clean degenerate geometry, detect the colour source.

Photogrammetry/scan meshes arrive with un-applied transforms, doubled verts,
loose geometry and zero-area faces — all of which break Decimate / UV / bake.
We also classify where colour comes from so `bake.py` knows what to sample.
"""

import bpy

from ._context import ensure_active


class ColourSource:
    """How the mesh carries colour. `bake.py` consumes this."""

    MATERIAL = "material"   # one or more materials with an Image Texture
    VERTEX = "vertex"       # a Color Attribute (vertex colours)
    SOLID = "solid"         # no usable colour — bake a flat fill

    def __init__(self, kind, attr_name=None, color=(0.8, 0.8, 0.8, 1.0)):
        self.kind = kind
        self.attr_name = attr_name      # for VERTEX
        self.color = color              # for SOLID

    def __repr__(self):
        return f"ColourSource({self.kind}, attr={self.attr_name})"


def base_color_image(mat):
    """Return the Image datablock that drives a material's colour, or None.

    Prefers an Image Texture wired (directly or via a UV/Mapping chain) to the
    Principled Base Color / Emission; falls back to any image node with an image.
    """
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    nodes = mat.node_tree.nodes

    def _trace(socket):
        if not socket.is_linked:
            return None
        node = socket.links[0].from_node
        if node.type == "TEX_IMAGE":
            return node.image
        # walk back through a single upstream colour input (Mapping, Mix, etc.)
        for inp in node.inputs:
            if inp.type in {"RGBA", "VALUE"} and inp.is_linked:
                img = _trace(inp)
                if img is not None:
                    return img
        return None

    for n in nodes:
        if n.type in {"BSDF_PRINCIPLED", "EMISSION"}:
            key = "Base Color" if n.type == "BSDF_PRINCIPLED" else "Color"
            if key in n.inputs:
                img = _trace(n.inputs[key])
                if img is not None:
                    return img
    # fallback: first image node that actually has an image
    for n in nodes:
        if n.type == "TEX_IMAGE" and n.image is not None:
            return n.image
    return None


def _material_solid_color(mat):
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    for n in mat.node_tree.nodes:
        if n.type == "BSDF_PRINCIPLED":
            return tuple(n.inputs["Base Color"].default_value)
        if n.type == "EMISSION":
            return tuple(n.inputs["Color"].default_value)
    return None


def detect_colour_source(obj):
    """Classify the mesh's colour source (priority: image > vertex > solid)."""
    mesh = obj.data

    # 1) any material with an image texture?
    for slot in obj.material_slots:
        if base_color_image(slot.material) is not None:
            return ColourSource(ColourSource.MATERIAL)

    # 2) a Color Attribute (vertex colours)?
    color_attrs = getattr(mesh, "color_attributes", None)
    if color_attrs and len(color_attrs) > 0:
        active = color_attrs.active_color
        name = active.name if active is not None else color_attrs[0].name
        return ColourSource(ColourSource.VERTEX, attr_name=name)

    # 3) a material with a flat colour but no image?
    for slot in obj.material_slots:
        col = _material_solid_color(slot.material)
        if col is not None:
            return ColourSource(ColourSource.SOLID, color=col)

    # 4) nothing — neutral fill, don't crash
    return ColourSource(ColourSource.SOLID)


def run(obj, settings, context):
    if obj.type != "MESH":
        raise ValueError("Lo-Fi convert needs a MESH object")

    ensure_active(context, obj)

    # Apply object transforms so geometry ops work in a sane space.
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # Clean degenerate geometry. Merge-by-distance MUST precede fill_holes
    # (watertight) or duplicate boundary verts hide the boundary loops.
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=1e-4)
    bpy.ops.mesh.delete_loose(use_verts=True, use_edges=True, use_faces=False)
    bpy.ops.mesh.dissolve_degenerate()         # removes zero-area faces / zero-len edges
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.object.mode_set(mode="OBJECT")

    colour = detect_colour_source(obj)
    print(f"lofi.prep: faces={len(obj.data.polygons)} colour={colour}")
    return colour
