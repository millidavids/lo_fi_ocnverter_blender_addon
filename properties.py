"""LoFiSettings — the add-on's user-facing settings, stored on the Scene.

Pure data: budgets, per-step toggles, and the output path. The pipeline reads
these (see `pipeline.convert`); the UI panel (see `ui.panel`) draws them.
"""

import bpy

# (tri_budget, tex_size, palette_colors) per preset.
_PRESETS = {
    "PS1": (1000, 64, 16),
    "LOFI": (1500, 128, 32),
    "N64": (2500, 128, 64),
    "HIFI": (5000, 256, 128),
}


def _apply_preset(self, context):
    vals = _PRESETS.get(self.preset)
    if vals is not None:
        self.tri_budget, self.tex_size, self.palette_colors = vals


class LoFiSettings(bpy.types.PropertyGroup):
    preset: bpy.props.EnumProperty(
        name="Preset",
        description="Quick budget presets; pick Custom to set values by hand",
        items=[
            ("PS1", "PS1 (tiny)", "1000 tris, 64px, 16 colours"),
            ("LOFI", "Lo-Fi (default)", "1500 tris, 128px, 32 colours"),
            ("N64", "N64", "2500 tris, 128px, 64 colours"),
            ("HIFI", "Hi-Fi lo-fi", "5000 tris, 256px, 128 colours"),
            ("CUSTOM", "Custom", "Set budgets manually"),
        ],
        default="LOFI",
        update=_apply_preset,
    )

    # --- budgets ----------------------------------------------------------
    tri_budget: bpy.props.IntProperty(
        name="Triangle Budget",
        description="Target triangle count after decimation (approximate; a "
                    "tolerance band is applied)",
        default=1500, min=50, soft_min=300, soft_max=5000, max=50000,
    )
    tex_size: bpy.props.IntProperty(
        name="Texture Size",
        description="Baked texture resolution in pixels (square)",
        default=128, min=16, soft_min=64, soft_max=256, max=2048,
    )
    palette_colors: bpy.props.IntProperty(
        name="Palette Colors",
        description="Number of colours to quantize the texture down to",
        default=64, min=2, soft_min=16, soft_max=256, max=256,
    )
    target_size: bpy.props.FloatProperty(
        name="Target Size",
        description="Longest bounding-box edge after normalization (Blender units)",
        default=1.0, min=0.001, soft_max=10.0,
    )

    # --- per-step toggles (defaults on) -----------------------------------
    do_heal: bpy.props.BoolProperty(
        name="Largest Component",
        description="Keep only the largest connected component (drops floating "
                    "scraps). Turn OFF if your object is multiple disconnected parts",
        default=True,
    )
    do_watertight: bpy.props.BoolProperty(
        name="Fill Holes",
        description="Cap open boundaries (e.g. the unseen underside) before decimating",
        default=True,
    )
    do_decimate: bpy.props.BoolProperty(
        name="Decimate",
        description="Collapse geometry down to the triangle budget",
        default=True,
    )
    do_normalize: bpy.props.BoolProperty(
        name="Normalize",
        description="Centre on the world origin and scale to the target size",
        default=True,
    )
    do_pixelate: bpy.props.BoolProperty(
        name="Pixelate / Palette",
        description="Quantize the baked texture to a small colour palette",
        default=True,
    )
    bake_shading: bpy.props.BoolProperty(
        name="Bake Shading (AO)",
        description="Bake ambient occlusion into the texture so the unlit asset "
                    "still reads as 3D form (authentic PS1 'baked lighting'). Turn "
                    "OFF for a pure albedo that relies entirely on engine lighting",
        default=True,
    )
    shading_strength: bpy.props.FloatProperty(
        name="Shading Strength",
        description="How strongly baked AO darkens crevices (0 = none, 1 = full)",
        default=0.9, min=0.0, max=1.0,
    )

    # --- device -----------------------------------------------------------
    use_gpu: bpy.props.BoolProperty(
        name="Use GPU (Metal)",
        description="Try the Metal GPU for the Cycles bake, falling back to CPU. "
                    "At these sizes CPU is already fast",
        default=True,
    )

    # --- output -----------------------------------------------------------
    output_path: bpy.props.StringProperty(
        name="Output .glb",
        description="Where to write the exported lo-fi .glb",
        subtype="FILE_PATH",
        default="//lofi_export.glb",
    )
