# metro_vfx — Metro Exodus / Enhanced Edition modding toolkit

A small, dependency-free (Python 3 + ffmpeg for audio) toolkit for building
real asset-replacement mods for **Metro Exodus** and **Metro Exodus PC Enhanced
Edition** — the first known packer verified against the Enhanced Edition's
archives.

## Tools
| file | what it does |
|------|--------------|
| `metro_vfx.py` | list / extract / **repack** `.vfx`+`.vfs0` archives (the `content_99.vfs0` pattern) |
| `metro_tex.py` | convert native split-mip textures ⇄ BC7 DDS |
| `metro_mesh.py` | shrink / collapse a creature mesh (`vscale` edit) to make it small or invisible |
| `metro_audio.py` | replace a `.vba` sound (OGG/Vorbis) with format-matched silence |

## Examples
- `example_texturereplace.py` — the smallest complete texture mod, start here.
- `example_make_arachnophobia.py` — the full multi-layer Arachnophobia mod.

## Learn the format
`TUTORIAL.md` documents the whole archive + texture + mesh + audio format,
reverse-engineered and verified byte-for-byte against live game data.

## Quick start
```
python example_texturereplace.py "<game_dir>" "content\textures\tile\tile_pautina.2048" out
```
Back up the game's `content.vfx` to `content.vfx.bak`, drop the produced
`content.vfx` + `content_99.vfs0` into the game folder, play. Uninstall =
restore the backup and delete `content_99.vfs0`.
