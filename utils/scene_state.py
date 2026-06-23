"""Save and restore the bits of scene state the pipeline disturbs.

The convert pipeline forces OBJECT mode, flips the render engine to Cycles for
the bake, and changes the active object / selection / active UV map. None of
that should outlive the run — capture before, restore in a `finally` (see plan
§4 step 0). The user's .blend must look untouched afterwards.
"""

import bpy


class SceneState:
    """Snapshot of mutable scene context, restorable via :meth:`restore`."""

    def __init__(self):
        self.active = None
        self.selected = []
        self.mode = "OBJECT"
        self.engine = "BLENDER_EEVEE_NEXT"
        self.active_uv = None  # (object_name, uv_layer_name)

    @classmethod
    def capture(cls, context):
        self = cls()
        view_layer = context.view_layer
        self.active = view_layer.objects.active
        self.selected = [o for o in context.scene.objects if o.select_get()]
        self.engine = context.scene.render.engine
        if self.active is not None:
            self.mode = self.active.mode
            if self.active.type == "MESH" and self.active.data.uv_layers.active:
                self.active_uv = (self.active.name, self.active.data.uv_layers.active.name)
        return self

    def restore_engine_and_mode(self, context):
        """The must-reset bits: render engine and OBJECT mode. Always safe to call."""
        try:
            context.scene.render.engine = self.engine
        except Exception:  # noqa: BLE001
            pass
        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:  # noqa: BLE001
                pass

    def restore_selection(self, context):
        """Reselect what was selected, restore active object + its active UV map."""
        try:
            for o in context.scene.objects:
                o.select_set(False)
            for o in self.selected:
                if o and o.name in context.scene.objects:
                    o.select_set(True)
        except Exception:  # noqa: BLE001
            pass
        if self.active is not None and self.active.name in context.scene.objects:
            context.view_layer.objects.active = self.active
        if self.active_uv is not None:
            obj_name, uv_name = self.active_uv
            obj = context.scene.objects.get(obj_name)
            if obj and obj.type == "MESH" and uv_name in obj.data.uv_layers:
                obj.data.uv_layers.active = obj.data.uv_layers[uv_name]

    def restore(self, context):
        """Full restore (used on failure): engine + OBJECT mode + selection."""
        self.restore_engine_and_mode(context)
        self.restore_selection(context)
