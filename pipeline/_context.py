"""Tiny context helpers shared by the pipeline steps.

Headless `bpy.ops` are context-sensitive: they act on the active/selected object
in the current mode and fail (or silently no-op) otherwise. Every step calls
`ensure_active(...)` before invoking an operator (see plan: review P1-7).
"""

import bpy


def ensure_object_mode(context):
    if context.mode != "OBJECT" and context.view_layer.objects.active is not None:
        bpy.ops.object.mode_set(mode="OBJECT")


def ensure_active(context, obj):
    """Make `obj` the sole selected + active object, in OBJECT mode."""
    ensure_object_mode(context)
    for o in context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
