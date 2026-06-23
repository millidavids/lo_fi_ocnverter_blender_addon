# Tests

## `test_quantize.py` — median-cut quantizer (standalone, numpy only)

No Blender needed: it loads `pipeline/pixelate.py` by file path (so it doesn't
pull in the bpy-dependent package). Run with any Python that has numpy, e.g.
Blender's bundled interpreter:

```bash
/Applications/Blender.app/Contents/Resources/5.1/python/bin/python3.13 tests/test_quantize.py
# or, if your system Python has numpy:
python3 tests/test_quantize.py
# or via pytest:
pytest tests/test_quantize.py
```

## Visual / integration verification (needs Blender)

The pipeline is verified end-to-end by running the headless converter against a
real **textured** fixture and rendering the result (see the cardinal rule in the
main README). The standing fixture is **referenced, not committed** (it is 34 MB):

```
../3d_model_generator/work/buddha/scene_textured.glb
```

Example end-to-end check:

```bash
/Applications/Blender.app/Contents/MacOS/Blender --background \
  --python scripts/run_headless.py -- \
  ../3d_model_generator/work/buddha/scene_textured.glb /tmp/buddha_lofi.glb \
  --tris 1500 --tex 128 --colors 32 \
  --render-geo /tmp/geo.png --render-tex /tmp/tex.png
```

Then open `/tmp/geo.png` and `/tmp/tex.png` and actually look at them — a valid
`.glb` of a blob is still a blob.
```
