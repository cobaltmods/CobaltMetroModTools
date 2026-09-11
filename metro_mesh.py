#!/usr/bin/env python3
"""
metro_mesh.py — collapse Metro Exodus creature meshes so they render invisibly.

A Metro `.mesh` file is a tree of chunks `[u32 id][u32 size][payload]`. The
SUBMESHES container (id 9) is special: its payload is a sequence of
`[u32 index][u32 size][submesh chunks]` entries, and each submesh carries a
HEADER chunk (id 1) whose `float vscale` (offset 56 in the header payload) every
vertex position is multiplied by at load time (MetroModel.cpp: `pos *= vscale`).

Shrinking each meaningful `vscale` toward zero collapses all of a mesh's
geometry to a sub-millimetre point at its pivot — the creature renders nothing
visible, in any shader, while its physics/AI (driven by the separate .ph_model),
skeleton, and animations are untouched. This is the safest way to hide a
creature: the file stays exactly the same size and structure, only one float per
submesh changes.

The engine resets `vscale <= 1.19e-7` back to 1.0, so we scale by a small factor
(default 3e-4) rather than zeroing — a 2 m spider becomes ~0.6 mm.

usage:
  python metro_mesh.py inspect <mesh_file>
  python metro_mesh.py verify  <mesh_file>          # parse + byte-identical rebuild
  python metro_mesh.py collapse <in_mesh> <out_mesh> [factor]
"""

from __future__ import annotations

import struct
import sys

HEADER = 0x01
SUBMESHES = 0x09
LOD1 = 0x0B
LOD2 = 0x0C
INLINE = 0x0F
VSCALE_OFF = 56          # float offset within a HEADER chunk payload
EPSILON = 1.192092896e-07


class MeshError(Exception):
    pass


def _u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


def find_vscales(data: bytes) -> list[tuple[int, float]]:
    """Return [(file_offset_of_float, current_value)] for every meaningful
    submesh vscale. Walks the real chunk tree; raises if structure is unexpected."""
    out: list[tuple[int, float]] = []

    def walk_chunks(base: int, length: int):
        o = base
        end = base + length
        while o + 8 <= end:
            cid = _u32(data, o)
            csize = _u32(data, o + 4)
            payload = o + 8
            cend = payload + csize
            if cend > end:
                raise MeshError(f"chunk 0x{cid:x} at {o} overruns parent")
            if cid == HEADER:
                if csize >= VSCALE_OFF + 4:
                    voff = payload + VSCALE_OFF
                    vscale = struct.unpack_from("<f", data, voff)[0]
                    # only the real, non-reset scales matter
                    if vscale > EPSILON:
                        out.append((voff, vscale))
            elif cid == SUBMESHES:
                walk_submeshes(payload, csize)
            elif cid in (LOD1, LOD2, INLINE):
                walk_chunks(payload, csize)
            o = cend

    def walk_submeshes(base: int, length: int):
        # payload = repeated [u32 index][u32 size][submesh chunks]
        o = base
        end = base + length
        while o + 8 <= end:
            idx = _u32(data, o)
            ssize = _u32(data, o + 4)
            spayload = o + 8
            send = spayload + ssize
            if send > end:
                raise MeshError(f"submesh idx {idx} at {o} overruns container")
            walk_chunks(spayload, ssize)
            o = send

    walk_chunks(0, len(data))
    return out


def collapse(data: bytes, factor: float = 3e-4) -> bytes:
    """Return a copy of `data` with every meaningful submesh vscale multiplied
    by `factor` (kept safely above the engine's reset epsilon)."""
    buf = bytearray(data)
    edits = find_vscales(data)
    if not edits:
        raise MeshError("no meaningful vscale found — mesh structure unexpected, not edited")
    for voff, vscale in edits:
        new = max(vscale * factor, EPSILON * 8)
        struct.pack_into("<f", buf, voff, new)
    return bytes(buf)


def _cmd_inspect(argv):
    data = open(argv[0], "rb").read()
    edits = find_vscales(data)
    print(f"{argv[0]}  ({len(data):,} bytes)")
    print(f"  {len(edits)} meaningful submesh vscale(s):")
    for voff, vscale in edits:
        print(f"    @{voff:>8}: vscale = {vscale:.5f}")


def _cmd_verify(argv):
    """Prove the parser is non-destructive: collapse by factor 1.0 changes
    nothing, so output must be byte-identical to input."""
    data = open(argv[0], "rb").read()
    edits = find_vscales(data)
    # rebuild with factor that maps each value to itself
    buf = bytearray(data)
    for voff, vscale in edits:
        struct.pack_into("<f", buf, voff, vscale)
    if bytes(buf) == data:
        print(f"OK: parsed {len(edits)} vscale(s), byte-identical roundtrip ({len(data):,} bytes)")
    else:
        print("MISMATCH — parser is not safe on this file")
        sys.exit(1)


def _cmd_collapse(argv):
    factor = float(argv[2]) if len(argv) > 2 else 3e-4
    data = open(argv[0], "rb").read()
    out = collapse(data, factor)
    assert len(out) == len(data)
    with open(argv[1], "wb") as f:
        f.write(out)
    print(f"collapsed {argv[0]} -> {argv[1]} (factor {factor}, {len(data):,} bytes, size unchanged)")


def main():
    cmds = {"inspect": _cmd_inspect, "verify": _cmd_verify, "collapse": _cmd_collapse}
    if len(sys.argv) < 3 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(2)
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
