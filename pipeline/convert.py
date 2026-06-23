"""Orchestrator — the one entry point the operator and headless runner both call.

Owns the NON-NEGOTIABLE safety contract (plan §4 step 0):
  * operate on a DUPLICATE of the active object, never the user's scan;
  * save scene state and restore it (engine, mode, selection);
  * on ANY failure delete the partial clone and fully restore;
  * purge temporary datablocks in `finally` even on success.
"""

import bpy

from ..utils.scene_state import SceneState
from . import bake, cartoonize, decimate, heal, material, normalize, pixelate, prep
from . import export as export_mod
from . import uv as uv_mod
from . import watertight
from ._context import ensure_active, ensure_object_mode


class ConvertError(Exception):
    pass


class TempData:
    """Tracks transient datablocks created during a run so they can be purged.

    NB: the delivered texture and final material are NOT temp — they belong to the
    clone and must persist. Transient = the per-slot emission bake materials, and
    (in cartoonize mode) the supersampled albedo + AO + cavity aux images that the
    final downscaled texture replaces."""

    def __init__(self):
        self.materials = []
        self.images = []

    def purge(self):
        for m in self.materials:
            try:
                if m.name in bpy.data.materials:
                    bpy.data.materials.remove(m)
            except Exception:  # noqa: BLE001
                pass
        for im in self.images:
            try:
                if im.name in bpy.data.images:
                    bpy.data.images.remove(im)
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


def _cleanup(*objs):
    """Delete the given objects if still present (used on failure paths)."""
    for o in objs:
        if o is not None and o.name in bpy.data.objects:
            _delete_object(o)


def convert(context, source_obj, settings):
    state = SceneState.capture(context)
    temp = TempData()
    hipoly = None      # full-res bake source (prepped, original UVs) — transient
    lopoly = None      # decimated deliverable
    success = False
    try:
        ensure_object_mode(context)

        # --- HI-POLY: the prepped clone, kept full-res as the bake source -----
        hipoly = _duplicate(context, source_obj)
        hipoly.name = source_obj.name + "_hipoly"
        ensure_active(context, hipoly)
        colour = prep.run(hipoly, settings, context)
        _assert_nonempty(hipoly, "prep")
        if settings.do_heal:
            heal.run(hipoly, settings, context)
            _assert_nonempty(hipoly, "heal")
        if settings.do_watertight:
            watertight.run(hipoly, settings, context)
        if settings.do_normalize:
            normalize.run(hipoly, settings, context)
        # optional perf safety valve: cap the bake-source poly count (default off)
        cap = int(getattr(settings, "bake_source_cap", 0))
        if cap and len(hipoly.data.polygons) > cap:
            decimate.decimate_to(hipoly, cap, context)

        # --- LO-POLY: duplicate the (normalized) hi-poly, decimate, re-UV -----
        lopoly = _duplicate(context, hipoly)
        lopoly.name = source_obj.name + "_lofi"
        ensure_active(context, lopoly)
        if settings.do_decimate:
            decimate.run(lopoly, settings, context)
            _assert_nonempty(lopoly, "decimate")
        old_uv, new_uv = uv_mod.run(lopoly, settings, context)

        # --- BAKE hi -> lo (albedo + AO + cavity), then drop the hi-poly ------
        baked = bake.run(hipoly, lopoly, settings, context, colour, old_uv, new_uv, temp)
        _delete_object(hipoly)
        hipoly = None

        image = baked["albedo"]
        if getattr(settings, "do_cartoonize", False):
            image = cartoonize.run(
                baked["albedo"], settings, settings.tex_size,
                ao_img=baked["ao"], cavity_img=baked["cavity"], temp=temp,
                structure_done=baked.get("structure_cartoonized", False))
            temp.images.append(baked["albedo"])
        if settings.do_pixelate:
            pixelate.run(image, settings)
        material.run(lopoly, settings, context, image, temp)

        out_path = bpy.path.abspath(settings.output_path)
        facts = export_mod.run(
            lopoly, settings, context, out_path,
            expect_unlit=True, tri_budget=settings.tri_budget)

        success = True
        return ConvertResult(lopoly.name, out_path, colour.kind, facts)

    except ConvertError:
        _cleanup(hipoly, lopoly)
        raise
    except Exception as exc:  # noqa: BLE001
        _cleanup(hipoly, lopoly)
        raise ConvertError(str(exc)) from exc
    finally:
        temp.purge()
        if success:
            state.restore_engine_and_mode(context)
            if lopoly is not None and lopoly.name in bpy.data.objects:
                ensure_active(context, lopoly)   # leave the result selected for the user
        else:
            state.restore(context)
