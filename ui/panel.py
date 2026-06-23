"""LOFI_PT_panel — the "Lo-Fi" tab in the 3D viewport sidebar (N-panel)."""

import bpy


class LOFI_PT_panel(bpy.types.Panel):
    bl_label = "Lo-Fi Converter"
    bl_idname = "LOFI_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Lo-Fi"

    def draw(self, context):
        layout = self.layout
        s = context.scene.lofi_settings

        layout.prop(s, "preset")

        col = layout.column(align=True)
        col.label(text="Budgets")
        col.prop(s, "tri_budget")
        col.prop(s, "tex_size")
        col.prop(s, "palette_colors")
        col.prop(s, "target_size")

        col = layout.column(align=True)
        col.label(text="Steps")
        col.prop(s, "do_heal")
        col.prop(s, "do_watertight")
        col.prop(s, "do_decimate")
        col.prop(s, "do_normalize")
        col.prop(s, "do_pixelate")
        col.prop(s, "bake_shading")
        if s.bake_shading:
            col.prop(s, "shading_strength")

        box = layout.box()
        box.label(text="Stylize", icon="BRUSH_DATA")
        box.prop(s, "cartoon_preset")
        if s.do_cartoonize:
            box.prop(s, "supersample")
            box.prop(s, "saturation")
            box.prop(s, "contrast")
            box.prop(s, "posterize_levels")
            box.prop(s, "edge_strength")
            box.prop(s, "smooth_iters")
            box.prop(s, "cavity_strength")
            box.prop(s, "black_floor")

        layout.prop(s, "use_gpu")
        layout.prop(s, "output_path")

        obj = context.active_object
        row = layout.row()
        row.scale_y = 1.4
        row.enabled = obj is not None and obj.type == "MESH"
        row.operator("lofi.convert", icon="MOD_REMESH")
        if obj is None or obj.type != "MESH":
            layout.label(text="Select a mesh object", icon="INFO")
