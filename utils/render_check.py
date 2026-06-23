"""Verification renders — adapted from ../3d_model_generator scripts/render_preview.py.

Two modes (the geometry/texture distinction matters — review P3-13):
  * "geometry": Workbench + cavity, single colour — shows shape/topology only.
  * "textured": Workbench TEXTURE colour — shows the baked/palette texture as
    mapped onto the mesh (works on a re-imported .glb, whose material carries an
    Image Texture node).

Used at every phase gate: render to PNG, then open the PNG to actually look.
"""

import bpy
import mathutils


def _frame_camera(obj, scene):
    bb = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
    center = sum(bb, mathutils.Vector()) / 8.0
    size = max(max(v[i] for v in bb) - min(v[i] for v in bb) for i in range(3)) or 1.0

    cam_data = bpy.data.cameras.new("lofi_cam")
    cam = bpy.data.objects.new("lofi_cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    d = size * 2.0
    cam.location = center + mathutils.Vector((d * 0.7, -d * 0.9, d * 0.55))
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    return cam


def render_object(obj, out_png, mode="geometry", resolution=900):
    scene = bpy.context.scene

    # Isolate the target: hide everything else from the render so a leftover
    # original (the convert source stays in the scene) can't occlude the result.
    hidden = []
    for o in scene.objects:
        if o is not obj and not o.hide_render:
            o.hide_render = True
            hidden.append(o)

    cam = _frame_camera(obj, scene)
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.film_transparent = False
    scene.render.filepath = out_png
    # Faithful colours: Blender 4.x defaults the view transform to AgX, which
    # desaturates/washes the preview. Standard shows the texture as authored.
    try:
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
    except Exception:  # noqa: BLE001
        pass

    scene.render.engine = "BLENDER_WORKBENCH"
    shading = scene.display.shading
    if mode == "textured":
        shading.light = "FLAT"
        shading.color_type = "TEXTURE"
        shading.show_cavity = False
    else:
        shading.light = "STUDIO"
        shading.color_type = "SINGLE"
        shading.show_cavity = True

    bpy.ops.render.render(write_still=True)

    bpy.data.objects.remove(cam, do_unlink=True)
    for o in hidden:
        o.hide_render = False
    print(f"lofi.render_check: {mode} -> {out_png}")
    return out_png


def save_image(image, out_png):
    """Write an image datablock to a PNG so it can be inspected directly."""
    image.filepath_raw = out_png
    image.file_format = "PNG"
    image.save()
    return out_png
