# Crimson Desert PAC Browser

A desktop tool for browsing, previewing, and exporting 3D models from Crimson Desert game archives.

Reads PAC mesh files directly from the game's PAZ archives, displays them in an interactive 3D viewer, and exports to OBJ + MTL with DDS textures for use in **Blender**.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![PySide6](https://img.shields.io/badge/PySide6-Qt6-green)
![OpenGL](https://img.shields.io/badge/OpenGL-3.3-orange)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Usage](#usage)
  - [GUI Browser](#gui-browser)
  - [Command-line Export](#command-line-export)
  - [Tests](#tests)
- [How It Works](#how-it-works)
- [Known Limitations](#known-limitations)
- [Acknowledgments](#acknowledgments)

---

## Features

- **Model browser** - searchable list of all PAC files in the game (12,000+ models)
- **3D preview** - real-time OpenGL viewer with orbit camera (rotate, pan, zoom)
- **OBJ + MTL export** - full geometry with material references and DDS textures


## Requirements

- Python 3.10 or newer
- Crimson Desert installed (needs access to game archives in `0009/`)
- [lazorr410/crimson-desert-unpacker](https://github.com/lazorr410/crimson-desert-unpacker) (included as git submodule)

## Quick Start

### 1. Clone this repo (with submodules)

```bash
git clone --recursive https://github.com/Altair200333/CrimsonDesertPacBrowser.git
cd CrimsonDesertPacBrowser
```

If you already cloned without `--recursive`, pull the submodule manually:

```bash
git submodule update --init --recursive
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

This installs PySide6, PyOpenGL, numpy, lz4, and cryptography. See [requirements.txt](requirements.txt) for exact versions.

### 3. Run

```bash
python pac_browser.py
```

On first launch you will be asked to locate your Crimson Desert installation folder. This is saved to `pac_browser.ini` and remembered for next time.

---

## Usage

### GUI Browser

```bash
python pac_browser.py
```

The left panel shows all PAC models from the game. Type in the search bar to filter by name. Click a model to load it in the 3D viewer.

**Controls:**

| Action | Input |
|--------|-------|
| Rotate camera | Left-click drag |
| Pan camera | Middle-click drag |
| Zoom | Scroll wheel |
| Export model | Ctrl+E or File > Export Model |
| Change game folder | File > Change Game Directory |

Export creates a folder with the OBJ file, MTL file, and a `textures/` subfolder containing all referenced DDS textures.

### Command-line Export

For scripting or batch workflows, use `pac_export.py` directly:

```bash
# Export a single PAC file
python pac_export.py path/to/model.pac -o output_folder

# Batch export all matching files from game archives
python pac_export.py --batch --filter "cd_phw_00_ub" -o output_folder
```

### Tests

```bash
python test_pac.py
```

Runs 19 tests that validate geometry parsing against known-good models. Requires access to the game archives.

---

## How It Works

PAC files store skinned meshes used for characters, armor, and weapons. Each file contains:

- **Section 0** -- metadata: mesh names, material names, bounding boxes, bone references
- **Sections 1-4** -- LOD geometry (low to high detail), with vertices and indices in a combined buffer

Vertex format is 40 bytes per vertex: quantized position (uint16 x3), UVs (float16 x2), normals (R10G10B10A2), bone indices, and bone weights. Positions are dequantized using per-mesh center and half-extent values stored in the mesh descriptor.

The tool reads these directly from PAZ archives using the PAMT file index, decompresses type 1 sections (per-section LZ4), and reconstructs the mesh geometry.

## Known Limitations

- About 2% of models with secondary physics data may show a few stray triangles
- No rigging or bone hierarchy export yet
- Some DDS textures are streaming-only and not stored in full resolution
- No write-back or mod creation support yet

## Acknowledgments

- [lazorr410/crimson-desert-unpacker](https://github.com/lazorr410/crimson-desert-unpacker) -- PAZ archive parser and unpacker
- [Lathiel/crimson-desert-pam-extractor](https://github.com/Lathiel/crimson-desert-pam-extractor) -- reference for PAM format dequantization
