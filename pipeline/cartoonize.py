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
def _shading(luma_shape, ao, cavity, ao_strength, cavity_strength):
    shade = np.ones(luma_shape, dtype=np.float32)
    if ao is not None:
        shade *= (1.0 - ao_strength * (1.0 - ao))          # darken occluded
    if cavity is not None:
        crev = np.clip(0.5 - cavity, 0.0, 0.5) * 2.0        # 0..1 in concave
        shade *= (1.0 - cavity_strength * crev)             # darken crevices
    return np.clip(shade, 0.0, 1.0)


def stylize(rgb, params, ao=None, cavity=None):
    """Full cartoon chain on an (H,W,3) float array. Returns (tex,tex,3)."""
    p = params
    rgb = guided_smooth(rgb, sigma=p["smooth_sigma"], eps=p["smooth_eps"],
                        iters=p["smooth_iters"])
    rgb = boost_saturation_contrast(rgb, sat=p["saturation"], contrast=p["contrast"])
    if p["posterize_levels"]:
        rgb = posterize(rgb, p["posterize_levels"])
    if p["edge_strength"] > 0.0:
        luma = (rgb * _LUMA).sum(axis=2)
        ink = xdog_edges(luma, sigma=p["edge_sigma"], k=1.6, tau=0.98,
                         eps=p["edge_eps"], phi=p["edge_phi"])
        ink = 1.0 - p["edge_strength"] * (1.0 - ink)    # scale line darkness
        rgb = rgb * ink[:, :, None]
    if ao is not None or cavity is not None:
        shade = _shading(rgb.shape[:2], ao, cavity,
                         p["ao_strength"], p["cavity_strength"])
        rgb = rgb * shade[:, :, None]
    rgb = np.clip(rgb, 0.0, 1.0)
    return dpid_downscale(rgb, p["supersample"], lam=p["dpid_lambda"])


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
        "edge_strength": g("edge_strength", 1.0),
        "edge_sigma": g("edge_sigma", 1.0),
        "edge_eps": g("edge_eps", 0.0),
        "edge_phi": g("edge_phi", 12.0),
        "ao_strength": g("shading_strength", 0.6),
        "cavity_strength": g("cavity_strength", 0.6),
        "dpid_lambda": g("dpid_lambda", 1.0),
    }


def run(albedo_img, settings, out_size, ao_img=None, cavity_img=None, temp=None):
    """Stylize the supersampled `albedo_img` and write the result into a NEW
    `out_size` image (pixelate.run reads image.size, so the downscaled result must
    be its own target-size datablock). Returns the new image."""
    import bpy

    p = params_from_settings(settings)
    p["supersample"] = max(1, albedo_img.size[0] // out_size)   # actual integer factor

    rgb = _read_rgba(albedo_img)[:, :, :3]
    small = stylize(rgb, p, ao=_read_gray(ao_img), cavity=_read_gray(cavity_img))

    out = bpy.data.images.new("lofi_cartoon", out_size, out_size, alpha=True)
    rgba = np.ones((out_size, out_size, 4), dtype=np.float32)
    rgba[:, :small.shape[1], :3] = small[:out_size, :out_size, :3]
    out.pixels.foreach_set(rgba.ravel())
    out.update()
    print(f"lofi.cartoonize: {albedo_img.size[0]}px ->{out_size}px "
          f"(ss x{p['supersample']}, posterize {p['posterize_levels']}, "
          f"edge {p['edge_strength']}, sat {p['saturation']})")
    return out
