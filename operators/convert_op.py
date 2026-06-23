"""LOFI_OT_convert — run the lo-fi conversion on the active mesh.

Thin wrapper: it validates context and delegates to `pipeline.convert.convert`,
which owns the duplicate / scene-state save-restore / failure-cleanup contract.

bl_options is {'REGISTER'} only — deliberately NOT 'UNDO'. The pipeline does its
own clone + scene-state restore and flips the render engine to Cycles for the
bake; letting Blender's operator-redo machinery re-run that would double-create
the clone or operate on a deleted one (see plan: review P1-6).
"""

import bpy

from ..pipeline import convert as convert_mod


class LOFI_OT_convert(bpy.types.Operator):
    bl_idname = "lofi.convert"
    bl_label = "Convert to Lo-Fi"
    bl_description = "Duplicate the active mesh and convert the copy into a lo-fi .glb"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def execute(self, context):
        settings = context.scene.lofi_settings
        try:
            result = convert_mod.convert(context, context.active_object, settings)
        except convert_mod.ConvertError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:  # noqa: BLE001 — surface anything, never leave a half state
            self.report({"ERROR"}, f"Lo-Fi convert failed: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, result.summary())
        return {"FINISHED"}
