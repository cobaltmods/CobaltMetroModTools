#!/usr/bin/env python3
"""
metro_vfx.py — Metro Exodus .vfx / .vfs archive library (original + Enhanced Edition).

Handles "vfx version 3" archives used by Metro Exodus (2019) and Metro Exodus
PC Enhanced Edition. Pure standard library, no dependencies.

Format (reverse-engineered from MetroEX by iOrange, and verified against real
game data):

  vfx file:
    u32   version            (3 for Exodus / Exodus EE)
    u32   compressionType    (1 = LZ4)
    strz  contentVersion     (e.g. "484107" original, "src_521776-..." EE)
    u8[16] guid              (static across the game)
    u32   numPackages
    u32   numFiles
    u32   numDuplicates      (0 in retail data; unsupported if nonzero)
    Package[numPackages]:
        strz  name           (e.g. "content_03.vfs0")
        u32   numLevels
        strz  levels[numLevels]
        u32   chunk
    Entry[numFiles]:
        u16   flags          (bit 3 set = folder)
        if file:
            u16 pakIdx       (index into package list)
            u32 offset       (byte offset inside that .vfsN file)
            u32 sizeUncompressed
            u32 sizeCompressed   (== sizeUncompressed means stored raw;
                                  otherwise LZ4 "stream" chunked compression)
            xstr name
        if folder:
            u16 numFiles     (number of direct children)
            u32 firstFile    (index of first child; children are contiguous)
            xstr name
    (trailing bytes, typically 8 zero bytes)

  xstr (XOR-scrambled string):
    u16 header   = (xorMask << 8) | ((len(name) + 1) & 0xFF)
    u8[len]      name bytes, each XORed with xorMask
    u8           0x00 terminator (not XORed)
    empty string is written as 01 00 00.

  Entries form a tree: entry 0 is the root folder; a folder's children occupy
  indices [firstFile, firstFile + numFiles).

  File data in .vfsN packages:
    stored raw when sizeCompressed == sizeUncompressed, otherwise an LZ4
    "stream": repeated blocks of
        u32 blockSize              (compressed size + 8)
        u32 blockUncompressedSize  (max 0x30000)
        u8[blockSize-8]            raw LZ4 block data (window shared across blocks)

  Texture files (.512/.1024/.2048/.dds inside archives) additionally have their
  own single-block LZ4 "blob" compression around raw BC7 block data — that is a
  content-format detail independent of the archive layer.
"""

from __future__ import annotations

import io
import os
import re
import struct
import sys
from dataclasses import dataclass, field

FLAG_FOLDER = 8

# GUID used by all known retail Exodus archives (same bytes in original + EE).
RETAIL_GUID = bytes.fromhex("125be29f76f2f440b8ea0fe1a4c69e7a")


class VfxError(Exception):
    pass


class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.o = 0

    def u8(self) -> int:
        v = self.d[self.o]
        self.o += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.d, self.o)[0]
        self.o += 2
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.d, self.o)[0]
        self.o += 4
        return v

    def bytes(self, n: int) -> bytes:
        v = self.d[self.o:self.o + n]
        if len(v) != n:
            raise VfxError("unexpected end of file")
        self.o += n
        return v

    def strz(self) -> str:
        end = self.d.index(b"\x00", self.o)
        v = self.d[self.o:end].decode("latin-1")
        self.o = end + 1
        return v

    def xstr(self) -> tuple[str, int]:
        """Returns (string, xor_mask). Mask preserved for byte-identical rebuilds."""
        header = self.u16()
        strlen = header & 0xFF          # includes the terminator
        mask = (header >> 8) & 0xFF
        chars = bytearray()
        for _ in range(strlen - 1):
            chars.append(self.u8() ^ mask)
        self.u8()                       # terminating null (not XORed)
        return chars.decode("latin-1"), mask

    def remaining(self) -> bytes:
        return self.d[self.o:]


class _Writer:
    def __init__(self):
        self.b = bytearray()

    def u8(self, v): self.b.append(v & 0xFF)
    def u16(self, v): self.b += struct.pack("<H", v)
    def u32(self, v): self.b += struct.pack("<I", v)
    def raw(self, v): self.b += v

    def strz(self, s: str):
        self.b += s.encode("latin-1") + b"\x00"

    def xstr(self, s: str, mask: int):
        data = s.encode("latin-1")
        if len(data) + 1 > 0xFF:
            raise VfxError(f"name too long for xstr: {s!r}")
        if not data:
            # empty string special case: header (mask=0,len=1) + null
            self.u16(0x0001)
            self.u8(0)
            return
        self.u16(((mask & 0xFF) << 8) | ((len(data) + 1) & 0xFF))
        self.raw(bytes(c ^ mask for c in data))
        self.u8(0)


@dataclass
class Package:
    name: str
    levels: list[str] = field(default_factory=list)
    chunk: int = 0


@dataclass
class Entry:
    flags: int
    name: str
    xor_mask: int = 0x55        # preserved from parse; arbitrary for new entries
    # file fields
    pak_idx: int = 0
    offset: int = 0
    size_u: int = 0
    size_c: int = 0
    # folder fields
    first_file: int = 0
    num_files: int = 0
    # computed
    idx: int = -1
    parent: int = -1
    path: str = ""              # full path like "content\\textures\\tile\\x.512"

    @property
    def is_file(self) -> bool:
        return (self.flags & FLAG_FOLDER) == 0


class Vfx:
    def __init__(self):
        self.version = 3
        self.compression = 1
        self.content_version = ""
        self.guid = RETAIL_GUID
        self.num_duplicates = 0
        self.packages: list[Package] = []
        self.entries: list[Entry] = []
        self.tail = b"\x00" * 8
        self.source_path: str | None = None

    # ---------------- parsing ----------------

    @classmethod
    def parse(cls, path: str) -> "Vfx":
        with open(path, "rb") as f:
            data = f.read()
        v = cls()
        v.source_path = path
        r = _Reader(data)

        v.version = r.u32()
        v.compression = r.u32()
        if v.version != 3:
            raise VfxError(f"unsupported vfx version {v.version} (only version 3 = Exodus/EE)")
        if v.compression != 1:
            raise VfxError(f"unsupported compression type {v.compression}")

        v.content_version = r.strz()
        v.guid = r.bytes(16)
        num_paks = r.u32()
        num_files = r.u32()
        v.num_duplicates = r.u32()
        if v.num_duplicates != 0:
            raise VfxError(f"vfx has {v.num_duplicates} duplicates — unsupported")

        for _ in range(num_paks):
            p = Package(name=r.strz())
            n_levels = r.u32()
            p.levels = [r.strz() for _ in range(n_levels)]
            p.chunk = r.u32()
            v.packages.append(p)

        for i in range(num_files):
            flags = r.u16()
            e = Entry(flags=flags, name="")
            if (flags & FLAG_FOLDER) == 0:
                e.pak_idx = r.u16()
                e.offset = r.u32()
                e.size_u = r.u32()
                e.size_c = r.u32()
            else:
                e.num_files = r.u16()
                e.first_file = r.u32()
            e.name, e.xor_mask = r.xstr()
            e.idx = i
            v.entries.append(e)

        v.tail = r.remaining()
        v._compute_paths()
        return v

    def _compute_paths(self):
        for e in self.entries:
            e.parent = -1
        for e in self.entries:
            if not e.is_file:
                for c in range(e.first_file, e.first_file + e.num_files):
                    if 0 <= c < len(self.entries):
                        self.entries[c].parent = e.idx
        for e in self.entries:
            parts = []
            cur = e
            guard = 0
            while cur is not None and guard < 64:
                if cur.name:
                    parts.append(cur.name)
                cur = self.entries[cur.parent] if cur.parent >= 0 else None
                guard += 1
            e.path = "\\".join(reversed(parts))

    # ---------------- serialization ----------------

    def serialize(self) -> bytes:
        w = _Writer()
        w.u32(self.version)
        w.u32(self.compression)
        w.strz(self.content_version)
        w.raw(self.guid)
        w.u32(len(self.packages))
        w.u32(len(self.entries))
        w.u32(self.num_duplicates)
        for p in self.packages:
            w.strz(p.name)
            w.u32(len(p.levels))
            for s in p.levels:
                w.strz(s)
            w.u32(p.chunk)
        for e in self.entries:
            w.u16(e.flags)
            if e.is_file:
                w.u16(e.pak_idx)
                w.u32(e.offset)
                w.u32(e.size_u)
                w.u32(e.size_c)
            else:
                w.u16(e.num_files)
                w.u32(e.first_file)
            w.xstr(e.name, e.xor_mask)
        w.raw(self.tail)
        return bytes(w.b)

    # ---------------- queries ----------------

    def find(self, pattern: str) -> list[Entry]:
        rx = re.compile(pattern, re.IGNORECASE)
        return [e for e in self.entries if e.is_file and rx.search(e.path)]

    def find_one(self, path: str) -> Entry:
        matches = [e for e in self.entries if e.path.lower() == path.lower()]
        if not matches:
            raise VfxError(f"path not found in vfx: {path}")
        if len(matches) > 1:
            raise VfxError(f"ambiguous path ({len(matches)} matches): {path}")
        return matches[0]

    def pak_index(self, pak_name: str) -> int | None:
        for i, p in enumerate(self.packages):
            if p.name.lower() == pak_name.lower():
                return i
        return None

    # ---------------- data access ----------------

    def read_file(self, entry: Entry, base_dir: str | None = None) -> bytes:
        """Read + (if needed) decompress one file's data from its .vfsN package."""
        if not entry.is_file:
            raise VfxError("not a file entry")
        base = base_dir or os.path.dirname(self.source_path or ".")
        pak_path = os.path.join(base, self.packages[entry.pak_idx].name)
        with open(pak_path, "rb") as f:
            f.seek(entry.offset)
            raw = f.read(entry.size_c)
        if len(raw) != entry.size_c:
            raise VfxError(f"short read from {pak_path}")
        if entry.size_c == entry.size_u:
            return raw
        return lz4_decompress_stream(raw, entry.size_u)


# ---------------- LZ4 (pure python) ----------------

def lz4_decompress_block(src: bytes, dst: bytearray, expected_add: int) -> None:
    """Decompress one raw LZ4 block, appending to dst (dst also acts as the
    match window, so streamed blocks work). expected_add = uncompressed size."""
    end_len = len(dst) + expected_add
    i = 0
    n = len(src)
    while i < n:
        token = src[i]; i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]; i += 1
                lit += b
                if b != 255:
                    break
        if lit:
            dst += src[i:i + lit]
            i += lit
        if len(dst) >= end_len or i >= n:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        if off == 0:
            raise VfxError("corrupt LZ4 block (offset 0)")
        mlen = (token & 0xF) + 4
        if (token & 0xF) == 15:
            while True:
                b = src[i]; i += 1
                mlen += b
                if b != 255:
                    break
        pos = len(dst) - off
        if pos < 0:
            raise VfxError("corrupt LZ4 block (offset beyond window)")
        for _ in range(mlen):
            dst.append(dst[pos])
            pos += 1
    if len(dst) != end_len:
        raise VfxError(f"LZ4 block decoded {len(dst) - (end_len - expected_add)} bytes, expected {expected_add}")


def lz4_decompress_stream(data: bytes, uncompressed_size: int) -> bytes:
    """Metro 'stream' compression: chunked LZ4 with shared window."""
    out = bytearray()
    r = _Reader(data)
    while r.o < len(data) and len(out) < uncompressed_size:
        block_size = r.u32()
        block_usize = r.u32()
        comp = r.bytes(block_size - 8)
        lz4_decompress_block(comp, out, block_usize)
    if len(out) != uncompressed_size:
        raise VfxError(f"stream decoded {len(out)}, expected {uncompressed_size}")
    return bytes(out)


def lz4_decompress_blob(data: bytes, uncompressed_size: int) -> bytes:
    """Metro 'blob' compression: single raw LZ4 block (used inside texture files)."""
    out = bytearray()
    lz4_decompress_block(data, out, uncompressed_size)
    return bytes(out)


def lz4_compress_blob(data: bytes) -> bytes:
    """Produce a valid LZ4 block for `data`.

    Uses a simple greedy encoder with a 16-byte-period fast path (ideal for
    repeated-block textures); falls back to literals for incompressible data.
    Always valid LZ4, not maximal compression.
    """
    n = len(data)
    out = bytearray()

    def write_seq(lit: bytes, mlen: int, moff: int):
        lit_len = len(lit)
        tok_lit = 15 if lit_len >= 15 else lit_len
        if mlen:
            tok_m = mlen - 4
            tok_match = 15 if tok_m >= 15 else tok_m
        else:
            tok_match = 0
        out.append((tok_lit << 4) | tok_match)
        if lit_len >= 15:
            rem = lit_len - 15
            while rem >= 255:
                out.append(255); rem -= 255
            out.append(rem)
        out.extend(lit)
        if mlen:
            out.extend(struct.pack("<H", moff))
            tok_m = mlen - 4
            if tok_m >= 15:
                rem = tok_m - 15
                while rem >= 255:
                    out.append(255); rem -= 255
                out.append(rem)

    # Fast path: whole buffer is one 16-byte pattern repeated.
    if n >= 32 and n % 16 == 0 and data[:16] * (n // 16) == data:
        # LZ4 end-of-block rules: last match must start >= 12 bytes before end
        # and the block must end with >= 5 literal bytes (we use 16).
        match_len = n - 16 - 16
        write_seq(data[:16], match_len, 16)
        write_seq(data[-16:], 0, 0)
        return bytes(out)

    # Generic fallback: one literals-only sequence for the whole buffer
    # (only the final LZ4 sequence may omit its match, so it must be a single
    # sequence). Valid LZ4, no compression.
    write_seq(data, 0, 0)
    return bytes(out)


# ---------------- BC7 helpers ----------------

# One BC7 mode-6 block whose endpoints and indices are all zero decodes to
# 16 pixels of RGBA(0,0,0,0) — fully transparent black.
BC7_TRANSPARENT_BLOCK = bytes([0x40] + [0] * 15)


def bc7_size(width: int, height: int, num_mips: int) -> int:
    total = 0
    w, h = width, height
    for _ in range(num_mips):
        total += max(1, w // 4) * max(1, h // 4) * 16
        w = max(1, w // 2)
        h = max(1, h // 2)
    return total


def bc7_transparent_image(width: int, height: int, num_mips: int) -> bytes:
    return BC7_TRANSPARENT_BLOCK * (bc7_size(width, height, num_mips) // 16)


def make_transparent_metro_texture(dimension: int) -> bytes:
    """Build the content of a fully transparent .512/.1024/.2048 texture file
    (LZ4 'blob' of BC7 data). .512 carries a 10-mip chain, others 1 mip."""
    mips = 10 if dimension == 512 else 1
    return lz4_compress_blob(bc7_transparent_image(dimension, dimension, mips))


# ---------------- patch building ----------------

def build_patch(vfx: Vfx, replacements: dict[str, bytes], pak_name: str = "content_99.vfs0",
                pak_chunk: int | None = None) -> tuple[bytes, bytes]:
    """Repoint `replacements` ({vfx_path: new_file_data}) into a new package.

    Returns (new_vfx_bytes, new_vfs0_bytes). Files are stored uncompressed
    (sizeCompressed == sizeUncompressed), which the engine reads directly.
    Identical data blobs are stored once and shared between entries.
    """
    idx = vfx.pak_index(pak_name)
    if idx is None:
        idx = len(vfx.packages)
        if pak_chunk is None:
            pak_chunk = max((p.chunk for p in vfx.packages), default=1) + 1
        vfx.packages.append(Package(name=pak_name, levels=[], chunk=pak_chunk))

    # 8 leading pad bytes, mirroring the layout of known-working community mods.
    vfs = bytearray(b"\x00" * 8)
    dedup: dict[bytes, int] = {}
    for path, data in sorted(replacements.items()):
        e = vfx.find_one(path)
        if data in dedup:
            offset = dedup[data]
        else:
            offset = len(vfs)
            dedup[data] = offset
            vfs += data
        e.pak_idx = idx
        e.offset = offset
        e.size_u = len(data)
        e.size_c = len(data)
    return vfx.serialize(), bytes(vfs)


# ---------------- CLI ----------------

def _cmd_info(argv):
    v = Vfx.parse(argv[0])
    print(f"content version : {v.content_version}")
    print(f"guid            : {v.guid.hex()}")
    print(f"packages        : {len(v.packages)}")
    print(f"files           : {len(v.entries)}")
    print(f"tail            : {v.tail.hex()} ({len(v.tail)} bytes)")
    for i, p in enumerate(v.packages):
        lv = f" levels={p.levels}" if p.levels else ""
        print(f"  [{i:3}] chunk={p.chunk:3}  {p.name}{lv}")


def _cmd_verify(argv):
    path = argv[0]
    v = Vfx.parse(path)
    rebuilt = v.serialize()
    orig = open(path, "rb").read()
    if rebuilt == orig:
        print(f"OK: byte-identical roundtrip ({len(orig):,} bytes)")
    else:
        print(f"MISMATCH: original {len(orig):,} vs rebuilt {len(rebuilt):,}")
        for i, (a, b) in enumerate(zip(orig, rebuilt)):
            if a != b:
                print(f"first difference at offset {i:#x}")
                break
        sys.exit(1)


def _cmd_find(argv):
    v = Vfx.parse(argv[0])
    for e in v.find(argv[1]):
        pak = v.packages[e.pak_idx].name
        comp = "raw" if e.size_c == e.size_u else "lz4"
        print(f"{e.path}   [{pak} @ {e.offset:#x}, u={e.size_u:,} c={e.size_c:,} {comp}]")


def _cmd_extract(argv):
    v = Vfx.parse(argv[0])
    out_dir = argv[2] if len(argv) > 2 else "."
    for e in v.find(argv[1]):
        data = v.read_file(e)
        dest = os.path.join(out_dir, e.path.replace("\\", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        print(f"extracted {e.path} -> {dest} ({len(data):,} bytes)")


def _cmd_pack(argv):
    """pack <vfx> <mod_folder> <out_dir> [pak_name]

    mod_folder mirrors archive paths (e.g. mod\\content\\textures\\tile\\x.512).
    Every file inside is packed into a new/reused _99 package and the vfx is
    rewritten to point at it. Output goes to out_dir (original untouched)."""
    vfx_path, mod_folder, out_dir = argv[0], argv[1], argv[2]
    pak_name = argv[3] if len(argv) > 3 else "content_99.vfs0"
    v = Vfx.parse(vfx_path)
    replacements = {}
    for root, _dirs, files in os.walk(mod_folder):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, mod_folder).replace(os.sep, "\\")
            with open(full, "rb") as f:
                replacements[v.find_one(rel).path] = f.read()
    if not replacements:
        print("no files found in mod folder"); sys.exit(1)
    new_vfx, new_vfs = build_patch(v, replacements, pak_name)
    os.makedirs(out_dir, exist_ok=True)
    vfx_out = os.path.join(out_dir, os.path.basename(vfx_path))
    vfs_out = os.path.join(out_dir, pak_name)
    with open(vfx_out, "wb") as f:
        f.write(new_vfx)
    with open(vfs_out, "wb") as f:
        f.write(new_vfs)
    print(f"packed {len(replacements)} files")
    print(f"  -> {vfx_out} ({len(new_vfx):,} bytes)")
    print(f"  -> {vfs_out} ({len(new_vfs):,} bytes)")


def main():
    cmds = {"info": _cmd_info, "verify": _cmd_verify, "find": _cmd_find,
            "extract": _cmd_extract, "pack": _cmd_pack}
    if len(sys.argv) < 3 or sys.argv[1] not in cmds:
        print("usage: metro_vfx.py info <vfx>\n"
              "       metro_vfx.py verify <vfx>\n"
              "       metro_vfx.py find <vfx> <regex>\n"
              "       metro_vfx.py extract <vfx> <regex> [out_dir]\n"
              "       metro_vfx.py pack <vfx> <mod_folder> <out_dir> [pak_name]")
        sys.exit(2)
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
