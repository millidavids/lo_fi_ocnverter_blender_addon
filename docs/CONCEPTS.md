# Lo-Fi Converter — Concepts & Methods (a primer)

This is a from-scratch explanation of the ideas behind this add-on: what each term means,
why it matters, and how we actually used it. It's written to be read top-to-bottom, but
every section stands alone, and there's a [glossary](#glossary) at the end.

The one idea to anchor everything: **a 3D object is two separate things — its *shape* and
its *surface look* — and "lo-fi" means reducing each of them, independently.**

- **Mesh** = the shape (the geometry: points in space joined into triangles).
- **Material** = the surface look (colour/texture, and how it reacts to light).

Almost everything below is "a technique for shrinking the mesh" or "a technique for shrinking
/ fixing the material," plus the plumbing that connects them.

---

## 1. Mesh vs Material — the core distinction

Imagine a papier-mâché duck.

- The **mesh** is the wire-and-paper *form* — the bumps, the beak, the curve of the body. In
  3D, that form is made of **vertices** (points), connected by **edges** (lines), which bound
  **faces**. Faces are almost always reduced to **triangles** (the GPU only really draws
  triangles), so we measure mesh complexity in **triangle count** (a.k.a. *polys* / *tri
  budget*). A photogrammetry scan might be **800,000 triangles**; a lo-fi duck might be **200**.

- The **material** is the *paint* on the surface — the yellow body, the orange beak, the black
  eyes. It's stored as a 2D image (a **texture**) plus rules for how light bounces off it. The
  texture has its own resolution (e.g. **2048×2048 pixels**, lo-fi'd to **64×64**) and its own
  notion of detail.

These are independent. You can have a blocky mesh wearing a crisp texture, or a smooth mesh
wearing a crunchy one. That independence is exactly what the **two resolution sliders**
(Geometry, Material) give you. The whole back half of the pipeline exists to take the *paint
off the high-detail duck and re-apply it onto the low-detail duck* — that's **baking** (§2.3).

---

## 2. The pipeline, stage by stage

The tool runs an ordered assembly line on a **duplicate** of your object (your scan is never
touched). Here's the line, with the concept behind each stage.

```
prep → heal → watertight → normalize → (split into hi-poly + lo-poly) →
decimate → UV unwrap → bake (hi→lo) → de-light + cartoonize → palettize → material → export
```

### 2.1 Cleaning the mesh: prep, heal, watertight, normalize

Scans are messy. Before we can shrink anything we tidy up.

- **Prep** — apply transforms, merge duplicate vertices, drop stray zero-area junk, and figure
  out where the colour comes from (a texture? vertex colours? a flat colour?).
- **Heal — "keep the largest connected component."** A scan often has floating crumbs (a bit of
  the table, a speck of noise) that aren't part of the object. "Connected component" = a clump
  of geometry all joined together. We keep the biggest clump and delete the rest.
- **Watertight — "fill holes."** A scan is usually *open* where no camera saw the surface (the
  underside of something sitting on a table). "Watertight" means *no holes* — a closed shell.
  `fill_holes` caps every open boundary loop. Why bother? Because the next step (decimate) and
  the baking behave far more predictably on a closed mesh. (A subtle gotcha lived here: the caps
  are invented geometry with garbage texture coordinates, so we force them to a single
  representative colour — see the cap-colour bug in §4.)
- **Normalize — centre + scale.** Move the object to the world origin and scale it so its
  longest edge is a known size (≈1 unit). This makes later steps **scale-invariant** (a setting
  that works on a 1-unit object works on all of them). The **"Keep Original Size"** option
  undoes this at the very end, so the output lands back at the source's real size — we normalize
  *for processing*, then restore.

### 2.2 Decimate — shrinking the mesh

**Decimation** is reducing the triangle count while keeping the silhouette as close as possible.

The method we use is **edge collapse**: repeatedly pick the edge whose removal changes the
shape least, and *collapse* it — merge its two endpoints into one, deleting the two triangles
that shared it. Do that thousands of times and 800k triangles becomes 1,500. Blender's
**Decimate modifier (Collapse)** does this; you give it a **ratio** (e.g. 0.002 = keep 0.2% of
the faces) and it collapses until it hits the target.

Two details we handle:
- **Triangulate first.** Decimation reasons in triangles, so we convert quads/n-gons to
  triangles before computing the ratio (otherwise the budget is off).
- The ratio only *approximates* a target count, so we nudge it within a tolerance band.

In the tool, the **Geometry slider** maps to a triangle target (a few triangles → near the
original), and decimate hits it.

> **Mental model:** decimation is "round off the shape with fewer, bigger flat panels." A
> decimated sphere becomes a faceted gem. That faceting *is* the lo-fi geometry look.

### 2.3 The heart of it: baking (transferring the surface)

Here's the problem. We now have a **low-poly** duck (few triangles) but its surface is blank —
all the colour detail lived on the **high-poly** duck. We need to *photograph the high-poly's
surface and paste it onto the low-poly's surface.* That process is **baking**.

To bake colour onto a surface, you first need a way to address points on that surface in 2D —
that's **UV mapping**.

#### UV mapping, unwrapping, islands, the atlas

A texture is a flat 2D image, but a duck is a curved 3D thing. **UV coordinates** are the
bridge: every vertex gets a 2D coordinate `(u, v)` saying "this point on the mesh corresponds
to *this* spot on the texture image." (They're called U and V just to avoid clashing with X/Y/Z.)

**Unwrapping** is the act of assigning those coordinates — conceptually, *cutting the 3D
surface along some seams and flattening it out into 2D*, like peeling an orange and pressing the
peel flat, or laying out a sewing pattern. We use **Smart UV Project**, which auto-cuts the
mesh into flattenable chunks.

- Each flattened chunk is a **UV island**.
- The islands get **packed** together into the square texture image — that packed image is a
  **texture atlas**.

So the low-poly duck gets a *fresh* UV unwrap: its surface chopped into islands, packed into a
small atlas, ready to receive paint.

#### Selected-to-active "hi→lo" baking + the cage

Now the actual transfer. We keep **both** ducks in the scene:

- the **high-poly** (full detail, still wearing the original texture),
- the **low-poly** (blank, with its fresh atlas).

We tell the renderer: *for every pixel in the low-poly's atlas, figure out which point of the
low-poly surface it maps to, shoot a short ray from there, find the nearest point on the
**high-poly** surface, and copy that point's colour into the pixel.* This is a
**selected-to-active bake** ("selected" = the high-poly source, "active" = the low-poly target).

The short ray needs a search distance — the **cage** / **cage extrusion**. Picture inflating
the low-poly slightly into a "cage" around the high-poly; rays travel inward from the cage to
find the surface. Too small and rays miss (black spots); too big and they hit the wrong surface
(e.g. a belly ray reaching the beak). We size it relative to the normalized object so it's
consistent.

We use an **EMIT bake** specifically: it copies the surface's *emission* (raw colour) with no
lighting math — exactly the flat colour transfer we want.

> **Why two meshes instead of just downscaling the texture?** Because the low-poly's UV layout
> is *different* from the high-poly's. Sampling the true high-poly surface through fresh,
> undistorted low-poly UVs gives clean colour placement. (An earlier version baked the texture
> "through" the decimated mesh and the texture visibly *followed the triangles* — a lesson that
> recurs in §4.)

### 2.4 De-lighting — recovering the "true colour"

A photo (and therefore a scan's texture) has **lighting baked into it**: the shadowed side of
the duck is darker *in the texture itself*. That's fine for a static image, but it's wrong for a
**game asset**, because the game engine will *also* light it — so shadows get applied twice, and
the model looks dirty and can't be re-lit.

**De-lighting** (a.k.a. **albedo recovery**) removes that baked-in lighting to recover the
**albedo** — the surface's *intrinsic* colour, as if lit perfectly evenly. ("Albedo" /
"base colour" = the true paint colour, no shadows.)

It's an **ill-posed** problem (you can't perfectly separate "this is dark because it's in
shadow" from "this is dark because it's painted dark"), so we approximate with two ideas:

- **Retinex theory:** *shading varies slowly across the surface; real colour/detail varies
  sharply.* So if you blur the image heavily you get an estimate of the lighting, and dividing
  it back out flattens the lighting while keeping the sharp colour edges. (We do this in
  brightness only, so colours don't shift.)
- **Ambient Occlusion (AO) division:** AO is how *shadowed-by-its-own-geometry* each spot is
  (crevices are dark, exposed bumps are bright). Dividing the texture by the AO lifts those
  crevice shadows.

We also made it **auto-adaptive**: it measures how much baked shading actually exists and only
de-lights as much as needed — a clean source (like a rubber duck's authored texture) is left
alone; a raw photogrammetry scan with strong baked shadows gets the full treatment.

### 2.5 Cartoonize / "flattening" — the lo-fi look

A plain shrink of a photo just makes a blurry photo. To get the **cartoon / lo-fi** look we
*abstract* the surface into flat regions and punch up the colour. "Flattening" shows up in a
few forms:

- **Guided-filter smoothing (abstraction).** A **guided filter** is an *edge-preserving blur*:
  it smooths away fine noise *inside* a region but keeps the hard boundaries *between* regions.
  Run it and a noisy photographed cheek becomes a single flat "cartoon cell," while the edge
  between cheek and beak stays crisp. (This is image-abstraction à la Winnemöller 2006.)
- **Region flatten.** Same idea, dialled up: merge a whole shaded-vs-lit surface into one flat
  colour ("skin → one colour"), so it reads as cartoon, not photo.
- **Posterize.** Reduce the number of brightness *steps* (like a poster print) — smooth
  gradients become a few flat bands. We posterize *hue-preservingly* (quantize brightness only)
  so neutral greys don't band into false colours.

### 2.6 Palettize (quantize) — few colours

Real PS1/lo-fi art uses a tiny palette. **Colour quantization** reduces the image to **N
colours** (e.g. 32). We use **median-cut**: repeatedly take the box of colours with the widest
spread and split it at the median, until you have N boxes; each box's average is a palette
entry. Then every pixel is snapped to its **nearest** palette colour. (Snapping to *nearest*
rather than the box average matters — it's what stops a belly-orange pixel from grabbing the
beak's red; see §4.)

### 2.7 Supersample + DPID downscale — keep detail when shrinking the texture

If you bake straight to 64×64 you get aliasing (jaggies, lost detail). So we **supersample**:
bake at, say, 4× the target (256×256) and then shrink to 64×64. A naive shrink *averages* and
blurs detail away; we use **DPID** (*Detail-Preserving Image Downscaling*, Weber 2016), which
*weights* pixels that deviate from their neighbourhood more heavily, so important detail
survives the size cut.

### 2.8 The material — lit PBR + nearest filtering

Finally we wrap the finished texture in a **material**: the rules for how the surface reacts to
light.

- **PBR** = *Physically-Based Rendering* — the modern standard where a material is described by
  a **base colour** (our de-lit texture), **roughness** (matte vs shiny), **metallic**, etc.,
  fed into a **Principled BSDF** shader. Because it's PBR/**lit**, the engine's lights produce
  the shadows at runtime — which is the whole point of de-lighting.
- **Unlit** (the alternative, `KHR_materials_unlit` in glTF) means "show the texture exactly,
  ignore lights." We started there (a true "PS1 unlit" look) and deliberately switched to
  **lit** so the asset drops into a normally-lit game.
- **Nearest-neighbour filtering** (`magFilter = 9728`): when the small texture is shown
  enlarged, "nearest" picks the single closest texel instead of blending — giving **crisp pixel
  edges** instead of a blurry smear. That hard-pixel look is essential to "lo-fi."

### 2.9 Export — glTF / GLB

We write a **`.glb`** — the binary form of **glTF**, the standard "JPEG of 3D" interchange
format. It packs the mesh, the UVs (called **TEXCOORD** sets in glТF), the embedded texture
image, and the material into one file a game engine can import. After writing it we **re-parse
the file and assert** the things a render can't show (is it really lit? is the sampler really
nearest? right dimensions? tri count in budget?) — because those are easy to get silently wrong
(and we did — §4).

---

## 3. Resolution: making "lo-fi" a dial

"Lo-fi" isn't one setting — it's *two*, and they're independent:

- **Geometry resolution** → triangle budget → how aggressively `decimate` runs.
- **Material resolution** → texture size + palette colours → how small/crunchy the texture is.

We made each a **slider from 0 (a few triangles / a tiny texture) to 1 (≈ the original)**,
scaled **relative to each source's own resolution** (so the same setting adapts whether the
source is 4k tris or 800k). The mapping is **logarithmic** so the slider feels even across that
huge range. Presets just quick-set the sliders; an Advanced mode lets you type exact numbers.

This is the cleanest expression of the **mesh-vs-material** split: two dials, no coupling — a
lo-fi body with a hi-fi paint job, or vice versa.

---

## 4. The hard lessons (bugs that taught us the most)

The most instructive moments were the bugs. Each is a real principle.

- **"Don't do spatial work in the atlas."** The texture atlas is the mesh *chopped into islands
  and shuffled*. Two islands sitting next to each other in the atlas can be on opposite sides of
  the duck. So any operation that *blurs or spreads across pixels* (de-light, region-flatten) —
  if done on the atlas — bleeds colour across island seams onto unrelated parts of the model.
  The fix: do all spatial/blur work in the **coherent source texture** (where neighbours really
  are neighbours), *before* baking; keep the post-bake steps strictly per-pixel. (This is the
  same lesson as "the texture followed the triangles" — operate in source space, not mesh/atlas
  space.)

- **The scrambled duck: the UV-layer export bug.** The low-poly briefly carried **two** UV
  layouts — its original one and the new baked one. The texture was baked into the *new* layout,
  but the glTF exporter assigns the *first* UV set as `TEXCOORD_0`, and the material samples
  `TEXCOORD_0` — so the exported material read the baked texture through the *wrong* coordinates.
  Result: eyes and beak smeared onto the wrong places. It was invisible on uniform statues
  (any coordinates map stone to stone) and glaring on the duck. Fix: drop the stale UV layer so
  the baked one is the only one. **Lesson:** "looks fine on a plain object" doesn't prove the
  UV/export path is correct — test with a feature-rich object.

- **The cap-colour bug.** Watertight's invented hole-caps were given a colour by *averaging UV
  coordinates*, which is meaningless across separate islands and happened to land on the duck's
  red bill — bleeding red onto the belly. Fix: pick the colour from the *densest* part of the
  UV layout (the body), not a coordinate average.

- **De-lighting is ill-posed, so be gentle.** Cranking de-light on an already-clean texture just
  amplifies noise into blotches. Making it *measure* how much shading exists and adapt — rather
  than always applying full strength — was the fix.

- **The output-path footgun.** The default save path (`//file.glb`, meaning "next to the .blend")
  can't resolve when the .blend is unsaved, so the export failed *after* a successful conversion,
  and the pipeline deleted everything on failure → "I ran it and nothing happened." Fix:
  auto-derive a writable path from the source object, and make file export optional entirely.

---

## 5. The methods, named (for further reading)

If you want to go deeper, these are the actual named techniques we lean on. Search any of them.

| What we call it | The real technique / source |
|---|---|
| Decimate | **Edge-collapse mesh simplification** (quadric error metrics, Garland & Heckbert 1997) |
| Hi→lo baking | **Selected-to-active texture baking / surface transfer**, with a **cage** |
| UV unwrap | **UV mapping / mesh parameterization**; Blender **Smart UV Project** |
| Abstraction / flatten | **Guided filter** (He et al.); **image abstraction** (Winnemöller et al. 2006) |
| (earlier) ink outlines | **XDoG** — eXtended Difference-of-Gaussians (Winnemöller 2011) |
| Palettize | **Median-cut colour quantization** (Heckbert 1982) + nearest-colour mapping |
| Shrink-but-keep-detail | **DPID — Detail-Preserving Image Downscaling** (Weber et al. 2016) |
| De-light | **Intrinsic image decomposition / albedo recovery**; **Retinex theory** (Land & McCann); **Ambient Occlusion** |
| Material model | **PBR** (Physically-Based Rendering) + **Principled BSDF**; glTF **`KHR_materials_unlit`** |
| Crisp pixels | **Nearest-neighbour texture filtering** (`magFilter` 9728) |
| File format | **glTF / GLB** (Khronos); **TEXCOORD** UV sets, **samplers** |

---

## Glossary

- **Albedo / base colour** — a surface's intrinsic colour with no lighting/shadows baked in.
- **Atlas (texture atlas)** — one image holding all of a mesh's UV islands packed together.
- **Bake** — render surface information (colour, occlusion, …) into a texture image.
- **BSDF / Principled BSDF** — the shader describing how a PBR surface reflects light.
- **Cage / cage extrusion** — the search envelope for hi→lo bake rays.
- **Connected component** — a clump of geometry all joined together; we keep the largest.
- **Decimate** — reduce triangle count via edge collapse, preserving the silhouette.
- **De-light** — remove baked-in lighting to recover albedo (so the engine can re-light it).
- **DPID** — detail-preserving image downscaling.
- **EMIT bake** — a bake that copies raw surface colour with no lighting math.
- **Guided filter** — an edge-preserving blur (smooths regions, keeps boundaries).
- **glTF / GLB** — the standard 3D interchange format (GLB = its single-file binary form).
- **Lit vs Unlit** — lit reacts to scene lights (PBR); unlit shows the texture as-is.
- **Material** — the surface look: texture(s) + how they react to light. *Not* the shape.
- **Median-cut** — a colour-quantization method that repeatedly splits the widest colour box.
- **Mesh** — the shape: vertices/edges/faces (triangles). *Not* the surface colour.
- **Nearest filtering** — show a texture with hard pixel edges (no blending).
- **Normalize** — centre + scale a mesh to a known size for scale-invariant processing.
- **PBR** — physically-based rendering; the modern base-colour/roughness/metallic material model.
- **Posterize** — reduce an image to a few flat tone bands.
- **Quantize (palettize)** — reduce an image to N colours.
- **Retinex** — the assumption that shading is low-frequency and reflectance is high-frequency.
- **Supersample** — render bigger than needed, then downscale, to reduce aliasing.
- **Triangle budget / poly count** — how many triangles the mesh is allowed.
- **UV mapping / unwrap** — assigning 2D texture coordinates by flattening the 3D surface.
- **UV island** — one connected flattened patch of the unwrapped surface.
- **Vertex / edge / face** — a point / a line between points / a polygon bounded by edges.
- **Watertight** — a closed mesh with no holes (open boundaries capped).
