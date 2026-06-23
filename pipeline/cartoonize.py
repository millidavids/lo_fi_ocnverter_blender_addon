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

_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _srgb_to_linear(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def _linear_to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92,
                    1.055 * (c ** (1.0 / 2.4)) - 0.055).astype(np.float32)


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


def boost_saturation_contrast(rgb, sat=1.4, contrast=1.25):
    luma = (rgb * _LUMA).sum(axis=2, keepdims=True)
    out = luma + (rgb - luma) * sat          # push away from grey
    out = (out - 0.5) * contrast + 0.5        # contrast about mid
    return np.clip(out, 0.0, 1.0)


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
    — baked-in dark lines can't be relit and fight the de-light goal."""
    p = params
    return np.clip(guided_smooth(rgb, sigma=p["smooth_sigma"], eps=p["smooth_eps"],
                                 iters=p["smooth_iters"]), 0.0, 1.0)


def delight(rgb, ao, params):
    """Remove baked shading from the albedo to recover a flat, intrinsic base colour
    for a LIT material (the engine relights it). NO-OP when `ao is None` (vertex/solid
    source path has nothing to de-light).

    Two divides, both in LINEAR space (the bake target is 8-bit sRGB, so the albedo AND
    the AO read back sRGB-ENCODED — decode both first):
      1. AO-divide: divide by the geometry's ambient occlusion (mean-1 normalized, floored)
         to lift occlusion-correlated shadows (eye sockets, crevices). Approximation, not
         an inverse — won't touch directional capture shadows.
      2. Retinex low-freq flatten: divide out an edge-preserving low-pass of luminance
         (mean-1 normalized) to flatten broad directional shading while keeping albedo
         edges. This is what actually attacks the capture lighting.
    `delight_strength` blends the result back toward the original."""
    if ao is None:
        return rgb
    p = params
    strength = float(p.get("delight_strength", 0.8))
    if strength <= 0.0:
        return rgb

    lin = _srgb_to_linear(rgb)

    # 1. AO-divide (mean-1, floored so AO~=0 doesn't explode)
    ao_lin = np.maximum(_srgb_to_linear(ao), 0.2)
    ao_div = ao_lin / float(ao_lin.mean())
    lin = lin / ao_div[:, :, None]

    # 2. Retinex: a large Gaussian low-pass of luminance is the shading estimate
    # (Retinex assumes shading is low-frequency). A Gaussian *tracks* smooth gradients
    # (so dividing removes them) while sharp albedo edges, living in the numerator,
    # survive. May leave mild halos at strong shadow edges — accepted, tunable by sigma.
    luma = (lin * _LUMA).sum(axis=2)
    shading = gaussian_blur(luma, sigma=p.get("retinex_sigma", 12.0))
    shading = np.maximum(shading, 1e-3)
    shading = shading / float(shading.mean())
    lin = lin / shading[:, :, None]

    # balance to a sane mid-tone and clamp, then re-encode
    lin = lin * (0.5 / max(1e-3, float((lin * _LUMA).sum(axis=2).mean())))
    out = _linear_to_srgb(np.clip(lin, 0.0, 1.0))
    return np.clip(rgb * (1.0 - strength) + out * strength, 0.0, 1.0)


def grade_finish(rgb, params, ao=None, cavity=None):
    """Post-bake GRADE on the hole-filled atlas, for a game-ready LIT asset:
    DE-LIGHT (strip baked shading) -> chroma-adaptive colour punch -> flat intrinsic
    regions -> detail-preserving downscale. No baked form-shading or ink (the engine
    lights the geometry). Per-pixel ops, so no triangle-coupling; `cavity` is unused
    (kept for signature compatibility)."""
    p = params
    cf = np.clip((colourfulness(rgb) - 0.04) / (0.18 - 0.04), 0.0, 1.0)
    eff_sat = (1.0 - cf) * p["mono_saturation"] + cf * p["saturation"]
    print(f"lofi.cartoonize: chroma={colourfulness(rgb):.3f} cf={cf:.2f} "
          f"eff_sat={eff_sat:.2f} delit={ao is not None}")

    rgb = delight(rgb, ao, p)             # flat intrinsic albedo (no baked shadow)
    rgb = boost_saturation_contrast(rgb, sat=eff_sat, contrast=p["contrast"])

    # flat intrinsic regions: merge shaded-vs-lit variation of one surface into one
    # colour (skin -> ~one colour), then quantize tone. region_flatten scales the merge.
    rf = float(p.get("region_flatten", 0.5))
    if rf > 0.0:
        rgb = guided_smooth(rgb, sigma=3.0, eps=0.05 * (1.0 - rf) + 0.004,
                            iters=max(1, int(round(rf * 3))))
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
    a[:, :, :3] = cartoonize_structure(a[:, :, :3], params)
    copy.pixels.foreach_set(a.ravel())
    copy.update()
    print(f"lofi.cartoonize: source '{image.name}' -> coherent cartoon {copy.size[0]}px")
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
