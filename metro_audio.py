#!/usr/bin/env python3
"""
metro_audio.py — replace Metro Exodus sounds with silence.

Metro `.vba` sound files are raw OGG/Vorbis streams (the engine decodes them
with stb_vorbis; MetroEX's exporter simply writes the bytes out as .ogg). To
mute a sound we swap it for a short silent OGG that matches the original's
channel count and sample rate, so the engine's expectations (mono positional
sources vs. stereo) are preserved. Looping sounds stay silent because the loop
of silence is silent.

Requires ffmpeg with libvorbis on PATH (used to synthesise the silence).

usage:
  python metro_audio.py fmt <file.vba>       # print channels + sample rate
  python metro_audio.py silence <out.vba> [channels] [rate] [seconds]
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile


class AudioError(Exception):
    pass


def read_ogg_format(data: bytes) -> tuple[int, int]:
    """Return (channels, sample_rate) from a Vorbis identification header."""
    if data[:4] != b"OggS":
        raise AudioError("not an OGG stream")
    # The Vorbis identification header begins with byte 0x01 then 'vorbis'.
    i = data.find(b"\x01vorbis")
    if i < 0:
        raise AudioError("no Vorbis identification header")
    # layout after '\x01vorbis': u32 version, u8 channels, u32 rate, ...
    base = i + 7
    channels = data[base + 4]
    rate = struct.unpack_from("<I", data, base + 5)[0]
    return channels, rate


_cache: dict[tuple[int, int, float], bytes] = {}


def make_silence(channels: int, rate: int, seconds: float = 0.25) -> bytes:
    """Synthesise a silent OGG/Vorbis clip via ffmpeg (cached per format)."""
    key = (channels, rate, seconds)
    if key in _cache:
        return _cache[key]
    layout = {1: "mono", 2: "stereo"}.get(channels, f"{channels}c")
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "s.ogg")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", f"anullsrc=r={rate}:cl={layout}",
               "-t", str(seconds), "-c:a", "libvorbis", "-q:a", "0", out]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            raise AudioError(f"ffmpeg failed: {r.stderr.decode(errors='replace')[:300]}")
        with open(out, "rb") as f:
            data = f.read()
    _cache[key] = data
    return data


def silence_for(vba_data: bytes, seconds: float = 0.25) -> bytes:
    """Silent replacement matching the format of an existing .vba's audio."""
    ch, rate = read_ogg_format(vba_data)
    return make_silence(ch, rate, seconds)


def _cmd_fmt(argv):
    ch, rate = read_ogg_format(open(argv[0], "rb").read())
    print(f"{argv[0]}: channels={ch} rate={rate}")


def _cmd_silence(argv):
    out = argv[0]
    ch = int(argv[1]) if len(argv) > 1 else 1
    rate = int(argv[2]) if len(argv) > 2 else 44100
    secs = float(argv[3]) if len(argv) > 3 else 0.25
    data = make_silence(ch, rate, secs)
    with open(out, "wb") as f:
        f.write(data)
    print(f"wrote {out} ({len(data):,} bytes, {ch}ch {rate}Hz {secs}s silence)")


def main():
    cmds = {"fmt": _cmd_fmt, "silence": _cmd_silence}
    if len(sys.argv) < 3 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(2)
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
