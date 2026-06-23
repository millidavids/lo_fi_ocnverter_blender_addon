"""Cycles device setup for the EMIT bake — Metal GPU with CPU fallback.

Adapted from the sibling ../3d_model_generator scripts/rebake.py (git 87459cf).
At 64-256px on ~1500 tris the bake is trivially fast on CPU, so GPU is a
nice-to-have; we always keep the CPU fallback.
"""

import bpy


def setup_cycles(scene, use_gpu=True):
    """Switch `scene` to Cycles and pick a device. Returns the device str used."""
    scene.render.engine = "CYCLES"
    device = "CPU"
    if use_gpu:
        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            prefs.compute_device_type = "METAL"
            prefs.refresh_devices()
            has_gpu = False
            for d in prefs.devices:
                # Enable GPU devices; leave CPU device unticked so it isn't double-counted.
                if d.type != "CPU":
                    d.use = True
                    has_gpu = True
            if has_gpu:
                scene.cycles.device = "GPU"
                device = "GPU(Metal)"
            else:
                scene.cycles.device = "CPU"
        except Exception as exc:  # noqa: BLE001
            print(f"lofi.bake_device: GPU unavailable ({exc}); using CPU")
            scene.cycles.device = "CPU"
    else:
        scene.cycles.device = "CPU"

    scene.cycles.samples = 1
    return device
