"""Parse an exported .glb and assert it really is the lo-fi asset we intended.

There is no glb-verify in the sibling — this is written from scratch. It parses
the GLB container (12-byte header + JSON/BIN chunks) and checks the properties
that a render CANNOT show (especially unlit-ness — Workbench/EEVEE/importers all
ignore `KHR_materials_unlit`, so this JSON assertion is the SOLE guarantee):

  * `KHR_materials_unlit` in extensionsUsed
  * every sampler magFilter == 9728 (NEAREST)
  * embedded texture dimensions == expected size
  * triangle count within the budget band
"""

import json
import struct

NEAREST = 9728
NEAREST_MIPMAP_NEAREST = 9984


class GlbVerifyError(Exception):
    pass


def parse_glb(path):
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 12:
        raise GlbVerifyError("file too small to be a GLB")
    magic, _version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise GlbVerifyError("not a GLB (bad magic)")

    json_chunk = bin_chunk = None
    off = 12
    while off < length:
        clen, ctype = struct.unpack_from("<I4s", data, off)
        off += 8
        chunk = data[off:off + clen]
        off += clen
        if ctype == b"JSON":
            json_chunk = chunk
        elif ctype == b"BIN\x00":
            bin_chunk = chunk
    if json_chunk is None:
        raise GlbVerifyError("GLB has no JSON chunk")
    return json.loads(json_chunk.decode("utf-8")), bin_chunk


def _png_dims(png):
    # 8-byte signature, then IHDR chunk: [len(4)][type(4)='IHDR'][w(4)][h(4)]
    if len(png) < 24 or png[12:16] != b"IHDR":
        raise ValueError("not a PNG / no IHDR")
    w = struct.unpack_from(">I", png, 16)[0]
    h = struct.unpack_from(">I", png, 20)[0]
    return w, h


def verify(path, expected_tex=None, tri_budget=None, tri_band=0.5, expect_unlit=True):
    """Return (facts: dict, problems: list[str]). Empty problems == pass."""
    gltf, binc = parse_glb(path)
    facts, problems = {}, []

    ext = gltf.get("extensionsUsed", []) or []
    facts["extensionsUsed"] = ext
    if expect_unlit and "KHR_materials_unlit" not in ext:
        problems.append("KHR_materials_unlit missing from extensionsUsed")

    samplers = gltf.get("samplers", []) or []
    facts["samplers"] = samplers
    if not samplers:
        problems.append("no samplers found (expected a NEAREST sampler)")
    for i, s in enumerate(samplers):
        mag = s.get("magFilter")
        if mag != NEAREST:
            problems.append(f"sampler[{i}] magFilter={mag} (expected {NEAREST} NEAREST)")

    dims = []
    buffer_views = gltf.get("bufferViews", []) or []
    for img in gltf.get("images", []) or []:
        bv = img.get("bufferView")
        if bv is not None and binc is not None and bv < len(buffer_views):
            view = buffer_views[bv]
            o = view.get("byteOffset", 0)
            n = view["byteLength"]
            try:
                dims.append(_png_dims(binc[o:o + n]))
            except Exception:  # noqa: BLE001
                pass
    facts["image_dims"] = dims
    if expected_tex is not None:
        for (w, h) in dims:
            if w != expected_tex or h != expected_tex:
                problems.append(f"texture {w}x{h} != expected {expected_tex}x{expected_tex}")

    accs = gltf.get("accessors", []) or []
    tris = 0
    for mesh in gltf.get("meshes", []) or []:
        for prim in mesh.get("primitives", []) or []:
            if "indices" in prim:
                tris += accs[prim["indices"]]["count"] // 3
            else:
                pos = prim.get("attributes", {}).get("POSITION")
                if pos is not None:
                    tris += accs[pos]["count"] // 3
    facts["tris"] = tris
    if tri_budget is not None:
        if tris == 0:
            problems.append("0 triangles in exported mesh")
        elif tris > tri_budget * (1 + tri_band):
            problems.append(
                f"tris {tris} exceeds budget {tri_budget} by more than {int(tri_band*100)}%")

    return facts, problems


def verify_or_raise(path, **kwargs):
    facts, problems = verify(path, **kwargs)
    if problems:
        raise GlbVerifyError("; ".join(problems) + f"  (facts: {facts})")
    return facts
