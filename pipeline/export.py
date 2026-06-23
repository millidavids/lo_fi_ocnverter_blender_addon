"""Export the lo-fi object to .glb and verify the result.

`export_image_format='AUTO'` embeds PNG — note 'PNG' is an INVALID enum in 5.1.2
and throws. Z-up -> +Y-up is automatic. After export we re-parse the .glb and
assert the properties a render can't show (see utils.glb_verify).
"""

import os

import bpy

from ._context import ensure_active
from ..utils import glb_verify


def run(obj, settings, context, filepath, *, expect_unlit, tri_budget):
    ensure_active(context, obj)          # only the clone selected -> use_selection

    out_dir = os.path.dirname(filepath) or "."
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"cannot create output directory '{out_dir}': {exc}")
    # Pre-check writability so a bad path fails cleanly here, rather than as a
    # messy Python error surfacing from inside the nested glTF export operator.
    if not os.access(out_dir, os.W_OK):
        raise RuntimeError(f"output directory not writable: '{out_dir}'")

    bpy.ops.export_scene.gltf(
        filepath=filepath,
        export_format="GLB",
        use_selection=True,
        export_image_format="AUTO",
    )

    facts = glb_verify.verify_or_raise(
        filepath,
        expected_tex=settings.tex_size,     # dims always checked, independent of lit/unlit
        tri_budget=tri_budget,
        expect_unlit=expect_unlit,
    )
    print(f"lofi.export: wrote {filepath}  facts={facts}")
    return facts
