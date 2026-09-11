# Metro Exodus Texture & Asset Modding — the missing manual

*How retail Metro Exodus (2019) and Metro Exodus **PC Enhanced Edition** archives
actually work, and how to build real asset-replacement mods for them with the
`metro_vfx.py` toolkit — no closed-source tools, no guesswork.*

This documents the workflow behind the **Arachnophobia** mod (spider webs made
invisible) and generalizes it to any texture/asset replacement.

---

## 1. The archive system

A Metro Exodus install looks like this:

```
content.vfx              <- the index ("virtual file system" table of contents)
content_03.vfs0          <- data archives (dumb byte containers)
content_04.vfs0 ... .vfs7
content_13_textures.vfs0
content_43_textures_4k.vfs0
patch.vfx0               <- EE: a second, tiny index (shaders/credits only)
patch_01.vfx ...         <- original edition: patch indexes, override content.vfx
```

- **`.vfx` files are the only thing with structure.** They map virtual paths
  like `content\textures\tile\tile_pautina.512` to `(archive file, offset,
  size)` triples.
- **`.vfsN` files are raw concatenated data.** No headers, no directory — all
  meaning lives in the vfx.
- The engine loads `content.vfx`, then any patch vfx files; later mounts
  override earlier ones per-path. The **Enhanced Edition ships with patches
  already merged** — it has only `content.vfx` + `patch.vfx0` (which contains
  nothing but shader blobs and credits audio). That makes EE *easier* to mod
  than the original: patch `content.vfx` and you're done.
- There is **no signature/CRC check** on the vfx — the game happily loads a
  modified one (this is how all existing character-replacer mods work).

### vfx version 3 binary format (Exodus + EE, little-endian)

```
u32    version          = 3
u32    compression      = 1 (LZ4)
strz   contentVersion   e.g. "484107" (retail 2019), "src_521776-shdr_..." (EE)
u8[16] guid             identical across all retail archives
u32    numPackages
u32    numFiles
u32    numDuplicates    = 0
Package × numPackages:
    strz  name                     "content_04.vfs2"
    u32   numLevels; strz × N      level names this pak belongs to (may be 0)
    u32   chunk                    load-group id
Entry × numFiles:                  entry 0 = root folder; tree via ranges
    u16   flags                    bit 3 (value 8) set = folder
    file:   u16 pakIdx; u32 offset; u32 sizeUncompressed; u32 sizeCompressed
    folder: u16 numChildren; u32 firstChildIndex   (children are contiguous)
    xstr  name
8 bytes of zero padding at the end
```

`xstr` (scrambled string): `u16 header = (xorMask << 8) | (len+1)`, then `len`
bytes each XORed with `xorMask`, then a plain `00`. The mask is random per
string — pure obfuscation, no key.

**Compression:** if `sizeCompressed == sizeUncompressed` the file is stored
raw. Otherwise it's Metro's "stream" LZ4: repeating
`[u32 blockSize][u32 blockUncompressedSize][blockSize-8 bytes of raw LZ4]`
with ≤ 0x30000-byte blocks sharing one window. Mod files can always be stored
raw — the engine doesn't care.

## 2. The `content_99.vfs0` pattern

Community mods (character replacers etc.) all ship a modified `content.vfx`
plus a new `content_99.vfs0`. What their (unreleased) tools actually did:

1. Append one more **Package** record named `content_99.vfs0` to the vfx.
2. For each file to replace, rewrite its **Entry**: `pakIdx` → the new
   package, `offset`/sizes → the replacement bytes inside the new archive
   (stored uncompressed).
3. Concatenate the replacement files into `content_99.vfs0`.

Nothing else changes — every other byte offset in the game's 80 GB of data
stays valid because the original archives are never touched. `metro_vfx.py
pack` automates exactly this, for either edition.

## 3. Texture format

World textures are **BC7**, split by resolution for streaming, one *file per
resolution tier*, each file being a single-block **LZ4 "blob"** of raw BC7 data
(no DDS header):

| file        | contents                                   |
|-------------|--------------------------------------------|
| `name.2048` | one 2048×2048 BC7 mip                       |
| `name.1024` | one 1024×1024 BC7 mip                       |
| `name.512`  | full 10-mip chain, 512×512 → 1×1            |
| `name.4096` | EE 4K-pack tier, one mip (weapons etc.)     |

The game picks the top tier from your texture-quality setting, lower mips come
from the lower files — so **replace every tier a texture has** or it will pop
back at distance / lower settings. There is no per-file metadata: dimensions
and format are implied by the extension, which is why replacements must keep
the same resolution and BC7 format.

## 4. The toolkit

Three dependency-free Python 3 scripts (`tools/`):

### `metro_vfx.py` — archive swiss-army knife
```bash
python metro_vfx.py info    content.vfx                 # header + package list
python metro_vfx.py verify  content.vfx                 # parse + byte-identical rebuild self-test
python metro_vfx.py find    content.vfx "pautina"       # search paths (regex)
python metro_vfx.py extract content.vfx "tile_pautina[.](512|1024|2048)" out_dir
python metro_vfx.py pack    content.vfx my_mod_folder out_dir
```
`pack` takes a folder mirroring archive paths
(`my_mod_folder\content\textures\tile\tile_pautina.512`, …) and produces a
patched vfx + `content_99.vfs0` in `out_dir`. Works on `content.vfx` (EE and
original) and on `patch_01.vfx`-style indexes (pass e.g. `patch_01_99.vfs0` as
the pak name).

### `metro_tex.py` — native ⇄ DDS texture conversion
```bash
# native -> editable DDS (BC7, DX10 header; opens in GIMP/Photoshop/texconv)
python metro_tex.py to_dds out.dds name.512 name.1024 name.2048

# your art -> native files (DDS must be BC7 with a FULL mip chain)
texconv -f BC7_UNORM -m 0 my_texture.png        # Microsoft DirectXTex texconv
python metro_tex.py from_dds my_texture.dds content\textures\tile\tile_pautina
```

### `metro_mesh.py` — collapse a model to invisibility
```bash
python metro_mesh.py inspect  arahind.mesh           # list submesh vscales
python metro_mesh.py verify   arahind.mesh           # byte-identical roundtrip proof
python metro_mesh.py collapse arahind.mesh out.mesh  # shrink geometry to a point
```
A `.mesh` is a chunk tree; each submesh HEADER (chunk id 1) has a `float vscale`
at offset 56 that every vertex is multiplied by. Scaling it by `3e-4` collapses
the model to a sub-mm point — invisible in any shader, no black silhouette —
while the separate `.ph_model` keeps collision/AI, so a hidden creature stays
killable. `verify` proves the parse is non-destructive before you edit.

### `metro_audio.py` — silence a sound
```bash
python metro_audio.py fmt arachnid_mvt_loop.vba      # channels=1 rate=44100
python metro_audio.py silence out.vba 1 44100 0.25   # matched silent OGG
```
`.vba` files are raw OGG/Vorbis. Replace one with format-matched silence
(ffmpeg + libvorbis) and it plays nothing; a silent loop is silent.

### `make_arachnophobia.py` — worked example / ready mod
Three toggleable layers packed into one `content_99.vfs0`: transparent web
textures, collapsed spider meshes, and silenced spider audio (275 files, 2.9 MB).
Every replacement is verified byte-exact out of the built archive before install.

## 5. Recipe: replace any texture (either edition)

```bash
# 1. find it
python metro_vfx.py find "...\Metro Exodus Enhanced Edition\content.vfx" "kalash"

# 2. extract + convert to DDS
python metro_vfx.py extract ...\content.vfx "wpn_kalash_base[.](512|1024|2048)" work
python metro_tex.py to_dds work\kalash.dds work\content\textures\...\wpn_kalash_base.512 ...

# 3. edit in your image editor, keep the same resolution, save as PNG/TGA

# 4. back to native
texconv -f BC7_UNORM -m 0 edited.png
python metro_tex.py from_dds edited.dds mymod\content\textures\wpn\wpn_kalash_base

# 5. pack + install
python metro_vfx.py pack ...\content.vfx mymod out
#    backup game's content.vfx -> content.vfx.bak, copy out\* into game folder
```

**Original 2019 edition:** the game also loads `patch_01.vfx` (+ later
patches), which override `content.vfx`. If your target file also exists in a
patch index, patch the *last* vfx that contains it (run `find` against each),
using pak name `patch_01_99.vfs0`. This is why old mods shipped four files.

## 6. Cautions

- **Always back up the vfx you replace.** Steam → *Verify integrity* restores
  everything if anything breaks.
- A game update changes `content.vfx`; rebuild your mod against the new file
  (seconds — replacements are stored separately, nothing else to redo).
- Don't blank textures that are *part of* other surfaces — e.g.
  `wall34_kraskirpich_new_web*` is a brick wall with webs painted in; blanking
  it blanks the wall.
- Names ≤ 254 chars, offsets are u32 (keep any single `.vfsN` under 4 GB).

## 7. State of the ecosystem, prior art & credits

Why this toolkit exists — the tooling history as of late 2026:

**The mystery tool, solved.** Every `content.vfx + content_99.vfs0` mod on
Nexus traces back to **MetroPacker** — a *private* console tool by iOrange,
Arkady Baganin, Vladimir Goncharenko and Alexander Shubin that patches a
`content.vfx` and emits a new `.vfs0` from a `content\` folder. An old build
leaked in a Metro modding group around Feb 2023 (the first `content_99` mods
appeared on Nexus days later), and TSNest published the final build on
ap-pro.ru / playground.ru on 10 Mar 2023. A C# remake lives at
`github.com/tsnest/MetroPacker` (2026). It targets Redux + *original*
Exodus; it stores inserted files uncompressed — independent confirmation the
engine runs no CRC checks.

**The rest of the landscape:**

- **QuickBMS: no Metro Exodus script was ever published** (verified across
  aluigi's script archive, the full zenhax mirror, archived XeNTaX, ResHax) —
  QuickBMS reimport was never an option; 2019–2020 forum repack questions
  went unanswered.
- **iOrange — MetroEX / MetroTC / MetroTools**: the pioneering open-source
  format research this spec is verified against. iOrange deleted his repos
  (he evidently joined 4A around mid-2021; his farewell README: *"now it's
  public for posterity. And I have no hobby anymore :'("*). Surviving
  mirrors: `ShokerStlk/MetroEX`, `tsnest/MetroTC`, `tsnest/MetroTools`
  (MetroEX v0.52 binary is on ModDB). MetroTools also contains the
  never-released **MetroME** (model editor: OBJ/FBX → `.model` import — the
  path to *custom model* mods) and a MIT-licensed `VFXReader::SaveToFile`.
- **tsnest — MetroDeveloper** (`dinput8.dll`/`.asi`): dev console + *partial*
  loose-file loading (`unlock_content_folder`) for 2033/LL/Redux/Arktika.1
  and original Exodus, maintained through 2026. **Enhanced Edition is not
  supported** (author, Apr 2026: "someday, but not any time soon").
- **Why loose `.dds` texture overrides fail in original Exodus**: the engine
  requests textures by native name (`name.512`/`.1024`/`.2048`) — a loose
  `name.dds` is never asked for. Redux works around it with a loose
  per-texture `.bin` setting `streamable : bool = False;`; Exodus keeps
  texture params in a global database with no per-texture `.bin`. (Untested
  corollary: loose *native-format* files from `from_dds`, at exact archive
  paths, should be served by MetroDeveloper's vfs hook on the original
  edition.)
- **Official Exodus SDK** (free, Jan 2023, installs as "Metro Exodus __DEV"):
  builds standalone mod.io mods run in the SDK launcher — it cannot touch the
  retail campaign. (A *leaked* full SDK circulating since 2025 can build
  archives, GOG-only — outside this guide's scope.)
- **Enhanced Edition, specifically**: no public tool has ever advertised EE
  support, and users confirm original-edition packed mods fail on EE (its
  index has different content version and archive layout, and EE has no
  `patch_01.vfx`). Patching **EE's own** `content.vfx` in place — what this
  toolkit does — sidesteps all of that.

**Bottom line:** for the original edition there are working (if scattered)
options; for the **Enhanced Edition, in-place archive patching as documented
here is the only known modding route**, and this toolkit is, to our
knowledge, the first packer verified against EE's actual archives.
