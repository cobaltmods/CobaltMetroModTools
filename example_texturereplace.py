#!/usr/bin/env python3
"""
example_texturereplace.py — the smallest complete Metro Exodus texture mod.

This is a teaching example: it replaces ONE texture in Metro Exodus / Enhanced
Edition and writes a ready-to-install content.vfx + content_99.vfs0. Read it
top to bottom to learn the whole pipeline; the other tools just do this at scale.

The five steps of every archive mod:
  1. parse the game's content.vfx  (the index)
  2. get your replacement file's bytes, in the game's native format
  3. build_patch() repoints the target entry into a new content_99.vfs0
  4. write the patched vfx + the new vfs0 next to each other
  5. re-open the output and verify the bytes read back exactly

usage:
  # blank a texture to transparent (no art needed — good first run):
  python example_texturereplace.py <game_dir> <vfx_texture_path> <out_dir>

  # or supply your own art already converted to native format with metro_tex.py:
  python example_texturereplace.py <game_dir> <vfx_texture_path> <out_dir> <file.512>

example:
  python example_texturereplace.py ^
      "E:\\SteamLibrary\\steamapps\\common\\Metro Exodus Enhanced Edition" ^
      "content\\textures\\tile\\tile_pautina.2048" ^
      out
"""

import os
import sys

from metro_vfx import (Vfx, build_patch, bc7_size,
                       make_transparent_metro_texture, lz4_decompress_blob,
                       bc7_transparent_image)


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(2)

    game_dir = sys.argv[1]
    target = sys.argv[2].replace("/", "\\")     # e.g. content\textures\tile\tile_pautina.2048
    out_dir = sys.argv[3]
    my_file = sys.argv[4] if len(sys.argv) > 4 else None

    # --- 1. parse the index (fall back to the vanilla backup if a mod is live) ---
    vfx_path = os.path.join(game_dir, "content.vfx")
    if not os.path.exists(vfx_path) and os.path.exists(vfx_path + ".bak"):
        vfx_path = vfx_path + ".bak"
    print(f"1. parsing {vfx_path}")
    vfx = Vfx.parse(vfx_path)

    # confirm the target exists (find_one raises a helpful error if not)
    entry = vfx.find_one(target)
    print(f"   found {entry.path}  ({entry.size_u:,} bytes, in {vfx.packages[entry.pak_idx].name})")

    # --- 2. get the replacement bytes ---
    if my_file:
        with open(my_file, "rb") as f:
            new_bytes = f.read()
        print(f"2. using your file {my_file} ({len(new_bytes):,} bytes)")
        print("   (make sure its resolution matches the target's .512/.1024/.2048/.4096)")
    else:
        # No art supplied: generate a fully transparent texture of the right size.
        # A Metro texture's resolution is its file extension.
        dim = int(target.rsplit(".", 1)[1])
        new_bytes = make_transparent_metro_texture(dim)
        # sanity-check it decodes to what we expect before shipping it
        mips = 10 if dim == 512 else 1
        assert lz4_decompress_blob(new_bytes, bc7_size(dim, dim, mips)) == \
               bc7_transparent_image(dim, dim, mips)
        print(f"2. generated transparent {dim}x{dim} BC7 ({len(new_bytes):,} bytes)")

    # --- 3 & 4. patch the index into a new package and write both files ---
    print("3. building patch (repointing the entry into content_99.vfs0)")
    new_vfx, new_vfs = build_patch(vfx, {entry.path: new_bytes}, "content_99.vfs0")

    os.makedirs(out_dir, exist_ok=True)
    out_vfx = os.path.join(out_dir, "content.vfx")
    out_vfs = os.path.join(out_dir, "content_99.vfs0")
    with open(out_vfx, "wb") as f:
        f.write(new_vfx)
    with open(out_vfs, "wb") as f:
        f.write(new_vfs)
    print(f"4. wrote {out_vfx} ({len(new_vfx):,} bytes)")
    print(f"   wrote {out_vfs} ({len(new_vfs):,} bytes)")

    # --- 5. verify: re-open the output and read the entry straight back ---
    check = Vfx.parse(out_vfx)
    got = check.read_file(check.find_one(target), base_dir=out_dir)
    assert got == new_bytes, "readback mismatch!"
    print("5. verified: the replacement reads back byte-for-byte from the built archive")

    print("\nInstall: back up the game's content.vfx to content.vfx.bak, then copy")
    print("both files above into the game folder. Uninstall = restore the backup")
    print("and delete content_99.vfs0.")


if __name__ == "__main__":
    main()
