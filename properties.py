"""LoFiSettings — the add-on's user-facing settings, stored on the Scene.

Pure data: budgets, per-step toggles, and the output path. The pipeline reads
these (see `pipeline.convert`); the UI panel (see `ui.panel`) draws them.
"""

import bpy

# preset -> (geo_resolution, mat_resolution) fidelity levels, relative to the source.
# The two sliders are the source of truth; presets just quick-set them.
_PRESETS = {
    "PS1": (0.18, 0.18),
    "LOFI": (0.50, 0.50),
    "N64": (0.68, 0.62),
    "HIFI": (0.85, 0.85),
}

_applying_preset = False    # re-entrancy guard (set-slider must not reset the preset)


def _apply_preset(self, context):
    if self.preset == "CUSTOM":
        return
    vals = _PRESETS.get(self.preset)
    if vals is not None:
        global _applying_preset
        _applying_preset = True
        self.geo_resolution, self.mat_resolution = vals
        _applying_preset = False


def _slider_update(self, context):
    # Moving a slider by hand drops the preset to Custom — but NOT while _apply_preset
    # is itself writing the sliders (the EnumProperty update fires on every assignment,
    # which would otherwise immediately clobber a just-picked preset).
    if not _applying_preset and self.preset != "CUSTOM":
        self.preset = "CUSTOM"


# Cartoon-look presets: key -> {prop: value}. OFF disables stylization.
# iter-6: presets drive de-light + flat-region controls (no baked shadow/ink).
# Saturation is kept modest: a big multiplicative boost over-saturates already-vivid,
# warm subjects (e.g. a rubber duck), pushing tones into false reds that the palette then
# snaps into blotches. Near-monochrome subjects desaturate via the mono path regardless.
_CARTOON_PRESETS = {
    "OFF": dict(do_cartoonize=False),
    "SUBTLE": dict(do_cartoonize=True, smooth_iters=1, posterize_levels=0, l0_strength=0.25,
                   saturation=1.08, contrast=1.1, delight_strength=0.6, region_flatten=0.3),
    "CEL": dict(do_cartoonize=True, smooth_iters=2, posterize_levels=8, l0_strength=0.5,
                saturation=1.15, contrast=1.2, delight_strength=0.8, region_flatten=0.5),
    "HEAVY": dict(do_cartoonize=True, smooth_iters=2, posterize_levels=6, l0_strength=0.7,
                  saturation=1.25, contrast=1.2, delight_strength=0.9, region_flatten=0.7),
}


def _apply_cartoon_preset(self, context):
    vals = _CARTOON_PRESETS.get(self.cartoon_preset)
    if vals is not None:
        for k, v in vals.items():
            setattr(self, k, v)


class LoFiSettings(bpy.types.PropertyGroup):
    preset: bpy.props.EnumProperty(
        name="Preset",
        description="Quick fidelity presets; they set the Geometry + Material sliders. "
                    "Pick Custom (or move a slider) to set them by hand",
        items=[
            ("PS1", "PS1 (tiny)", "Very low geometry + material"),
            ("LOFI", "Lo-Fi (default)", "Low geometry + material"),
            ("N64", "N64", "Medium geometry + material"),
            ("HIFI", "Hi-Fi lo-fi", "High — near the original"),
            ("CUSTOM", "Custom", "Set the sliders (or manual budgets) by hand"),
        ],
        default="LOFI",
        update=_apply_preset,
    )

    # --- resolution sliders (the primary control) -------------------------
    # 0 = a few triangles / a tiny texture; 1 ~= slightly less than the original.
    # Scaled relative to the source's own resolution (resolved in pipeline.resolution).
    geo_resolution: bpy.props.FloatProperty(
        name="Geometry",
        description="Mesh detail, relative to the source: 0 = a few triangles, "
                    "1 = slightly less than the original",
        default=0.50, min=0.0, max=1.0, subtype="FACTOR", update=_slider_update,
    )
    mat_resolution: bpy.props.FloatProperty(
        name="Material",
        description="Texture/colour detail, relative to the source: 0 = tiny + few "
                    "colours, 1 = near the original. Independent of Geometry",
        default=0.50, min=0.0, max=1.0, subtype="FACTOR", update=_slider_update,
    )
    manual_budgets: bpy.props.BoolProperty(
        name="Manual Budgets",
        description="Ignore the sliders and use the exact triangle/texture/palette "
                    "values below",
        default=False,
    )

    # --- budgets (manual-override values; otherwise derived from the sliders) ---
    tri_budget: bpy.props.IntProperty(
        name="Triangle Budget",
        description="Target triangle count after decimation (approximate; a "
                    "tolerance band is applied)",
        default=1500, min=4, soft_min=300, soft_max=5000, max=50000,
    )
    tex_size: bpy.props.IntProperty(
        name="Texture Size",
        description="Baked texture resolution in pixels (square)",
        default=128, min=16, soft_min=64, soft_max=256, max=2048,
    )
    palette_colors: bpy.props.IntProperty(
        name="Palette Colors",
        description="Number of colours to quantize the texture down to (Auto palette only; "
                    "fixed palettes use their own colour count)",
        default=64, min=2, soft_min=16, soft_max=256, max=256,
    )
    palette_mode: bpy.props.EnumProperty(
        name="Palette",
        description="Where the colour palette comes from. Auto generates one FROM the image "
                    "(median-cut in perceptual OKLab, honouring the colour count); the "
                    "others SNAP every pixel to a fixed curated/custom palette. All modes "
                    "require Pixelate / Palette = ON",
        items=[
            ("AUTO", "Auto (from image)", "Median-cut a palette out of the texture (OKLab)"),
            ("PICO8", "PICO-8 (16)", "Snap to the PICO-8 16-colour palette"),
            ("DB16", "DawnBringer 16", "Snap to the DawnBringer 16 palette"),
            ("DB32", "DawnBringer 32", "Snap to the DawnBringer 32 palette"),
            ("CUSTOM", "Custom file…", "Snap to a .hex / .pal / .gpl palette file"),
        ],
        default="AUTO",
    )
    custom_palette_path: bpy.props.StringProperty(
        name="Palette File",
        description="A .hex (Lospec), .pal (JASC) or .gpl (GIMP) palette file to snap to "
                    "when Palette = Custom",
        subtype="FILE_PATH", default="",
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
        description="Centre on the world origin and scale to the Target Size. The hi->lo "
                    "bake is calibrated for this ~unit size — leave ON and use 'Keep "
                    "Original Size' if you want the result at the source's scale",
        default=True,
    )
    keep_original_size: bpy.props.BoolProperty(
        name="Keep Original Size",
        description="Output the lo-fi at the SOURCE's world size + position (a drop-in "
                    "replacement) instead of the normalized Target Size. The bake still "
                    "runs on a unit object internally, then the result is scaled back",
        default=False,
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
        default=0.6, min=0.0, max=1.0,
    )

    # --- cartoonize / stylize (defaults below == the HEAVY preset) --------
    cartoon_preset: bpy.props.EnumProperty(
        name="Stylize",
        description="Cartoonization preset: de-light + abstract into flat colour regions "
                    "for a LIT game asset. Off = plain bake/downscale (v1 behaviour)",
        items=[
            ("OFF", "Off", "Plain bake + downscale (v1)"),
            ("SUBTLE", "Subtle", "Light de-light + flatten + saturation"),
            ("CEL", "Cel", "De-light + flatten regions + posterize"),
            ("HEAVY", "Heavy", "Strong de-light + flatten + posterize + high saturation"),
        ],
        default="HEAVY",
        update=_apply_cartoon_preset,
    )
    do_cartoonize: bpy.props.BoolProperty(
        name="Cartoonize",
        description="Abstract surfaces into flat cartoon regions, amplify edges/"
                    "colour, and detail-preservingly downscale (avoids washout)",
        default=True,
    )
    supersample: bpy.props.IntProperty(
        name="Supersample",
        description="Bake at tex_size × this, then detail-preservingly downscale. "
                    "Higher = more detail preserved (and slower bakes)",
        default=4, min=1, soft_max=4, max=8,
    )
    smooth_iters: bpy.props.IntProperty(
        name="Flatten Iterations",
        description="Edge-preserving smoothing passes (abstraction strength)",
        default=2, min=0, max=5,
    )
    smooth_sigma: bpy.props.FloatProperty(name="Flatten Radius", default=2.0, min=0.5, max=8.0)
    smooth_eps: bpy.props.FloatProperty(name="Flatten Threshold", default=0.02, min=0.001, max=0.2)
    saturation: bpy.props.FloatProperty(
        name="Saturation", description="Colour punch for colourful subjects (1 = unchanged). "
                    "Kept modest — a large boost over-saturates already-vivid subjects into "
                    "false reds",
        default=1.2, min=0.0, soft_max=2.5, max=4.0,
    )
    contrast: bpy.props.FloatProperty(
        name="Contrast", default=1.2, min=0.5, soft_max=2.0, max=3.0,
    )
    posterize_levels: bpy.props.IntProperty(
        name="Posterize Levels",
        description="Tone steps per channel (0 = off) on the DE-LIT albedo, for flat "
                    "cartoon regions. Kept modest so the colour palette does the final reduction",
        default=8, min=0, max=32,
    )
    delight_strength: bpy.props.FloatProperty(
        name="De-light",
        description="Strip baked shading from the albedo (AO-divide + low-frequency "
                    "flatten) so the LIT material relights cleanly in-engine. 0 = keep "
                    "the baked shading",
        default=0.8, min=0.0, max=1.0,
    )
    retinex_sigma: bpy.props.FloatProperty(
        name="De-light Radius",
        description="Radius of the low-frequency shading estimate removed during de-light "
                    "(larger = flattens broader directional shadows)",
        default=12.0, min=2.0, max=40.0,
    )
    region_flatten: bpy.props.FloatProperty(
        name="Flatten Regions",
        description="Merge shaded-vs-lit variation of one surface into a single flat "
                    "colour (skin -> ~one colour) before posterizing",
        default=0.5, min=0.0, max=1.0,
    )
    l0_strength: bpy.props.FloatProperty(
        name="Flatten (L0)",
        description="Collapse the surface into genuinely FLAT cartoon cells with crisp "
                    "edges (L0 gradient minimization, run in coherent source space). "
                    "0 = off (just edge-preserving smoothing); higher = fewer, flatter "
                    "regions. The biggest lever on the 'cartoon' read",
        default=0.5, min=0.0, max=1.0,
    )
    dpid_lambda: bpy.props.FloatProperty(
        name="Detail Preservation",
        description="DPID downscale strength: higher keeps more detail (less averaging)",
        default=1.0, min=0.0, max=2.0,
    )

    # --- hi->lo bake (iteration 4) ----------------------------------------
    cage_extrusion: bpy.props.FloatProperty(
        name="Bake Cage",
        description="Ray extrusion for the hi->lo selected-to-active bake, relative "
                    "to the normalized unit size",
        default=0.05, min=0.0, soft_max=0.2, max=1.0,
    )
    uv_pack_margin: bpy.props.FloatProperty(
        name="UV Margin",
        description="Smart-UV island margin + pack spacing (smaller = tighter packing, "
                    "more texels per island, but tighter seams)",
        default=0.02, min=0.0, max=0.2,
    )
    bake_source_cap: bpy.props.IntProperty(
        name="Bake Source Cap",
        description="Optional perf valve: cap the hi-poly bake source to this many "
                    "triangles (0 = off / full detail). High values only; capping "
                    "reduces AO/cavity form detail",
        default=0, min=0, max=2000000,
    )

    # --- device -----------------------------------------------------------
    use_gpu: bpy.props.BoolProperty(
        name="Use GPU (Metal)",
        description="Try the Metal GPU for the Cycles bake, falling back to CPU. "
                    "At these sizes CPU is already fast",
        default=True,
    )

    # --- output -----------------------------------------------------------
    export_to_file: bpy.props.BoolProperty(
        name="Export to File",
        description="Write the result to a .glb file. Turn OFF to only build the lo-fi "
                    "object in the scene (iterate on the sliders + regenerate without "
                    "creating a pile of files)",
        default=True,
    )
    output_path: bpy.props.StringProperty(
        name="Output .glb",
        description="Where to write the exported lo-fi .glb. Leave blank to auto-derive "
                    "it from the source object (its asset folder / the .blend / your home, "
                    "named <object>_lofi.glb)",
        subtype="FILE_PATH",
        default="",
    )
