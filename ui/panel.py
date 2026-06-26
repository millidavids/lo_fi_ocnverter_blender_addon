"""LOFI_PT_panel — the "Lo-Fi" tab in the 3D viewport sidebar (N-panel)."""

import bpy

from ..pipeline import resolution


def _resolved_caption(obj, s):
    """('~N tris', 'Mpx · C colours') previews for the current sliders + active object.
    Approximate: uses len(polygons) (fast, O(1)) — decimate triangulates + runs on the
    prepped mesh, so the real count differs a little. Best-effort; never raises."""
    try:
        src_tris = max(1, len(obj.data.polygons))
        src_tex = resolution.source_tex_res(obj)
        tris = resolution.resolve_geo(src_tris, s.geo_resolution)
        tex, pal = resolution.resolve_mat(src_tex, s.mat_resolution)
        return f"~{tris} tris", f"{tex}px · {pal} colours"
    except Exception:  # noqa: BLE001
        return None, None


class LOFI_PT_panel(bpy.types.Panel):
    bl_label = "Lo-Fi Converter"
    bl_idname = "LOFI_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Lo-Fi"

    def draw(self, context):
        layout = self.layout
        s = context.scene.lofi_settings

        obj = context.active_object
        is_mesh = obj is not None and obj.type == "MESH"

        layout.prop(s, "preset")

        col = layout.column(align=True)
        col.label(text="Resolution (independent)")
        cap_geo = cap_mat = None
        if is_mesh:
            cap_geo, cap_mat = _resolved_caption(obj, s)
        col.prop(s, "geo_resolution", slider=True)
        if cap_geo:
            col.label(text=cap_geo, icon="MESH_DATA")
        col.prop(s, "mat_resolution", slider=True)
        if cap_mat:
            col.label(text=cap_mat, icon="TEXTURE")

        adv = layout.column(align=True)
        adv.prop(s, "manual_budgets")
        if s.manual_budgets:
            adv.prop(s, "tri_budget")
            adv.prop(s, "tex_size")
            adv.prop(s, "palette_colors")
        adv.prop(s, "target_size")

        col = layout.column(align=True)
        col.label(text="Steps")
        col.prop(s, "do_heal")
        col.prop(s, "do_watertight")
        col.prop(s, "do_decimate")
        col.prop(s, "do_normalize")
        if s.do_normalize:
            col.prop(s, "keep_original_size")
        col.prop(s, "do_pixelate")
        if s.do_pixelate:
            col.prop(s, "palette_mode")
            if s.palette_mode == "CUSTOM":
                col.prop(s, "custom_palette_path")
            elif s.palette_mode != "AUTO":
                col.label(text="Fixed palette — colour count ignored", icon="COLOR")
        col.prop(s, "bake_shading")
        if s.bake_shading:
            col.prop(s, "shading_strength")

        box = layout.box()
        box.label(text="Stylize", icon="BRUSH_DATA")
        box.prop(s, "cartoon_preset")
        if s.do_cartoonize:
            box.prop(s, "supersample")
            box.prop(s, "delight_strength")
            box.prop(s, "region_flatten")
            box.prop(s, "l0_strength")
            box.prop(s, "saturation")
            box.prop(s, "contrast")
            box.prop(s, "posterize_levels")
            box.prop(s, "smooth_iters")

        layout.prop(s, "use_gpu")
        layout.prop(s, "export_to_file")
        if s.export_to_file:
            layout.prop(s, "output_path")
            if is_mesh and not (s.output_path or "").strip():
                try:
                    from ..pipeline.convert import default_output_path
                    layout.label(text="→ " + default_output_path(obj), icon="FILE_TICK")
                except Exception:  # noqa: BLE001
                    pass

        row = layout.row()
        row.scale_y = 1.4
        row.enabled = is_mesh
        row.operator("lofi.convert", icon="MOD_REMESH")
        if not is_mesh:
            layout.label(text="Select a mesh object", icon="INFO")
