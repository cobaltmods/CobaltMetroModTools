#!/usr/bin/env python3
"""
metro_tex.py — convert between Metro Exodus native textures and BC7 DDS.

Metro Exodus (and EE) store world textures as three files per texture:
    name.2048   LZ4 blob of BC7 data, single 2048x2048 mip   (high quality)
    name.1024   LZ4 blob of BC7 data, single 1024x1024 mip   (medium quality)
    name.512    LZ4 blob of BC7 data, 10-mip chain 512..1     (always loaded)
(EE 4K packs add name.4096, single mip, same scheme.)

to_dds:   merge extracted native files into one standard BC7 DDS (DX10 header)
          that GIMP / Photoshop (Intel plugin) / texconv can open.
from_dds: split a BC7 DDS with a FULL mip chain back into native files.
          Make such a DDS with e.g.:  texconv -f BC7_UNORM -m 0 input.png

usage:
  python metro_tex.py to_dds out.dds name.512 [name.1024] [name.2048] [name.4096]
  python metro_tex.py from_dds in.dds out_basename
"""

import os
import struct
import sys

from metro_vfx import lz4_decompress_blob, lz4_compress_blob, bc7_size

DDS_MAGIC = b"DDS "
DXGI_BC7 = (97, 98, 99)  # TYPELESS, UNORM, UNORM_SRGB


def mip_dims(dim):
    """Mips stored in a native file of this top dimension."""
    return 1 if dim > 512 else 10


def write_dds(path, width, height, mip_count, bc7_data):
    header = struct.pack(
        "<4s I I I I I I I 11I  I I 4s 5I  I I I I I",
        DDS_MAGIC, 124,
        0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000,  # caps|h|w|pixfmt|mipcount|linearsize
        height, width, max(1, (width // 4) * 16), 0, mip_count,
        *([0] * 11),
        32, 0x4, b"DX10", 0, 0, 0, 0, 0,
        0x1000 | 0x8 | 0x400000, 0, 0, 0, 0)  # caps: texture|complex|mipmap
    dx10 = struct.pack("<5I", 98, 3, 0, 1, 0)  # BC7_UNORM, TEXTURE2D
    with open(path, "wb") as f:
        f.write(header + dx10 + bc7_data)


def read_dds(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != DDS_MAGIC:
        sys.exit("not a DDS file")
    height, width = struct.unpack_from("<II", data, 12)
    mip_count = struct.unpack_from("<I", data, 28)[0] or 1
    fourcc = data[84:88]
    offset = 128
    if fourcc == b"DX10":
        dxgi = struct.unpack_from("<I", data, 128)[0]
        if dxgi not in DXGI_BC7:
            sys.exit(f"DDS is DXGI format {dxgi}, need BC7 (98). "
                     f"Re-encode with: texconv -f BC7_UNORM -m 0 <input>")
        offset = 148
    else:
        sys.exit("DDS has no DX10 header — need BC7. Use: texconv -f BC7_UNORM -m 0 <input>")
    return width, height, mip_count, data[offset:]


def cmd_to_dds(argv):
    out_path, native_files = argv[0], argv[1:]
    pieces = {}
    for p in native_files:
        dim = int(p.rsplit(".", 1)[1])
        with open(p, "rb") as f:
            blob = f.read()
        raw = lz4_decompress_blob(blob, bc7_size(dim, dim, mip_dims(dim)))
        pieces[dim] = raw
        print(f"  {os.path.basename(p)}: {len(blob):,} -> {len(raw):,} bytes BC7")
    dims = sorted(pieces, reverse=True)
    if 512 not in pieces:
        sys.exit("need at least the .512 file (it holds the 512..1 mip chain)")
    top = dims[0]
    merged = b"".join(pieces[d] for d in dims)  # e.g. 2048 mip + 1024 mip + 512-chain
    mip_count = len([d for d in dims if d > 512]) + 10
    write_dds(out_path, top, top, mip_count, merged)
    print(f"wrote {out_path} ({top}x{top}, {mip_count} mips)")


def cmd_from_dds(argv):
    in_path, out_base = argv[0], argv[1]
    width, height, mip_count, data = read_dds(in_path)
    if width != height:
        sys.exit("texture must be square")
    if width < 512 or width & (width - 1):
        sys.exit("dimension must be a power of two >= 512")
    import math
    full_chain = int(math.log2(width)) + 1
    if mip_count < full_chain:
        sys.exit(f"DDS has {mip_count} mips, need full chain ({full_chain}). "
                 f"Re-encode with: texconv -f BC7_UNORM -m 0 <input>")

    # slice out each mip
    mips = []
    off = 0
    d = width
    for _ in range(mip_count):
        size = bc7_size(d, d, 1)
        mips.append((d, data[off:off + size]))
        off += size
        d = max(1, d // 2)

    d = width
    i = 0
    while d > 512:
        blob = lz4_compress_blob(mips[i][1])
        with open(f"{out_base}.{d}", "wb") as f:
            f.write(blob)
        print(f"wrote {out_base}.{d} ({len(blob):,} bytes)")
        i += 1
        d //= 2
    chain = b"".join(m[1] for m in mips[i:i + 10])
    assert len(chain) == bc7_size(512, 512, 10)
    blob = lz4_compress_blob(chain)
    with open(f"{out_base}.512", "wb") as f:
        f.write(blob)
    print(f"wrote {out_base}.512 ({len(blob):,} bytes, 10-mip chain)")


def main():
    if len(sys.argv) < 4 or sys.argv[1] not in ("to_dds", "from_dds"):
        print(__doc__)
        sys.exit(2)
    (cmd_to_dds if sys.argv[1] == "to_dds" else cmd_from_dds)(sys.argv[2:])


if __name__ == "__main__":
    main()
