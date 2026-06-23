"""Heal: keep only the largest connected component, drop floating scraps.

Connectivity in Blender is geometric (shared verts), so we union-find vertices
over edges, score components by face count, and delete everything outside the
winner. (Caveat from the plan: a scan's floor/table can be the biggest part —
this is a toggle, off-able.)
"""

from collections import Counter

import bmesh


def run(obj, settings, context):
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    if len(bm.faces) == 0:
        bm.free()
        return

    parent = list(range(len(bm.verts)))

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:        # path compression
            parent[x], x = root, parent[x]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for e in bm.edges:
        union(e.verts[0].index, e.verts[1].index)

    counts = Counter(find(f.verts[0].index) for f in bm.faces)
    best = counts.most_common(1)[0][0]
    n_components = len(counts)

    verts_to_del = [v for v in bm.verts if find(v.index) != best]
    if verts_to_del:
        bmesh.ops.delete(bm, geom=verts_to_del, context="VERTS")

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    print(f"lofi.heal: kept largest of {n_components} components, "
          f"faces now {len(mesh.polygons)}")
