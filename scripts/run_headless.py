"""Headless driver for the Lo-Fi converter.

    blender --background --python scripts/run_headless.py -- IN.ext OUT.glb [opts]

Imports IN (glb/gltf/obj/ply/usdz/fbx/stl), converts the first mesh, writes
OUT.glb. The same pipeline.convert the operator uses, so this is the headless /
batch path for free.

Options: --tris N  --tex N  --colors N  --size F  --no-heal  --no-watertight
         --no-decimate  --no-normalize  --no-pixelate  --no-gpu
         --render-geo PNG  --render-tex PNG
"""

import argparse
import importlib
import os
import sys

import bpy


def _bootstrap_package():
    this = os.path.dirname(os.path.abspath(__file__))   # .../<pkg>/scripts
    repo = os.path.dirname(this)                         # .../<pkg>
    parent = os.path.dirname(repo)
    pkg = os.path.basename(repo)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return importlib.import_module(pkg)


def _import_mesh(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    low = path.lower()
    if low.endswith((".glb", ".gltf")):
        bpy.ops.import_scene.gltf(filepath=path)
    elif low.endswith(".obj"):
        bpy.ops.wm.obj_import(filepath=path)
    elif low.endswith(".ply"):
        bpy.ops.wm.ply_import(filepath=path)
    elif low.endswith(".usdz") or low.endswith(".usd") or low.endswith(".usdc"):
        bpy.ops.wm.usd_import(filepath=path)
    elif low.endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=path)
    elif low.endswith(".stl"):
        bpy.ops.wm.stl_import(filepath=path)
    else:
        raise SystemExit(f"unsupported input format: {path}")
    mesh = next((o for o in bpy.context.scene.objects if o.type == "MESH"), None)
    if mesh is None:
        raise SystemExit("no mesh found in input")
    bpy.context.view_layer.objects.active = mesh
    return mesh


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="run_headless")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--tris", type=int)
    p.add_argument("--tex", type=int)
    p.add_argument("--colors", type=int)
    p.add_argument("--size", type=float)
    p.add_argument("--no-heal", action="store_true")
    p.add_argument("--no-watertight", action="store_true")
    p.add_argument("--no-decimate", action="store_true")
    p.add_argument("--no-normalize", action="store_true")
    p.add_argument("--no-pixelate", action="store_true")
    p.add_argument("--no-shading", action="store_true", help="skip baked-AO shading")
    p.add_argument("--no-gpu", action="store_true")
    p.add_argument("--render-geo")
    p.add_argument("--render-tex")
    args = p.parse_args(argv)

    addon = _bootstrap_package()
    addon.register()

    mesh = _import_mesh(args.input)

    s = bpy.context.scene.lofi_settings
    s.output_path = args.output
    if args.tris is not None:
        s.tri_budget = args.tris
    if args.tex is not None:
        s.tex_size = args.tex
    if args.colors is not None:
        s.palette_colors = args.colors
    if args.size is not None:
        s.target_size = args.size
    s.do_heal = not args.no_heal
    s.do_watertight = not args.no_watertight
    s.do_decimate = not args.no_decimate
    s.do_normalize = not args.no_normalize
    s.do_pixelate = not args.no_pixelate
    s.bake_shading = not args.no_shading
    s.use_gpu = not args.no_gpu

    convert_mod = importlib.import_module(addon.__name__ + ".pipeline.convert")
    result = convert_mod.convert(bpy.context, mesh, s)
    print("RESULT:", result.summary())

    if args.render_geo or args.render_tex:
        render_check = importlib.import_module(addon.__name__ + ".utils.render_check")
        clone = bpy.data.objects.get(result.clone_name)
        if args.render_geo and clone:
            render_check.render_object(clone, os.path.abspath(args.render_geo), mode="geometry")
        if args.render_tex and clone:
            render_check.render_object(clone, os.path.abspath(args.render_tex), mode="textured")


if __name__ == "__main__":
    main()
