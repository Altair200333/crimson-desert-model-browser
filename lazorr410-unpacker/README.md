# PAZ Unpacker

Tools for browsing, extracting, decrypting, and decompressing `.paz` archive files used by Crimson Desert.

## Features

- Parse PAMT index files and extract from associated PAZ archives
- ChaCha20 decryption with deterministic key derivation from filename
- LZ4 block decompression for compressed entries
- GUI for visual browsing and batch extraction
- No external dependencies — all crypto and compression implemented in-project

## Components

| Component | Language | Description |
|-----------|----------|-------------|
| `lib/` | C++17 | Core library: PAMT parsing, ChaCha20, LZ4, key derivation |
| `lib/paz_native.*` | C++17 | Shared library (DLL) with C API for P/Invoke |
| `gui/` | C# / Avalonia | Desktop GUI for browsing and extracting PAZ archives |
| `python/` | Python 3 | CLI tools for unpacking and repacking PAZ archives |
| `decrypt_info/` | Python 3 | Internal reference implementations and format documentation |

## Building

Requirements: CMake 3.15+, MSVC (Visual Studio), .NET 8 SDK

```bash
# 1. Configure and build the C++ library + native DLL
cmake -B build -S .
cmake --build build --config Release

# 2. Build the GUI
dotnet build gui/PazGui.csproj
```

### Release build

```bash
cmake --build build --config Release
dotnet publish gui/PazGui.csproj -c Release -r win-x64 --self-contained false -o publish
```

The `publish/` folder will contain `PazGui.exe` and `paz-native.dll`. Use `--self-contained true` to bundle the .NET runtime.

## Project Structure

```
lib/
├── paz.h                  # Umbrella header
├── PazTypes.h             # Shared types (FileEntry)
├── PamtFile.h/.cpp        # PAMT index parser
├── PamtExtractor.h/.cpp   # PAZ file extraction
├── CryptChaCha20.h/.cpp   # ChaCha20 cipher + key derivation (hashlittle)
├── Lz4.h/.cpp             # LZ4 block decompression
├── paz_native.h/.cpp      # C API (DLL exports for GUI)
gui/
├── Models/
│   ├── PamtParser.cs      # C# PAMT parser
│   ├── PamtExtractor.cs   # Extraction pipeline (decrypt + decompress)
│   └── PazNative.cs       # P/Invoke wrapper for paz-native.dll
├── ViewModels/             # MVVM view models
└── Views/                  # Avalonia XAML views
python/
├── README.md              # Python tools documentation
├── paz_crypto.py          # Shared library: key derivation, ChaCha20, LZ4
├── paz_parse.py           # PAMT index parser (list archive contents)
├── paz_unpack.py          # Extract, decrypt, decompress from PAZ archives
└── paz_repack.py          # Repack modified files back into PAZ archives
decrypt_info/
├── PAZ_DECRYPTION.md      # Full decryption documentation
├── paz_crypto.py          # Python reference: key derivation + ChaCha20
├── paz_decompress.py      # Python reference: LZ4 decompression
└── paz_repack.py          # Repack modified assets into PAZ archives
```

## Architecture

The C++ library (`lib/`) provides PAMT parsing, ChaCha20 decryption, and LZ4 decompression. A shared library target (`paz-native`) exposes these through a C API for the C# GUI to call via P/Invoke.

During extraction, encrypted XML files are decrypted using a key derived deterministically from the filename. Compressed entries (LZ4) are decompressed based on PAMT flags.

### PAMT entry flags

The PAMT `flags` field encodes the compression type at `(flags >> 16) & 0x0F`:

| Value | Compression |
|-------|-------------|
| 0 | None |
| 2 | LZ4 block (most common) |
| 3 | Custom engine compression |
| 4 | zlib |

An entry is compressed when `comp_size != orig_size`.

## Key Derivation

ChaCha20 keys are deterministic — derived entirely from the filename (lowercase basename, no directory prefix). No key database or runtime capture is needed.

The algorithm uses Bob Jenkins' `hashlittle` (lookup3) to seed the key and IV:

```
seed = hashlittle(basename.lower(), initval=0xC5EDE)

IV:  seed repeated 4 times (16 bytes)
Key: 8 × 4-byte chunks, each = (seed ^ 0x60616263) ^ delta[i]
     deltas = [0x00000000, 0x0A0A0A0A, 0x0C0C0C0C, 0x06060606,
               0x0E0E0E0E, 0x0A0A0A0A, 0x06060606, 0x02020202]
```

See [`PAZ_DECRYPTION.md`](PAZ_DECRYPTION.md) for the full specification, validation approach, and repacking workflow.

## Python Tools

Standalone CLI scripts for unpacking and repacking without the GUI. Requires `pip install cryptography lz4`.

```bash
# List archive contents
python python/paz_parse.py /path/to/0.pamt --paz-dir /path/to/0003

# Extract all files (with automatic decryption + decompression)
python python/paz_unpack.py /path/to/0.pamt --paz-dir /path/to/0003 -o output/

# Extract only XML files
python python/paz_unpack.py /path/to/0.pamt --paz-dir /path/to/0003 -o output/ --filter "*.xml"

# Repack a modified file into the PAZ archive
python python/paz_repack.py modified.xml --pamt /path/to/0.pamt --paz-dir /path/to/0003 \
    --entry "technique/rendererconfiguration.xml"
```

See [`python/README.md`](python/README.md) for full documentation.

## License

MIT — see [LICENSE](LICENSE).
