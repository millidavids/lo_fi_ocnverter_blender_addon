# Lo-Fi Converter — Blender Add-on: Implementation Plan

> Self-contained for a fresh-context implementing agent. Assumes no knowledge of
> the sibling project except the reusable files it points to.
> Revised after an independent staff-engineer review (findings folded in below;
> the bake recipe and the test-fixture import are the two things most likely to
> bite — read §3 and §4 step 6 carefully).

## 1. What this is

A Blender add-on that converts a **reconstructed 3D model** (a photogrammetry /
scan mesh) into a **lo-fi, low-poly, pixelated game asset** in the aesthetic of
*Abiotic Factor* (PS1/retro era), exported as glTF `.glb`.

**It does NOT do reconstruction.** It consumes a mesh, *however it was made*, and
does the one thing reconstruction tools don't: the lo-fi transform.

**Input sources (all CUDA-free — the user has no NVIDIA GPU):**
- **Apple Object Capture** (macOS / Apple Silicon, Metal GPU) → textured
  `OBJ`/`USDZ` with proper per-vertex UVs. Imports into Blender cleanly. The
  leanest native option on the Mac.
- **The sibling project's COLMAP + CPU-OpenMVS pipeline** (`../3d_model_generator`,
  containerized, cross-platform) → a textured mesh. **Import its result as
  glTF/OBJ, not the raw OpenMVS `.ply`** (see §3 — Blender's PLY importer drops
  OpenMVS's per-face UVs).
- Any other mesh (downloaded, sculpted, etc.).

> Note: tools like Scene Scanner, Meshroom (dense), and RealityCapture were
> considered and rejected — they require CUDA/NVIDIA, which the user does not
> have. The add-on is deliberately source-agnostic so it never depends on one.

**Input contract:** a Blender **mesh object** carrying colour as either (a) a
material with an Image Texture, or (b) **vertex colours** (a Color Attribute —
common for scan meshes). A raw point cloud (0 faces) is out of scope for v1 (§6).

**Target Blender:** 4.2 LTS+ (developed against 5.1.2).

## 2. The "PS1 look" is asset-side only

The asset carries: (1) low-poly geometry, (2) a small palettized texture, (3)
material flags — **unlit** (`KHR_materials_unlit`) + **nearest** filtering (no
mipmaps). Vertex jitter, affine warp, dithering, low-res rendering are the game
engine's shaders, not baked here.

## 3. Reusable assets & lessons from `../3d_model_generator`

- **`scripts/rebake.py`** — a WORKING headless Blender script that Smart-UV-
  unwraps a mesh and bakes a texture onto the new layout via Cycles, with Metal
  GPU + CPU fallback. Seeds `uv.py` + `bake.py` + `utils/bake_device.py`. **Read
  it — but note the bake-recipe correction in §4 step 6; do not copy its bake
  pass flags blindly.**
- **`scripts/render_preview.py`** — headless Workbench geometry render (3/4 view).
  Use it (or the viewport) for the mandatory visual verification at every phase.
- **There is NO glb-verify script in the sibling** — you will WRITE
  `utils/glb_verify.py` (parse the `.glb` JSON chunk; assert `KHR_materials_unlit`
  in `extensionsUsed`, sampler `magFilter == 9728`, texture dims, tri count).

### Test fixture (IMPORTANT — the obvious one is a trap)
The sibling's `work/monstree_out.work/scene_textured.ply` is a real 1.12M-face
textured reconstruction, BUT **Blender's PLY importer will silently drop its
texture**: OpenMVS stores per-face (`texcoord`) UVs + a `TextureFile` comment,
and Blender's importer reads neither. So that PLY imports as untextured geometry.
For a real **textured** regression input, do ONE of:
- Export a textured **glTF/OBJ** of that mesh (e.g. via the sibling's own glTF
  exporter, or MeshLab) and check it in as the fixture — glTF/OBJ carry the UVs
  + texture that Blender imports correctly; **or**
- Use a real **Apple Object Capture** `OBJ`/`USDZ` (clean vertex UVs + texture);
  **or**
- For the *vertex-colour* path, any mesh with a Color Attribute.
Keep a small textured fixture in `tests/fixtures/` so Phases 2–3 validate the
real bake path, not a primitive.

### Hard-won lessons (apply these)
- **VISUAL VERIFICATION IS MANDATORY.** The sibling repeatedly passed
  "tri-count / file-size / valid glTF" checks while emitting garbage geometry
  because nobody *rendered* it. A valid `.glb` of a blob is still a blob. **Render
  and look** (geometry AND texture) at every milestone.
- **Connectivity is geometric in Blender** (shared verts) — so the UV-atlas-seam
  fragmentation bug that wrecked the sibling's "largest component" step is a
  NON-issue here. Use linked/loose-parts selection directly.
- **Decimate budget:** photogrammetry meshes have many open boundaries. Blender's
  Decimate (Collapse) *does* collapse them (good), but `ratio` only approximates a
  triangle target — verify, use a tolerance band, cap iterations (§4 step 3).
- **Bake:** `EMIT` bake of flat colour is the safe path; `DIFFUSE` has pass-flag
  and lighting-contamination traps (§4 step 6).
- **Texture orientation / V-flip** bit the sibling once — verify visually on a
  known asset before trusting the bake.
- **Largest-component can be the wrong thing** (a scan's *floor/table* can be the
  biggest connected part). Object Capture outputs are object-only (fine); for
  table-on outputs, mask/crop upstream. Keep heal a toggle.

## 4. The transform pipeline (on a COPY of the active object)

Order: **clone & isolate → prep → heal → decimate → normalize → re-UV → bake →
pixelate → material → export.** Each transform step is a toggle (defaults on).

0. **Clone & save scene state (NON-NEGOTIABLE).** Operate on a **duplicate** of
   the active object — never mutate the user's scan in place. Record and restore
   afterward: active object, selection, mode (force OBJECT during the run),
   `scene.render.engine` (the bake flips it to Cycles), and the active UV map.
   The Operator uses `bl_options = {'REGISTER', 'UNDO'}`. On ANY failure, delete
   the partial copy and restore state so the user's `.blend` is never left dirty.

1. **Prep / detect.** Mesh only. `transform_apply` (location/rot/scale).
   **Cleanup degenerate geometry**: merge-by-distance (remove doubles) + delete
   loose verts/edges + delete zero-area faces — photogrammetry meshes have these
   and they break Decimate/UV. Detect the colour source:
   - **multiple material slots** → join to a single material before bake (or pick
     the dominant slot); document the choice.
   - **a material with an Image Texture** wired to colour → textured path.
   - **a Color Attribute (vertex colours), or a material with no image** → vertex/
     flat-colour path.
   - **neither colour source** → bake a solid fill (or warn). Don't crash.

2. **Heal — largest connected component** (toggle, default on). `bmesh` linked
   sets; keep the most-faces set; delete the rest. (Geometric — see lessons. Note
   the floor caveat: a toggle, off-able.)

2b. **Make watertight — fill holes** (toggle, default on). A reconstruction is
    *open* wherever no photo saw the surface — e.g. the underside of an object
    that sat on a table — leaving boundary holes. In Edit Mode: Select All →
    `bpy.ops.mesh.fill_holes(sides=0)` caps every boundary loop (`sides=0` = all
    holes regardless of size; the default `30` closes only tiny ones, so a big
    neck/base opening stays open). Do this **before decimate**: a closed,
    boundary-free mesh decimates far more predictably — open boundary edges were
    exactly what tripped up the sibling project's decimator (it needed a "sloppy"
    fallback to hit the budget on bordered meshes).
    - The cap is **invented geometry no photo saw**, so it bakes to a flat/default
      colour (untextured) — fine for a base that's on the ground or hidden. The
      only way to a *real, textured* bottom is to also photograph the underside.
    - `Grid Fill` gives nicer cap topology on roughly-circular openings but needs
      an even boundary-edge count; `fill_holes` is the robust default. For
      badly-broken meshes, a **Voxel Remesh** makes the whole surface watertight
      but discards the original topology + UVs (which re-UV + re-bake regenerate
      anyway) — offer it as an opt-in fallback, not the default.

3. **Decimate to a triangle budget** (default ≈ 1500; range 300–5000).
   **Triangulate FIRST**, then compute `ratio = target_tris / current_tris`
   **against the post-triangulate face count** (not the pre-triangulate quad
   count — that was a reviewer-flagged bug). Apply Decimate (Collapse). Accept a
   **tolerance band** (target ±15%); if outside, adjust ratio and re-run, **cap at
   ~3 iterations** (collapse compounds error and can oscillate). Render to confirm
   the shape survived the reduction.

4. **Normalize.** Origin → geometry bounds-centre; move to world origin; scale so
   the longest bbox edge = a configurable size (default 1.0). Orientation left
   as-is (gauge-free).

5. **Re-UV.** New UV map; **Smart UV Project** (reuse `rebake.py`; `island_margin
   ≈ 0.02`); make it active. (Do this AFTER decimate — unwrapping 1.1M faces is
   needlessly slow; at ~1500 tris it's instant.)

6. **Bake colour → a new image** (default 128px; range 64–256). **Use `EMIT`
   bake** — it bakes flat, unlit colour with no lighting/pass-flag subtlety, and
   unifies both source paths:
   - Build a temporary **Emission** material whose Emission Color is driven by the
     colour source: the source Image Texture sampled via the **original** UV map
     (add a UV Map node pinned to the old UV — exactly `rebake.py`'s trick), OR
     the **Color Attribute** node for vertex colours, OR a solid RGB.
   - Add the target Image Texture node (the new blank image), select + make it
     active (the bake destination), with the **new** UV map active.
   - Cycles, GPU (Metal) with CPU fallback (reuse `rebake.py`'s device setup —
     though at these sizes CPU is fast; GPU is optional for v1). `bake(type='EMIT')`,
     small margin. Save the image.
   - (If you ever use `DIFFUSE` instead: you MUST set
     `render.bake.use_pass_color=True` and direct/indirect False — `rebake.py`
     omits the color flag and only works by default. `EMIT` avoids this.)

7. **Pixelate / palettize** to N colours (default 64; range 16–256). Blender
   bundles **numpy** but NOT Pillow — implement **median-cut** in numpy
   (`pipeline/pixelate.py`, ~60–80 lines): read `image.pixels` (flat RGBA floats),
   median-cut RGB to N colours, remap pixels, write back. Baking at the target
   size already gives pixel-art resolution; if you bake larger, nearest-downscale
   first. **Unit-test the quantizer standalone** (`tests/test_quantize.py`, no
   Blender needed).

8. **Material — unlit + nearest.** Final material = Image Texture (the palettized
   image), **Interpolation = `Closest`** → **Emission** → Material Output (NO
   Principled BSDF). Confirmed: Emission-only ⇒ `KHR_materials_unlit`; `Closest`
   ⇒ NEAREST sampler (9728). (This is the same emission structure as the bake
   material — just swap in the pixelated image.)

9. **Export `.glb`.** `export_scene.gltf(filepath, export_format='GLB',
   use_selection=True, export_image_format='PNG')`. Z-up→+Y-up is automatic.
   Then **`glb_verify`**: assert `KHR_materials_unlit`, sampler 9728, texture
   dims, tri count — AND re-import + render to confirm it still looks right
   (exporters have regressed on samplers historically).

## 5. Add-on architecture (feature-sliced; small concern-focused files)

```
lo_fi_converter_blender_addon/
  __init__.py            # bl_info + register/unregister only
  properties.py          # LoFiSettings(PropertyGroup): budgets, toggles, output path
  pipeline/              # PURE functions on bpy objects (no UI) — callable headless
    prep.py  heal.py  decimate.py  normalize.py
    watertight.py        # fill boundary holes (fill_holes; voxel-remesh fallback) — before decimate
    uv.py                # smart UV project
    bake.py              # EMIT bake (adapt rebake.py)  [split from uv per CLAUDE.md cohesion]
    pixelate.py          # numpy median-cut palette
    material.py          # emission + closest unlit material
    export.py            # glb export + verify
    convert.py           # orchestrates all of the above given settings; owns the
                         #   clone + scene-state save/restore + failure cleanup (§4 step 0)
  utils/
    bake_device.py       # Cycles Metal/CPU setup (from rebake.py)
    scene_state.py       # save/restore active/selection/mode/engine
    glb_verify.py        # parse .glb, assert unlit + nearest + dims (WRITE this)
  operators/convert_op.py# LOFI_OT_convert — thin wrapper calling pipeline.convert
  ui/panel.py            # LOFI_PT_panel, "Lo-Fi" tab in the 3D viewport N-panel
  scripts/run_headless.py# blender --background --python run_headless.py -- in.ext out.glb [opts]
  tests/
    test_quantize.py     # standalone numpy unit test
    fixtures/            # a small TEXTURED glb/obj + a vertex-coloured mesh
  README.md
```

**Separation rule:** all real work in `pipeline/` as pure functions taking
(object, settings); the Operator and the headless script both call
`pipeline.convert(...)`. Gives batch/headless for free and keeps the UI thin.

## 6. Decisions & risks (review-hardened)

1. **Bake type = `EMIT`** (not DIFFUSE) — unifies textured + vertex-colour, avoids
   pass-flag/lighting traps. *(Top fix from review.)*
2. **Convert a duplicate; restore scene state; clean up on failure.** *(Top fix.)*
3. **Real textured test fixture** (glTF/OBJ or Object Capture output) — the
   OpenMVS `.ply` loses its texture in Blender. *(Top fix.)*
4. **Decimate budget** = post-triangulate ratio + tolerance band + capped
   iterations.
5. **Multi-material / no-image / vertex-colour / no-colour** all handled in prep
   (§4 step 1) — don't assume `materials[0]` + one image (the `rebake.py`
   assumption).
6. **Failure modes have defined behavior:** bake init/throw, degenerate UVs, heal
   leaving nothing, missing/unpacked source image → catch, clean up the clone,
   surface a clear error; never leave the scene dirty.
7. **Point cloud (0 faces)** is **out of scope for v1** — document that the user
   meshes first (Object Capture outputs a mesh; the sibling's OpenMVS
   `ReconstructMesh`; or Blender remesh). A later phase may add an optional remesh.
8. **GPU(Metal) bake is optional for v1** — at 64–256px on ~1500 tris, CPU bake is
   trivially fast; keep the fallback, don't over-invest in device setup.
9. **Version drift** — pin `bl_info["blender"]`; isolate version-sensitive ops
   (bake args, glTF export args) so they're easy to adjust.

## 7. Build phases (each yields a VERIFIED, looked-at artifact)

- **Phase 0 — Scaffold.** `bl_info`, register/unregister, empty settings, panel,
  no-op operator. *Verify:* enables in Preferences, "Lo-Fi" panel appears.
  Establish the dev install/reload loop (symlink into `scripts/addons/`, or
  iterate via `run_headless.py`).
- **Phase 1 — Geometry transform, headless.** Import a mesh → clone → cleanup →
  heal → **fill holes (watertight)** → triangulate → decimate → normalize →
  export geometry-only `.glb`. *Verify by RENDERING:* a coherent low-poly object
  near the budget, with **no open boundaries** — rotate to confirm the underside
  is capped (this is where the sibling's bugs lived — look hard).
- **Phase 2 — UV + EMIT bake + textured export** (against the REAL textured
  fixture). Smart-UV → emission bake → Closest/Emission material → `.glb`.
  *Verify:* re-imports with the texture correctly mapped (orientation!);
  `glb_verify` asserts unlit + nearest.
- **Phase 3 — Pixelate / palette.** numpy median-cut. *Verify:* visibly pixel-art
  at N colours; reads as lo-fi.
- **Phase 4 — Vertex-colour input path.** EMIT-bake the Color Attribute. *Verify*
  on a vertex-coloured fixture.
- **Phase 5 — UI + safety.** Panel + properties; the duplicate/restore/undo
  contract (§4 step 0); per-step toggles; output path; Convert button on the
  active object. *Verify* interactively, including undo and re-run.
- **Phase 6 — Real-input + polish.** Run a real Object Capture (or sibling-
  reconstructed) asset end-to-end; presets; README (install, usage, capture
  tips); `run_headless.py` batch.

## 8. Verification (the cardinal rule)

Every phase: render the geometry AND inspect the texture AND re-import the
exported `.glb`. "It ran / produced a valid file" is NOT done — *looking at it*
is. Keep a textured fixture as the standing visual regression.

## 9. Open questions

- Which reconstruction front-end will the user standardize on (Apple Object
  Capture on the Mac is the recommended lean native path; the sibling's container
  remains the cross-platform/Windows CPU engine)? Confirm its export format
  (OBJ/USDZ/glTF) so the import path is tested against the real thing.
- Default budgets (tris / texture px / palette) for the target game.
- Real-world scale (unit bbox default; a known-size reference is a future feature).
