# Lo-Fi Converter — Blender Add-on

Convert a **reconstructed / scanned 3D mesh** into a **lo-fi, low-poly, pixelated,
game-ready asset** in the PS1/retro aesthetic, exported as glTF `.glb`.

It does **not** do reconstruction. It consumes a mesh — however it was made — and
does the one thing reconstruction tools don't: the lo-fi transform (decimate →
re-UV → bake → **cartoonize + de-light** → palettize → lit nearest-filter material
→ export).

The texture is **de-lit**: baked capture shading is stripped out to recover a flat,
intrinsic albedo, and the material is **lit PBR** — so your engine's lights draw the
shadows on the geometry at runtime (low-poly + flat textures + dynamic lighting, à la
Abiotic Factor), instead of fighting shadows baked into the colour.

Developed and verified against **Blender 5.1.2** (targets 4.2 LTS+).

![pipeline result](docs/example.png)

## What you get

The exported `.glb` carries:
1. **Low-poly geometry**, watertight — from a few triangles up to near-original, set by
   the **Geometry** slider (see **Resolution** below).
2. **A small cartoonized + palettized texture** — from tiny/few-colours up to near-
   original, set independently by the **Material** slider; *abstracted and amplified*,
   not a washed-out downscale (see **Stylize** below).
3. **Material**: a **lit PBR** material (Principled, matte) with a de-lit base
   colour and **nearest** filtering (`magFilter = 9728`, no smoothing) — the pixel
   look, but it reacts to engine lighting.

Vertex jitter, affine warping, dithering and low-res rendering are the *game
engine's* shaders, not baked here.

## Inputs

A Blender **mesh object** carrying colour as either:
- a **material with an Image Texture** (e.g. Apple Object Capture `OBJ`/`USDZ`,
  or the sibling `../3d_model_generator` pipeline's `.glb`), or
- **vertex colours** (a Color Attribute — common for scan meshes).

Multi-material meshes are handled (each slot is baked, nothing dropped). A mesh
with no colour bakes a neutral fill rather than crashing. A raw point cloud
(0 faces) is out of scope — mesh it first (Object Capture and the sibling's
OpenMVS both output meshes; or use Blender's remesh).

## Install

### As a legacy add-on (simplest for development)

```bash
# Blender 5.1 user dir ships only extensions/, so create the legacy dir:
mkdir -p "$HOME/Library/Application Support/Blender/5.1/scripts/addons"
ln -s "$PWD" "$HOME/Library/Application Support/Blender/5.1/scripts/addons/lo_fi_converter_blender_addon"
```

Then in Blender: *Edit > Preferences > Add-ons*, search "Lo-Fi", enable it.

### As an extension (4.2+ distribution)

The repo ships a `blender_manifest.toml`. Build/validate with:

```bash
/Applications/Blender.app/Contents/MacOS/Blender --command extension validate .
/Applications/Blender.app/Contents/MacOS/Blender --command extension build --source-dir .
```

Install the resulting `.zip` via *Preferences > Get Extensions > Install from Disk*.

## Usage — UI

1. Select a mesh object.
2. Open the **3D Viewport sidebar** (press `N`) → **Lo-Fi** tab.
3. Set the two **Resolution** sliders — **Geometry** and **Material** — *independently*
   (see below); toggle any pipeline steps; set the **Output .glb** path.
4. Under **Stylize**, pick a cartoon preset (Off / Subtle / Cel / **Heavy**) and
   tweak its sliders if you like.
5. Click **Convert to Lo-Fi**.

### Resolution: two independent sliders

Geometry and material fidelity are dialled **separately**, each from ~nothing (a few
triangles / a tiny texture) up to *slightly less than the original*. So you can make a
lo-fi mesh wearing a hi-fi material, or a hi-fi mesh with a crunchy lo-fi texture —
whatever the look needs.

- The sliders are **relative to each source's own resolution** (0% = near-nothing, 100%
  ≈ the original), so the same setting adapts per object. The panel shows the resolved
  `~N tris` / `Mpx · C colours` for the active object as you drag.
- **Presets** (PS1 / Lo-Fi / N64 / Hi-Fi) just quick-set both sliders; move a slider and
  it drops to **Custom**.
- **Manual Budgets** (Advanced) ignores the sliders and uses exact triangle / texture /
  palette numbers.

The add-on works on a **duplicate** — your original scan is never modified. On
any failure the partial copy is deleted and the scene state restored, so your
`.blend` is never left dirty.

## Usage — headless / batch

```bash
/Applications/Blender.app/Contents/MacOS/Blender --background \
  --python scripts/run_headless.py -- IN.glb OUT.glb \
  --geo 0.3 --mat 0.8 --cartoon-preset CEL \
  --render-geo geo.png --render-tex tex.png
```

`--geo`/`--mat` are the two resolution sliders (0..1, relative to the source) — e.g. the
above is a low-poly mesh with a high-res material. Supports
`.glb/.gltf/.obj/.ply/.usdz/.fbx/.stl` input. Flags: `--geo --mat` (or `--manual --tris
--tex --colors` for exact budgets), `--size`, `--no-heal --no-watertight --no-decimate
--no-normalize --no-pixelate --no-gpu`, stylize: `--cartoon-preset OFF|SUBTLE|CEL|HEAVY
--no-cartoonize --saturation F --delight F --posterize N --supersample N`, and
`--render-geo PNG --render-tex PNG`.

## The pipeline (on a copy of the active object)

`prep → heal → watertight → decimate → normalize → re-UV → bake → cartoonize →
pixelate → material → export`, each a toggle.

| Step | What it does |
|------|--------------|
| **prep** | apply transforms; merge doubles; delete loose/zero-area geo; detect colour source |
| **heal** | keep the largest connected component (drops floating scraps) |
| **watertight** | `fill_holes` caps open boundaries (e.g. the unseen underside); cap UVs flattened so it bakes a flat colour |
| **decimate** | triangulate, then collapse to the triangle budget (±15% band, capped iterations) |
| **normalize** | centre on world origin; scale longest edge to the target size |
| **re-UV** | new UV map via Smart UV Project |
| **bake** | hi→lo selected-to-active Cycles **EMIT** bake of the colour source onto the new UVs at a *supersampled* resolution; near-black holes inpainted; **AO** baked as an aux map (feeds de-light) |
| **cartoonize** | (Stylize) **de-light** (strip baked shading) + abstract + flatten regions + detail-preserving downscale — see below |
| **pixelate** | numpy median-cut to an N-colour palette |
| **material** | Image (`Closest`) → Principled BSDF → **lit PBR** + nearest |
| **export** | `.glb` (GLB, embedded PNG) + `glb_verify` assertions |

## Stylize (cartoonize)

A plain bake + downscale is a low-pass filter: it averages detail away into a
**washed-out blob**. The cartoonize stage does the opposite — it *de-lights* and
*abstracts* the surface into flat cartoon regions, then finishes with a
detail-preserving downscale:

- **De-light** → recover a flat intrinsic albedo by dividing out the baked shading:
  an **AO-divide** (lift occlusion-correlated shadows, using the AO baked from the
  hi-poly) + a **Retinex low-frequency flatten** (remove broad directional shading,
  keeping albedo edges). All in linear space. This is what makes the asset relight
  cleanly — no shadows fought into the colour.
- **Guided-filter abstraction** → flat cartoon regions (decoupled from the mesh).
- **Region flatten + hue-preserving posterize** → merge a shaded-vs-lit surface into
  one flat colour (skin → ~one colour) with clean tone steps.
- **Saturation / contrast** → punchy colour (chroma-adaptive — see below).
- **DPID detail-preserving downscale** (Weber 2016) → detail survives the size cut.

No baked shadows or ink lines: the **lit** material lets the engine draw shadows on
the geometry instead. (Fine features on a coarse 1500-tri monochrome mesh stay soft
under light — bake a normal map upstream if you need them crisp.)

**Presets** (the **Stylize** dropdown): `Off` (plain bake/downscale, v1 behaviour),
`Subtle`, `Cel`, `Heavy` (default; strongest de-light + flatten). All controls stay
adjustable per preset.

**Chroma-adaptive:** the stage measures how colourful the source is and routes
automatically. Colourful subjects (fruit, painted props) get the full punchy cel
colour; **near-monochrome subjects (marble statues, stone) get *no* saturation
boost** (so capture-lighting tints + noise aren't amplified into blotches) — they
de-light to clean stone whose form is then carried by engine lighting.

## Capture tips

- **Apple Object Capture** (macOS / Apple Silicon, no NVIDIA needed) → textured
  `OBJ`/`USDZ` with clean UVs. The leanest native Mac path.
- **The sibling `../3d_model_generator`** (COLMAP + CPU-OpenMVS, containerized,
  cross-platform) → a textured `.glb`. Import its `.glb` (not the raw OpenMVS
  `.ply`, whose per-face UVs Blender drops).
- Photograph the **underside** too if you want a real textured bottom; otherwise
  the watertight cap is a flat colour (fine for a base on the ground).

## Verification

The cardinal rule: *look at it* — and now look at it **lit** (cast a light and check
the engine draws the shadows), since the albedo no longer carries them. Every
conversion can render a 3/4 view (`--render-geo` / `--render-tex`) AND runs
`utils/glb_verify.py`, which parses the `.glb` and asserts it is a **lit PBR**
material (NOT `KHR_materials_unlit`), has a base-colour texture, `magFilter == 9728`,
the texture dimensions and triangle count. The material model is **only** verifiable
from the glTF JSON — no render shows it — so that check is a hard gate.

Run the standalone numpy unit tests (no Blender — quantizer + cartoonize math):

```bash
BLPY=/Applications/Blender.app/Contents/Resources/5.1/python/bin/python3.13
"$BLPY" tests/test_quantize.py
"$BLPY" tests/test_cartoonize.py   # DPID detail-preservation, XDoG, hue-preserving posterize, chroma-adaptivity
```

## Layout

```
__init__.py        bl_info + register/unregister
properties.py      LoFiSettings (budgets, budget + stylize presets, toggles, output)
pipeline/          pure functions on bpy objects (prep, heal, watertight,
                   decimate, normalize, uv, bake, cartoonize, pixelate, material,
                   export, convert)  — callable from the operator AND headless
utils/             bake_device, scene_state, glb_verify, render_check
operators/         LOFI_OT_convert (thin wrapper over pipeline.convert)
ui/                LOFI_PT_panel ("Lo-Fi" N-panel tab, incl. Stylize section)
scripts/           run_headless.py; rebake.py (vendored reference)
tests/             test_quantize.py, test_cartoonize.py
```
