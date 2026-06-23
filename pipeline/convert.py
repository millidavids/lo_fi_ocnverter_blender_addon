"""Orchestrator — the one entry point the operator and headless runner both call.

Owns the NON-NEGOTIABLE safety contract (plan §4 step 0):
  * operate on a DUPLICATE of the active object, never the user's scan;
  * save scene state and restore it (engine, mode, selection);
  * on ANY failure delete the partial clone and fully restore;
  * purge temporary datablocks in `finally` even on success.
"""

import bpy

from ..utils.scene_state import SceneState
from . import bake, decimate, heal, material, normalize, pixelate, prep
from . import export as export_mod
from . import uv as uv_mod
from . import watertight
from ._context import ensure_active, ensure_object_mode


class ConvertError(Exception):
    pass


class TempData:
    """Tracks transient datablocks created during a run so they can be purged.

    NB: the baked image and the final material are NOT temp — they belong to the
    delivered clone and must persist. Only the per-slot emission bake materials
    are transient (the final material replaces them, orphaning them)."""

    def __init__(self):
        self.materials = []

    def purge(self):
        for m in self.materials:
            try:
                if m.name in bpy.data.materials:
                    bpy.data.materials.remove(m)
            except Exception:  # noqa: BLE001
                pass


class ConvertResult:
    def __init__(self, clone_name, out_path, colour, facts):
        self.clone_name = clone_name
        self.out_path = out_path
        self.colour = colour
        self.facts = facts

    def summary(self):
        return (f"Lo-Fi: '{self.clone_name}' "
                f"({self.facts.get('tris', '?')} tris, {self.colour}) -> {self.out_path}")


def _duplicate(context, obj):
    new = obj.copy()
    new.data = obj.data.copy()
    new.animation_data_clear()
    coll = getattr(context, "collection", None) or context.scene.collection
    coll.objects.link(new)
    return new


def _delete_object(obj):
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    try:
        if data and data.users == 0:
            bpy.data.meshes.remove(data)
    except Exception:  # noqa: BLE001
        pass


def _assert_nonempty(obj, step):
    if len(obj.data.polygons) == 0:
        raise ConvertError(f"{step} left the mesh empty")


def convert(context, source_obj, settings):
    state = SceneState.capture(context)
    temp = TempData()
    clone = None
    success = False
    try:
        ensure_object_mode(context)
        clone = _duplicate(context, source_obj)
        clone.name = source_obj.name + "_lofi"
        ensure_active(context, clone)

        colour = prep.run(clone, settings, context)
        _assert_nonempty(clone, "prep")

        if settings.do_heal:
            heal.run(clone, settings, context)
            _assert_nonempty(clone, "heal")
        if settings.do_watertight:
            watertight.run(clone, settings, context)
        if settings.do_decimate:
            decimate.run(clone, settings, context)
            _assert_nonempty(clone, "decimate")
        if settings.do_normalize:
            normalize.run(clone, settings, context)

        old_uv, new_uv = uv_mod.run(clone, settings, context)
        image = bake.run(clone, settings, context, colour, old_uv, new_uv, temp)
        if settings.do_pixelate:
            pixelate.run(image, settings)
        material.run(clone, settings, context, image, temp)

        out_path = bpy.path.abspath(settings.output_path)
        facts = export_mod.run(
            clone, settings, context, out_path,
            expect_unlit=True, tri_budget=settings.tri_budget)

        success = True
        return ConvertResult(clone.name, out_path, colour.kind, facts)

    except ConvertError:
        if clone is not None and clone.name in bpy.data.objects:
            _delete_object(clone)
        raise
    except Exception as exc:  # noqa: BLE001
        if clone is not None and clone.name in bpy.data.objects:
            _delete_object(clone)
        raise ConvertError(str(exc)) from exc
    finally:
        temp.purge()
        if success:
            state.restore_engine_and_mode(context)
            if clone is not None and clone.name in bpy.data.objects:
                ensure_active(context, clone)   # leave the result selected for the user
        else:
            state.restore(context)
