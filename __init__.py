"""Lo-Fi Converter — Blender add-on.

Converts a reconstructed/scan mesh into a lo-fi, low-poly, pixelated PS1-era
game asset exported as glTF .glb. It does NOT do reconstruction — it consumes a
mesh (Apple Object Capture output, the sibling ../3d_model_generator pipeline's
.glb, or any mesh) and applies the lo-fi transform.

This module is intentionally thin: bl_info + register/unregister only. All real
work lives in `pipeline/` as pure functions callable both from the operator and
headless via `scripts/run_headless.py`.
"""

bl_info = {
    "name": "Lo-Fi Converter",
    "author": "Blackhearth Games",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar (N) > Lo-Fi",
    "description": "Convert a scan/reconstructed mesh into a lo-fi, low-poly, "
                   "pixelated unlit .glb game asset.",
    "category": "Object",
}

import bpy

from . import properties
from .operators import convert_op
from .ui import panel

_CLASSES = (
    properties.LoFiSettings,
    convert_op.LOFI_OT_convert,
    panel.LOFI_PT_panel,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.lofi_settings = bpy.props.PointerProperty(type=properties.LoFiSettings)


def unregister():
    if hasattr(bpy.types.Scene, "lofi_settings"):
        del bpy.types.Scene.lofi_settings
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
