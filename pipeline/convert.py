"""Orchestrator — the one entry point the operator and headless runner both call.

Owns the NON-NEGOTIABLE safety contract (plan §4 step 0):
  * operate on a DUPLICATE of the active object, never the user's scan;
  * save scene state and restore it (engine, mode, selection);
  * on ANY failure delete the partial clone and fully restore;
  * purge temporary datablocks in `finally` even on success.
"""

import os
import re

import bpy
from mathutils import Matrix, Vector

from ..utils.scene_state import SceneState
from . import bake, cartoonize, decimate, heal, material, normalize, pixelate, prep
from . import export as export_mod
from . import resolution
from . import uv as uv_mod
from . import watertight
from ._context import ensure_active, ensure_object_mode

# Output paths that mean "auto-derive from the source object".
_AUTO_OUTPUT = ("", "//lofi_export.glb")
_TEX_SUBDIRS = ("textures", "texture", "maps", "tex", "source")


class ConvertError(Exception):
    pass


def _source_asset_dir(obj):
    """Directory of the source object's on-disk texture (its asset folder), or None.
    Scans EVERY image node in the object's materials (not just the base-colour one,
    which a freshly-imported glTF material doesn't expose until prep runs). Steps up out
    of a textures/ subfolder so the .glb lands beside the asset. Packed/embedded textures
    (e.g. inside a .glb) have no on-disk path -> None."""
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or not mat.use_nodes:
            continue
        for node in mat.node_tree.nodes:
            img = getattr(node, "image", None)
            if img is None or not img.filepath:
                continue
            d = os.path.dirname(bpy.path.abspath(img.filepath))
            if os.path.isdir(d):
                if os.path.basename(d).lower() in _TEX_SUBDIRS and os.path.isdir(os.path.dirname(d)):
                    return os.path.dirname(d)
                return d
    return None


def default_output_path(obj):
    """Sensible .glb output derived from the source: <asset-or-blend-or-home>/<name>_lofi.glb."""
    name = re.sub(r"[^\w.-]+", "_", obj.name).strip("_") or "lofi"
    blend_dir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else None
    d = _source_asset_dir(obj) or blend_dir or os.path.expanduser("~")
    return os.path.join(d, f"{name}_lofi.glb")


def _effective_settings(source_obj, settings):
    """Resolve the geometry/material sliders into concrete budgets, relative to the
    SOURCE's own resolution (read before any processing mutates the clone). Manual mode
    uses the raw tri/tex/palette fields verbatim. Returns a non-mutating overlay."""
    if getattr(settings, "manual_budgets", False):
        return settings
    st = resolution.source_tris(source_obj)
    tx = resolution.source_tex_res(source_obj)
    tris = resolution.resolve_geo(st, settings.geo_resolution)
    tex, pal = resolution.resolve_mat(tx, settings.mat_resolution)
    print(f"lofi.resolution: source ~{st} tris / {tx}px  ->  geo {settings.geo_resolution:.2f}"
          f"={tris} tris, mat {settings.mat_resolution:.2f}={tex}px/{pal} colours")
    return resolution.EffectiveSettings(
        settings, tri_budget=tris, tex_size=tex, palette_colors=pal)


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
        # Resolve the sliders into budgets from the SOURCE's resolution, before any
        # processing mutates a clone. `eff` overlays tri_budget/tex_size/palette_colors;
        # everything else delegates to the live settings. (Manual mode -> raw settings.)
        eff = _effective_settings(source_obj, settings)
        # Capture the source's world size/centre up-front, so "keep original size" can
        # restore the unit-normalized result back to the original scale + position.
        _bb = [source_obj.matrix_world @ Vector(c) for c in source_obj.bound_box]
        orig_size = max(max(v[i] for v in _bb) - min(v[i] for v in _bb) for i in range(3))
        orig_center = sum(_bb, Vector()) / 8.0

        # --- HI-POLY: the prepped clone, kept full-res as the bake source -----
        hipoly = _duplicate(context, source_obj)
        hipoly.name = source_obj.name + "_hipoly"
        ensure_active(context, hipoly)
        colour = prep.run(hipoly, eff, context)
        _assert_nonempty(hipoly, "prep")
        if settings.do_heal:
            heal.run(hipoly, eff, context)
            _assert_nonempty(hipoly, "heal")
        if settings.do_watertight:
            watertight.run(hipoly, eff, context)
        if settings.do_normalize:
            normalize.run(hipoly, eff, context)
        # optional perf safety valve: cap the bake-source poly count (default off)
        cap = int(getattr(settings, "bake_source_cap", 0))
        if cap and len(hipoly.data.polygons) > cap:
            decimate.decimate_to(hipoly, cap, context)

        # --- LO-POLY: duplicate the (normalized) hi-poly, decimate, re-UV -----
        lopoly = _duplicate(context, hipoly)
        lopoly.name = source_obj.name + "_lofi"
        ensure_active(context, lopoly)
        if settings.do_decimate:
            decimate.run(lopoly, eff, context)
            _assert_nonempty(lopoly, "decimate")
        old_uv, new_uv = uv_mod.run(lopoly, eff, context)

        # --- BAKE hi -> lo, then drop the hi-poly ----------------------------
        baked = bake.run(hipoly, lopoly, eff, context, colour, old_uv, new_uv, temp)
        _delete_object(hipoly)
        hipoly = None

        image = baked["albedo"]
        if getattr(settings, "do_cartoonize", False):
            image = cartoonize.run(
                baked["albedo"], eff, eff.tex_size,
                ao_img=baked["ao"], cavity_img=baked["cavity"], temp=temp,
                structure_done=baked.get("structure_cartoonized", False))
            temp.images.append(baked["albedo"])
        if settings.do_pixelate:
            pixelate.run(image, eff)
        material.run(lopoly, eff, context, image, temp)

        # Keep Original Size: the bake ran on a unit-normalized object (robust cage);
        # now scale the result back to the source's world size + centre, so the lo-fi is
        # a drop-in at the original scale. Only meaningful when Normalize ran (else the
        # clone already carries the source transform). Rotation isn't re-applied.
        if getattr(settings, "keep_original_size", False) and settings.do_normalize \
                and orig_size > 1e-9 and max(lopoly.dimensions) > 1e-9:
            f = orig_size / max(lopoly.dimensions)
            lopoly.data.transform(Matrix.Translation(orig_center) @ Matrix.Scale(f, 4))
            lopoly.data.update()
            print(f"lofi.size: kept original size ~{orig_size:.4f}u at {tuple(round(c,3) for c in orig_center)}")

        out = (settings.output_path or "").strip()
        if out in _AUTO_OUTPUT:                  # unset/placeholder -> derive from source
            out = default_output_path(source_obj)
        out_path = bpy.path.abspath(out)
        facts = export_mod.run(
            lopoly, eff, context, out_path,
            expect_unlit=False, tri_budget=eff.tri_budget)   # iter-6: lit PBR

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
