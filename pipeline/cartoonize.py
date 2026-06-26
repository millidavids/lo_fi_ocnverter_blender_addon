"""Cartoonize a baked albedo: abstract + amplify, instead of wash-out.

A plain bake+downscale is a low-pass filter -- it averages away the detail that
carries an object's identity, leaving a blurred blob. This stage does the
opposite: it ABSTRACTS surfaces into flat cartoon regions while AMPLIFYING the
identity-defining signals (edges, contrast, saturation, structural shading), and
finishes with a DETAIL-PRESERVING downscale so the result stays crisp.

Recipe (research-backed):
  guided-smooth (abstract)  -> Winnemoller 2006 image abstraction
  saturation + contrast     -> punchy cartoon colour
  posterize (modest)        -> tone steps
  XDoG ink edges            -> Winnemoller 2012 (DoG + tanh soft-threshold)
  cavity/AO shading         -> features read on low-poly
  DPID downscale            -> Weber 2016 detail-preserving downscaling

All maths is pure numpy and operates DIRECTLY on the bake's pixel values (which
come back sRGB-encoded -- perceptually even, exactly what posterize/saturation
want; no encode/decode round-trip). The numpy functions have NO Blender
dependency and are unit-tested in tests/test_cartoonize.py; only `run` touches bpy.
"""

import numpy as np

# Shared colour-space maths (OKLab/OKLCh). Normally a package import; when this file is
# exec_module'd by path in the standalone unit tests there's no package, so fall back to a
# direct path load (registered in sys.modules so cartoonize + pixelate share one instance).
try:
    from . import colour
except ImportError:
    import importlib.util as _il, os as _os, sys as _sys
    _cp = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "colour.py")
    _cs = _il.spec_from_file_location("lofi_colour", _cp)
    colour = _il.module_from_spec(_cs)
    _sys.modules["lofi_colour"] = colour
    _cs.loader.exec_module(colour)

_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)

# sRGB <-> linear live canonically in colour.py now; keep the private names as aliases so
# `delight` (and anything else here) keeps working without duplicating the transfer curve.
_srgb_to_linear = colour.srgb_to_linear
_linear_to_srgb = colour.linear_to_srgb


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
def _gauss_kernel(sigma):
    radius = max(1, int(round(3.0 * sigma)))
    x = np.arange(-radius, radius + 1, dtype=np.float32)
    k = np.exp(-(x * x) / (2.0 * sigma * sigma))
    return (k / k.sum()).astype(np.float32)


def _conv1d(a, k, axis):
    r = len(k) // 2
    a = np.moveaxis(a, axis, -1)
    pad = np.pad(a, [(0, 0)] * (a.ndim - 1) + [(r, r)], mode="reflect")
    out = np.zeros_like(a)
    n = a.shape[-1]
    for i, kv in enumerate(k):
        out += kv * pad[..., i:i + n]
    return np.moveaxis(out, -1, axis)


def gaussian_blur(img, sigma):
    """Separable Gaussian blur of a 2-D or 3-D (H,W[,C]) array."""
    if sigma <= 0:
        return img.astype(np.float32, copy=True)
    k = _gauss_kernel(sigma)
    return _conv1d(_conv1d(img.astype(np.float32), k, 0), k, 1)


def guided_smooth(rgb, sigma=2.0, eps=0.01, iters=1):
    """Edge-preserving flatten (self-guided guided filter, Gaussian-mean variant).

    In flat regions a->0 so the output goes to the local mean (smoothed into
    cartoon cells); across edges a->1 so detail is preserved.
    """
    out = rgb.astype(np.float32, copy=True)
    for _ in range(max(1, iters)):
        chans = []
        for c in range(out.shape[2]):
            I = out[:, :, c]
            mean_i = gaussian_blur(I, sigma)
            var = gaussian_blur(I * I, sigma) - mean_i * mean_i
            a = var / (var + eps)
            b = mean_i - a * mean_i
            chans.append(gaussian_blur(a, sigma) * I + gaussian_blur(b, sigma))
        out = np.stack(chans, axis=2)
    return np.clip(out, 0.0, 1.0)


def _psf2otf(psf, shape):
    """Optical-transfer function of a small PSF for an FFT-domain solve: place the PSF,
    circularly shift its centre to (0,0), then fft2 (matches MATLAB psf2otf)."""
    psf = np.asarray(psf, dtype=np.float64)
    ph, pw = psf.shape
    out = np.zeros(shape, dtype=np.float64)
    out[:ph, :pw] = psf
    out = np.roll(out, -(ph // 2), axis=0)
    out = np.roll(out, -(pw // 2), axis=1)
    return np.fft.fft2(out)


def l0_smooth(rgb, lam, kappa=2.0, beta_max=1e5, pad=8):
    """L0 gradient-minimization smoothing (Xu et al. 2011): drives small gradients to
    EXACTLY zero, so regions go genuinely FLAT with crisp step edges -- the cartoon
    "flatten" a guided filter (which only softens gradients) can't give.

    Solved by half-quadratic splitting in the Fourier domain: alternate a hard-threshold
    on the gradients (keep an edge only where the joint cross-channel gradient energy
    exceeds lam/beta) with a least-squares image update; beta climbs *kappa each pass until
    beta_max (~8-15 passes). `lam` is the smoothing strength (larger = flatter/fewer edges).

    The FFT solve is periodic; we reflect-PAD by `pad` first and crop after so wrap-around
    can't bleed an opposite edge into a real border. Pure numpy. Returns float32 in [0,1].
    Run this ONLY in coherent source space (pre-bake) -- it's a strong spatial op."""
    S = np.pad(rgb.astype(np.float64), ((pad, pad), (pad, pad), (0, 0)), mode="reflect")
    H, W, _ = S.shape
    otfx = _psf2otf([[1, -1]], (H, W))
    otfy = _psf2otf([[1], [-1]], (H, W))
    Normin1 = np.fft.fft2(S, axes=(0, 1))
    Den2 = (np.abs(otfx) ** 2 + np.abs(otfy) ** 2)[:, :, None]
    beta = 2.0 * lam
    while beta < beta_max:
        Den = 1.0 + beta * Den2
        # gradients with circular boundary (consistent with the periodic FFT solve)
        h = np.concatenate([np.diff(S, axis=1), S[:, :1, :] - S[:, -1:, :]], axis=1)
        v = np.concatenate([np.diff(S, axis=0), S[:1, :, :] - S[-1:, :, :]], axis=0)
        kill = (h ** 2 + v ** 2).sum(axis=2) < lam / beta     # joint across channels
        h[kill] = 0.0
        v[kill] = 0.0
        n2 = np.concatenate([h[:, -1:, :] - h[:, :1, :], -np.diff(h, axis=1)], axis=1)
        n2 += np.concatenate([v[-1:, :, :] - v[:1, :, :], -np.diff(v, axis=0)], axis=0)
        FS = (Normin1 + beta * np.fft.fft2(n2, axes=(0, 1))) / Den
        S = np.real(np.fft.ifft2(FS, axes=(0, 1)))
        beta *= kappa
    return np.clip(S[pad:-pad, pad:-pad], 0.0, 1.0).astype(np.float32)


def _l0_lambda(strength):
    """Map a 0..1 `l0_strength` slider to an L0 lambda. 0 -> None (skip entirely)."""
    s = float(strength)
    if s <= 0.0:
        return None
    return 0.002 + 0.04 * s * s          # gentle->strong flatten


def boost_saturation_contrast(rgb, sat=1.4, contrast=1.25):
    """Punch cartoon colour PERCEPTUALLY, in OKLCh: scale chroma for saturation and scale
    lightness about a perceptual mid for contrast, then pull anything that overshoots the
    sRGB gamut back by reducing chroma (hue preserved). Working in OKLCh (not sRGB-luma)
    keeps the perceived HUE fixed -- no blue->purple drift, no false reds from a luma boost
    -- and the gamut clip avoids the per-channel clamping that would distort hue."""
    lch = colour.srgb_to_oklch(rgb)
    L, C, h = lch[..., 0], lch[..., 1], lch[..., 2]
    C = C * sat
    pivot = float(L.mean()) if L.size else colour.MID_GREY_L   # adaptive; never 0.5
    L = (L - pivot) * contrast + pivot
    graded = colour.gamut_clip_oklch(np.stack([L, C, h], axis=-1))
    return np.clip(colour.oklch_to_srgb(graded), 0.0, 1.0)


def posterize(rgb, levels):
    levels = max(2, int(levels))
    return np.round(rgb * (levels - 1)) / (levels - 1)


def posterize_value(rgb, levels):
    """Hue-preserving posterize: quantize BRIGHTNESS only and scale RGB to match,
    so near-grey pixels can't band into different per-channel levels (the artifact
    that turns monochrome stone into coloured blotches)."""
    levels = max(2, int(levels))
    luma = (rgb * _LUMA).sum(axis=2, keepdims=True)
    q = np.round(luma * (levels - 1)) / (levels - 1)
    return np.clip(rgb * (q / np.maximum(luma, 1e-4)), 0.0, 1.0)


def xdog_edges(luma, sigma=1.0, k=1.6, tau=0.98, eps=0.0, phi=12.0):
    """eXtended Difference-of-Gaussians ink map in [0,1] (1=keep, <1=dark line).

    DoG = G_sigma - tau*G_{k*sigma}; soft-thresholded with tanh. Run this on the
    ABSTRACTED luminance (post guided-smooth) so it inks real feature boundaries,
    not photogrammetry noise.
    """
    dog = gaussian_blur(luma, sigma) - tau * gaussian_blur(luma, sigma * k)
    ink = np.where(dog >= eps, 1.0, 1.0 + np.tanh(phi * (dog - eps)))
    return np.clip(ink, 0.0, 1.0).astype(np.float32)


def dpid_downscale(img, factor, lam=1.0):
    """Detail-preserving downscale (Weber 2016): source pixels that deviate more
    from their block's mean get MORE weight, so detail survives instead of blurring.
    `factor` must be an integer; H,W are cropped to a multiple of it."""
    factor = int(factor)
    if factor <= 1:
        return img.astype(np.float32, copy=True)
    h, w, c = img.shape
    th, tw = h // factor, w // factor
    img = img[:th * factor, :tw * factor]
    blocks = img.reshape(th, factor, tw, factor, c).astype(np.float32)
    bmean = blocks.mean(axis=(1, 3), keepdims=True)
    dev = np.sqrt(((blocks[..., :3] - bmean[..., :3]) ** 2).sum(axis=-1, keepdims=True))
    wgt = np.power(dev, lam) + 1e-6
    out = (blocks * wgt).sum(axis=(1, 3)) / wgt.sum(axis=(1, 3))
    return out


# --------------------------------------------------------------------------- #
# orchestration (pure numpy)
# --------------------------------------------------------------------------- #
def _normalize(gray, lo_p=3.0, hi_p=97.0):
    """Percentile contrast-stretch a [0,1] map. The baked cavity is often crushed
    near-flat (e.g. a serene face's shallow relief reads 0.80-1.0); stretching it
    makes the captured features (eye sockets, nose, mouth, curls) usable."""
    a = gray.astype(np.float32)
    lo, hi = np.percentile(a, lo_p), np.percentile(a, hi_p)
    return np.clip((a - lo) / max(1e-4, hi - lo), 0.0, 1.0)


def colourfulness(rgb):
    """Robust central chroma of the image in [0,1] (median of max-min per pixel)."""
    return float(np.median(rgb.max(axis=2) - rgb.min(axis=2)))


def cartoonize_structure(rgb, params):
    """Coherent cartoon ABSTRACTION in the SOURCE space (pre-bake): edge-preserving
    guided smoothing into flat cartoon cells, decoupled from the mesh triangles.

    Iteration 6: NO posterize and NO XDoG ink here. Tone-quantization moved post-bake
    into `grade_finish` (so it acts on the DE-LIT albedo), and ink is dropped entirely
    — baked-in dark lines can't be relit and fight the de-light goal.

    `region_flatten` adds extra smoothing passes HERE (coherent source space) rather
    than post-bake: merging a surface into flat regions in the source layout can't bleed
    colour across the chopped/packed low-poly atlas seams."""
    p = params
    extra = int(round(p.get("region_flatten", 0.0) * 2.0))   # merge regions in coherent space
    return np.clip(guided_smooth(rgb, sigma=p["smooth_sigma"], eps=p["smooth_eps"],
                                 iters=p["smooth_iters"] + extra), 0.0, 1.0)


def delight(rgb, ao, params):
    """Remove baked shading to recover a flat, intrinsic base colour for a LIT material.

    CRITICAL: run this in the COHERENT SOURCE space (pre-bake), NEVER on the chopped/
    packed low-poly atlas. De-light is a per-pixel brightness correction; if its
    estimate is imperfect at a UV-island seam (and it always is), the atlas version
    bleeds that error across seams and splatters brightness mottling onto unrelated
    parts of the mesh. In source space the correction follows the real surface layout.

    `ao` is optional (AO-divide lifts occlusion-correlated shadows); the Retinex luma
    low-pass flatten runs regardless and is what attacks broad capture lighting. All in
    LINEAR space (decode sRGB -> divide -> re-encode). `delight_strength` blends back."""
    p = params
    strength = float(p.get("delight_strength", 0.8))
    if strength <= 0.0:
        return rgb

    lin = _srgb_to_linear(rgb)

    # Shading estimate: a large Gaussian low-pass of luminance (Retinex assumes shading is
    # low-frequency). AUTO-ADAPT first: de-lighting is ill-posed, and an already-flat
    # source (e.g. a Poly Haven diffuse, authored as clean albedo: shading_amt ~0.1) has
    # little low-frequency luma variation, so de-lighting it only normalizes real albedo
    # texture into mottle. A raw photogrammetry scan (statues ~0.4-0.8) has strong baked
    # shading. Scale by how much broad shading exists (spread of the log estimate).
    luma = (lin * _LUMA).sum(axis=2)
    shading = np.maximum(gaussian_blur(luma, sigma=p.get("retinex_sigma", 12.0)), 1e-3)
    shading = shading / float(shading.mean())
    shading_amt = float(np.std(np.log(shading)))
    auto = float(np.clip((shading_amt - 0.15) / (0.45 - 0.15), 0.0, 1.0))
    print(f"lofi.delight: shading_amt={shading_amt:.3f} auto={auto:.2f}")
    if ao is None and auto < 0.03:
        return rgb                        # already-clean source -> true no-op

    if ao is not None:                    # AO-divide (mean-1, floored so AO~=0 can't explode)
        ao_lin = np.maximum(_srgb_to_linear(ao), 0.2)
        lin = lin / (ao_lin / float(ao_lin.mean()))[:, :, None]
    # Dividing the shading out flattens broad gradients; sharp albedo edges (in the
    # numerator) survive. `shading ** auto` scales the correction: ~no-op on flat sources,
    # full on shaded ones. In source space the few large islands keep cross-seam bleed tiny.
    lin = lin / (shading ** auto)[:, :, None]

    lin = lin * (0.5 / max(1e-3, float((lin * _LUMA).sum(axis=2).mean())))   # balance mid-tone
    out = _linear_to_srgb(np.clip(lin, 0.0, 1.0))
    return np.clip(rgb * (1.0 - strength) + out * strength, 0.0, 1.0)


def grade_finish(rgb, params, ao=None, cavity=None):
    """Post-bake GRADE on the hole-filled atlas, for a game-ready LIT asset:
    chroma-adaptive colour punch -> tone steps -> detail-preserving downscale.

    PER-PIXEL / block-local ops ONLY. No de-light, no spatial blur, no shading here —
    the atlas is a chopped/packed UV layout, so anything spatial bleeds colour across
    island seams (the iter-3 lesson). De-light + region-flatten happen pre-bake in the
    coherent SOURCE space (`cartoonize_source_copy`). `ao`/`cavity` are unused (kept for
    signature compatibility)."""
    p = params
    cf = float(np.clip((colourfulness(rgb) - 0.04) / (0.18 - 0.04), 0.0, 1.0))
    # Saturation curve over colourfulness cf:
    #   mono (cf~0)         -> mono_saturation (desaturate: kill amplified false tints)
    #   muted-colour (~0.5) -> peak boost (punchy cartoon colour)
    #   already-vivid (~1)  -> ~1.0 (NEUTRAL): boosting a vivid, warm subject pushes tones
    #                          into false reds the palette then snaps into blotches.
    t = min(cf / 0.45, 1.0)
    ss = t * t * (3.0 - 2.0 * t)                              # smoothstep 0 -> 1 by cf=0.45
    boost = (p["saturation"] - 1.0) * 4.0 * cf * (1.0 - cf)   # hump: 0 at cf 0/1, peak at 0.5
    eff_sat = p["mono_saturation"] + (1.0 - p["mono_saturation"]) * ss + boost
    print(f"lofi.cartoonize: chroma={colourfulness(rgb):.3f} cf={cf:.2f} eff_sat={eff_sat:.2f}")

    rgb = boost_saturation_contrast(rgb, sat=eff_sat, contrast=p["contrast"])
    if p["posterize_levels"]:
        rgb = posterize_value(rgb, p["posterize_levels"])     # hue-preserving tone steps

    rgb = np.clip(rgb, 0.0, 1.0)
    return dpid_downscale(rgb, p["supersample"], lam=p["dpid_lambda"])


def stylize(rgb, params, ao=None, cavity=None):
    """Full chain (structure + grade) on one array — used for vertex/solid sources,
    which have no coherent source texture to pre-cartoonize (NOT decoupled)."""
    return grade_finish(cartoonize_structure(rgb, params), params, ao=ao, cavity=cavity)


# --------------------------------------------------------------------------- #
# Blender wrapper
# --------------------------------------------------------------------------- #
def _read_rgba(image):
    w, h = image.size
    flat = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    return flat.reshape(h, w, 4)


def _read_gray(image):
    return _read_rgba(image)[:, :, 0] if image is not None else None


def params_from_settings(settings):
    g = lambda n, d: getattr(settings, n, d)
    return {
        "supersample": max(1, int(g("supersample", 4))),
        "smooth_sigma": g("smooth_sigma", 2.0),
        "smooth_eps": g("smooth_eps", 0.02),
        "smooth_iters": int(g("smooth_iters", 2)),
        "saturation": g("saturation", 1.4),
        "contrast": g("contrast", 1.25),
        "posterize_levels": int(g("posterize_levels", 6)),
        "dpid_lambda": g("dpid_lambda", 1.0),
        # de-light (iter-6): strip baked shading for a lit-material base colour.
        "delight_strength": g("delight_strength", 0.8),
        "retinex_sigma": g("retinex_sigma", 12.0),
        "region_flatten": g("region_flatten", 0.5),
        # L0 flatten (iter-7): genuinely-flat cartoon cells, source space only. 0 = off.
        "l0_strength": g("l0_strength", 0.0),
        # chroma-adaptive: monochrome subjects desaturate (kill false colour from
        # amplifying faint tints); colourful subjects get the full punch.
        "mono_saturation": g("mono_saturation", 0.6),
        "source_res": int(g("source_res", 1024)),
    }


def cartoonize_source_copy(image, params, temp):
    """Lo-fi the MATERIAL in its own coherent space, decoupled from the mesh.

    COPY the source image (never mutate the user's scan), sever its on-disk link so a
    stray reload/save-modified can't clobber the original, scale the copy down for
    speed+coherence, apply the cartoon STRUCTURE, and return it. The bake then samples
    this coherent cartoon through the low-poly UVs (cells come from the image, not the
    triangles)."""
    copy = image.copy()
    copy.name = "lofi_src_" + image.name
    # Sever the on-disk link ONLY for unpacked file-backed copies, so a stray
    # reload/save can't clobber the user's texture. Packed/generated images have no
    # external file (severing them just warns), so leave them alone.
    if copy.source == "FILE" and copy.packed_file is None:
        copy.filepath_raw = ""
    if temp is not None:
        temp.images.append(copy)
    res = params["source_res"]
    w, h = copy.size
    if max(w, h) > res:
        copy.scale(res, res)
    a = _read_rgba(copy)
    # ABSTRACT first (guided smoothing denoises into flat cells), optionally FLATTEN hard
    # with L0 (drives small gradients to zero -> genuinely flat cartoon cells with crisp
    # edges), THEN de-light the smooth result. Order matters: de-lighting raw photo texture
    # divides by a luma low-pass and amplifies fine texture noise in darker areas into mottle
    # (which the palette then snaps into colour blotches); de-lighting the already-flattened
    # albedo has no noise to amplify. All run in the COHERENT source space, pre-bake, so
    # nothing bleeds across atlas seams (L0 especially must never touch the packed atlas).
    rgb = cartoonize_structure(a[:, :, :3], params)
    lam = _l0_lambda(params.get("l0_strength", 0.0))
    if lam is not None:
        rgb = l0_smooth(rgb, lam)
        print(f"lofi.cartoonize: L0 flatten lam={lam:.4f} (strength {params['l0_strength']})")
    a[:, :, :3] = delight(rgb, None, params)
    copy.pixels.foreach_set(a.ravel())
    copy.update()
    print(f"lofi.cartoonize: source '{image.name}' -> de-lit coherent cartoon {copy.size[0]}px")
    return copy


def run(albedo_img, settings, out_size, ao_img=None, cavity_img=None, temp=None,
        structure_done=False):
    """Finish the supersampled `albedo_img` into a NEW `out_size` image (pixelate.run
    reads image.size, so the downscaled result must be its own target-size datablock).

    `structure_done=True` (image source already cartoonized coherently in bake): apply
    GRADE only. Otherwise (vertex/solid baked raw): full structure + grade. Returns the
    new image."""
    import bpy

    p = params_from_settings(settings)
    p["supersample"] = max(1, albedo_img.size[0] // out_size)   # actual integer factor

    rgb = _read_rgba(albedo_img)[:, :, :3]
    ao, cavity = _read_gray(ao_img), _read_gray(cavity_img)
    small = (grade_finish(rgb, p, ao=ao, cavity=cavity) if structure_done
             else stylize(rgb, p, ao=ao, cavity=cavity))

    out = bpy.data.images.new("lofi_cartoon", out_size, out_size, alpha=True)
    rgba = np.ones((out_size, out_size, 4), dtype=np.float32)
    rgba[:, :small.shape[1], :3] = small[:out_size, :out_size, :3]
    out.pixels.foreach_set(rgba.ravel())
    out.update()
    print(f"lofi.cartoonize: {albedo_img.size[0]}px ->{out_size}px "
          f"(ss x{p['supersample']}, delight {p['delight_strength']}, "
          f"posterize {p['posterize_levels']}, sat {p['saturation']})")
    return out
