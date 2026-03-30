"""Export Crimson Desert PAC skinned meshes to OBJ + MTL.

Parses the PAC binary format (PAR header, 5 sections), extracts mesh geometry
from the highest-detail LOD, and writes Wavefront OBJ + MTL with DDS texture
references.

Usage:
    # Export a single PAC file already on disk
    python pac_export.py path/to/mesh.pac -o output_dir/

    # Export directly from a PAZ archive (requires paz_parse/paz_unpack on sys.path)
    python pac_export.py --paz-dir F:/games/CrimsonDesert/0009 --filter "cd_phw_00_ub_00_0163" -o output/

    # Batch export all uncompressed PHW upper body PACs
    python pac_export.py --paz-dir F:/games/CrimsonDesert/0009 --filter "cd_phw_*_ub_*.pac" -o output/ --batch
"""

import os
import sys
import struct
import argparse
from dataclasses import dataclass, field

try:
    import lz4.block
    HAS_LZ4 = True
except ImportError:
    HAS_LZ4 = False


# ── Data structures ─────────────────────────────────────────────────

@dataclass
class MeshDescriptor:
    """Per-mesh metadata from PAC section 0."""
    display_name: str           # 1st name string (mesh name)
    material_name: str          # 2nd name string (texture base name)
    center: tuple               # (cx, cy, cz) for position dequantization
    half_extent: tuple          # (hx, hy, hz) for position dequantization
    vertex_counts: list         # [lod0, lod1, lod2, lod3] per LOD
    index_counts: list          # [lod0, lod1, lod2, lod3] per LOD
    bbox_unknowns: tuple = ()   # float[0:2], purpose unknown


@dataclass
class Vertex:
    """Decoded vertex data."""
    pos: tuple      # (x, y, z) world-space
    uv: tuple       # (u, v) texture coordinates
    normal: tuple   # (nx, ny, nz) unit normal


@dataclass
class Mesh:
    """Single submesh with geometry and material."""
    name: str
    material: str
    vertices: list = field(default_factory=list)
    indices: list = field(default_factory=list)


# ── PAC parsing ─────────────────────────────────────────────────────

def decompress_type1_pac(raw_data: bytes, orig_size: int) -> bytes:
    """Decompress a type 1 PAC file with internal section-level LZ4.

    Type 1 files store the 80-byte header uncompressed, but individual
    sections may be LZ4 block compressed. The section size table uses
    (u32 comp_size, u32 decomp_size) per slot.
    """
    if not HAS_LZ4:
        raise RuntimeError("lz4 package required for type 1 decompression: pip install lz4")

    output = bytearray(raw_data[:0x50])  # copy 80-byte header
    file_offset = 0x50

    for slot in range(8):
        off = 0x10 + slot * 8
        comp = struct.unpack_from('<I', raw_data, off)[0]
        decomp = struct.unpack_from('<I', raw_data, off + 4)[0]
        if decomp == 0:
            continue
        if comp > 0:
            blob = raw_data[file_offset:file_offset + comp]
            output.extend(lz4.block.decompress(blob, uncompressed_size=decomp))
            file_offset += comp
        else:
            output.extend(raw_data[file_offset:file_offset + decomp])
            file_offset += decomp

    # Fix header: set all comp_size fields to 0 (output is fully decompressed)
    for slot in range(8):
        struct.pack_into('<I', output, 0x10 + slot * 8, 0)

    return bytes(output)


def parse_header(data: bytes) -> dict:
    """Parse 80-byte PAC header. Returns section sizes and offsets."""
    magic = data[0:4]
    if magic != b'PAR ':
        raise ValueError(f"Not a PAC file (magic: {magic!r}, expected b'PAR ')")

    version = struct.unpack_from('<I', data, 4)[0]

    # 8 section slots at offset 0x10, each 8 bytes: [u32_comp_size, u32_decomp_size]
    # If comp_size > 0, section was LZ4 compressed (should be decompressed first)
    # After decompression, comp_size fields are set to 0
    sections = []
    offset = 0x50  # first section starts right after header
    for i in range(8):
        slot_off = 0x10 + i * 8
        comp_size = struct.unpack_from('<I', data, slot_off)[0]
        decomp_size = struct.unpack_from('<I', data, slot_off + 4)[0]
        stored_size = comp_size if comp_size > 0 else decomp_size
        if decomp_size > 0:
            sections.append({'index': i, 'offset': offset, 'size': decomp_size})
            offset += stored_size

    return {'version': version, 'sections': sections}


ATTR4_PATTERN = bytes([0x04, 0x00, 0x01, 0x02, 0x03])  # 4-attribute meshes (standard)
ATTR3_PATTERN = bytes([0x03, 0x00, 0x01, 0x02])         # 3-attribute meshes (cloth/sim)


def find_mesh_descriptors(data: bytes, sec0_offset: int, sec0_size: int) -> list[MeshDescriptor]:
    """Find all per-mesh descriptors in section 0 by pattern matching.

    Finds both 4-attribute [04 00 01 02 03] and 3-attribute [03 00 01 02]
    meshes. Returns them sorted by position in section 0 (matching vertex
    buffer order in geometry sections). 3-attr meshes have 3 LODs only.
    """
    region = data[sec0_offset:sec0_offset + sec0_size]
    found = []  # (offset_in_region, MeshDescriptor)

    # Find 4-attribute descriptors (standard meshes, 4 LODs)
    pos = 0
    while True:
        idx = region.find(ATTR4_PATTERN, pos)
        if idx == -1:
            break
        desc_start = idx - 35
        if desc_start >= 0 and region[desc_start] == 0x01:
            floats = struct.unpack_from('<8f', region, desc_start + 3)
            vc = [struct.unpack_from('<H', region, desc_start + 40 + i * 2)[0] for i in range(4)]
            ic = [struct.unpack_from('<I', region, desc_start + 48 + i * 4)[0] for i in range(4)]
            names = _find_name_strings(region, desc_start)
            found.append((desc_start, MeshDescriptor(
                display_name=names[0], material_name=names[1],
                center=(floats[2], floats[3], floats[4]),
                half_extent=(floats[5], floats[6], floats[7]),
                vertex_counts=vc, index_counts=ic,
                bbox_unknowns=(floats[0], floats[1]),
            )))
        pos = idx + 5

    # Find 3-attribute descriptors (cloth/sim meshes, 3 LODs)
    pos = 0
    while True:
        idx = region.find(ATTR3_PATTERN, pos)
        if idx == -1:
            break
        desc_start = idx - 35
        if desc_start >= 0 and region[desc_start] == 0x01:
            # Skip false positives inside 4-attr patterns
            if idx >= 1 and region[idx - 1] == 0x04:
                pos = idx + 4
                continue
            floats = struct.unpack_from('<8f', region, desc_start + 3)
            vc3 = [struct.unpack_from('<H', region, desc_start + 40 + i * 2)[0] for i in range(3)]
            ic3 = [struct.unpack_from('<I', region, desc_start + 46 + i * 4)[0] for i in range(3)]
            vc = vc3 + [0]  # pad to 4 LODs
            ic = ic3 + [0]
            names = _find_name_strings(region, desc_start)
            found.append((desc_start, MeshDescriptor(
                display_name=names[0], material_name=names[1],
                center=(floats[2], floats[3], floats[4]),
                half_extent=(floats[5], floats[6], floats[7]),
                vertex_counts=vc, index_counts=ic,
                bbox_unknowns=(floats[0], floats[1]),
            )))
        pos = idx + 4

    # Sort by position in section 0 to match vertex buffer order
    found.sort(key=lambda x: x[0])
    return [desc for _, desc in found]


def _find_name_strings(region: bytes, desc_start: int) -> tuple[str, str]:
    """Extract two length-prefixed name strings before a descriptor.

    Walks backwards from desc_start to find two consecutive
    [u8_len][ASCII_chars] entries.
    """
    # The material name ends right at desc_start.
    # Read its length byte, then the display name before it.
    names = []
    cursor = desc_start

    for _ in range(2):
        # The string occupies [cursor - length ... cursor)
        # and its length byte is at [cursor - length - 1]
        # Search backwards for a plausible length byte
        found = False
        for back in range(1, 200):
            candidate_len = region[cursor - back]
            if candidate_len == 0:
                continue
            # Check if this length byte accounts for exactly the right span
            if candidate_len == back - 1:
                name_bytes = region[cursor - back + 1:cursor]
                # Validate: should be printable ASCII
                try:
                    name = name_bytes.decode('ascii')
                    if all(32 <= c < 127 for c in name_bytes):
                        names.append(name)
                        cursor = cursor - back
                        found = True
                        break
                except (UnicodeDecodeError, ValueError):
                    continue
        if not found:
            names.append(f"unknown_{desc_start:x}")

    # We found material_name first (closest to descriptor), then display_name
    names.reverse()
    return (names[0], names[1])


# ── Vertex decoding ─────────────────────────────────────────────────

def decode_vertices(data: bytes, section_offset: int,
                    vertex_count: int, desc: MeshDescriptor,
                    vertex_start: int = 0) -> list[Vertex]:
    """Decode vertices from a geometry section.

    Args:
        data: full PAC file bytes
        section_offset: byte offset of the geometry section
        vertex_count: number of vertices for this mesh
        desc: mesh descriptor with center/half_extent for dequantization
        vertex_start: byte offset within the section where this mesh's verts begin
    """
    STRIDE = 40
    cx, cy, cz = desc.center
    hx, hy, hz = desc.half_extent
    base = section_offset + vertex_start
    verts = []

    for i in range(vertex_count):
        vo = base + i * STRIDE

        # Position: 3 × uint16 at +0, dequantize with per-mesh bbox
        px, py, pz = struct.unpack_from('<HHH', data, vo)
        x = cx + (px / 32767.0) * hx
        y = cy + (py / 32767.0) * hy
        z = cz + (pz / 32767.0) * hz

        # UV: 2 × float16 at +8
        u, v = struct.unpack_from('<ee', data, vo + 8)

        # Normal: R10G10B10A2_UNORM at +16, axes permuted (Y,Z,X)
        packed = struct.unpack_from('<I', data, vo + 16)[0]
        nx_raw = (packed >> 0) & 0x3FF
        ny_raw = (packed >> 10) & 0x3FF
        nz_raw = (packed >> 20) & 0x3FF
        # UNORM → signed float, then permute Y,Z,X
        nx = ny_raw / 511.5 - 1.0
        ny = nz_raw / 511.5 - 1.0
        nz = nx_raw / 511.5 - 1.0

        verts.append(Vertex(pos=(x, y, z), uv=(float(u), float(v)), normal=(nx, ny, nz)))

    return verts


def decode_indices(data: bytes, section_offset: int,
                   index_count: int, total_verts_before: int,
                   index_start: int = 0) -> list[int]:
    """Decode triangle indices from a geometry section.

    Args:
        index_start: byte offset within the section where this mesh's indices begin
                     (after ALL vertex data for ALL meshes)
    """
    base = section_offset + index_start
    indices = []
    for i in range(index_count):
        idx = struct.unpack_from('<H', data, base + i * 2)[0]
        indices.append(idx)
    return indices


# ── Material / texture naming ───────────────────────────────────────

def material_to_dds_basename(mat_name: str) -> str:
    """Convert material name to DDS texture base filename.

    PHW body parts (nude/head) need '_00_' inserted before the numeric ID.
    All other types map directly via lowercase.
    """
    lower = mat_name.lower()

    # PHW body exception: cd_phw_00_nude_XXXX or cd_phw_00_head_XXXX
    if lower.startswith('cd_phw_00_nude_') or lower.startswith('cd_phw_00_head_'):
        prefix_end = len('cd_phw_00_')
        rest = lower[prefix_end:]
        parts = rest.split('_')
        # Insert '00' before the first 4-digit numeric part
        for i, p in enumerate(parts):
            if p.isdigit() and len(p) == 4:
                parts.insert(i, '00')
                break
        return 'cd_phw_00_' + '_'.join(parts)

    return lower


# ── OBJ + MTL writing ──────────────────────────────────────────────

def write_mtl(meshes: list[Mesh], mtl_path: str, texture_rel_dir: str = "",
              available_textures: set = None):
    """Write Wavefront MTL file with DDS texture references.

    Args:
        available_textures: if provided, only reference textures in this set (lowercase basenames).
                           If None, reference all textures unconditionally.
    """
    with open(mtl_path, 'w') as f:
        f.write(f"# Materials for {os.path.basename(mtl_path).replace('.mtl', '')}\n\n")

        seen = set()
        for mesh in meshes:
            if mesh.material in seen or mesh.material == "(null)":
                continue
            seen.add(mesh.material)

            dds_base = material_to_dds_basename(mesh.material)
            tex_prefix = (".\\" + texture_rel_dir + "\\" + dds_base) if texture_rel_dir else dds_base

            f.write(f"newmtl {mesh.material}\n")
            f.write("Ka 0.2 0.2 0.2\n")
            f.write("Kd 0.8 0.8 0.8\n")
            f.write("Ks 0.5 0.5 0.5\n")
            f.write("Ns 100.0\n")

            # Only reference textures that exist (if we know what's available)
            suffixes = [("map_Kd", ""), ("bump", "_n"), ("map_Ks", "_sp")]
            for mtl_key, suffix in suffixes:
                dds_name = f"{dds_base}{suffix}.dds"
                if available_textures is None or dds_name in available_textures:
                    f.write(f"{mtl_key} {tex_prefix}{suffix}.dds\n")

            f.write("\n")


def write_obj(meshes: list[Mesh], obj_path: str, mtl_filename: str):
    """Write Wavefront OBJ file. Y/Z swapped for Blender (Z-up)."""
    with open(obj_path, 'w') as f:
        f.write(f"# Crimson Desert PAC export\n")
        f.write(f"mtllib {mtl_filename}\n\n")

        vert_offset = 0  # OBJ indices are 1-based and global across all meshes

        for mesh in meshes:
            f.write(f"o {mesh.name}\n")
            f.write(f"usemtl {mesh.material}\n")

            # Positions as-is (no axis swap)
            for v in mesh.vertices:
                x, y, z = v.pos
                f.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")

            # Normals as-is
            for v in mesh.vertices:
                nx, ny, nz = v.normal
                f.write(f"vn {nx:.6f} {ny:.6f} {nz:.6f}\n")

            # UVs — flip V
            for v in mesh.vertices:
                u, v_coord = v.uv
                f.write(f"vt {u:.6f} {1.0 - v_coord:.6f}\n")

            # Faces (triangles), 1-based with offset for prior meshes
            for i in range(0, len(mesh.indices), 3):
                i0 = mesh.indices[i] + vert_offset + 1
                i1 = mesh.indices[i + 1] + vert_offset + 1
                i2 = mesh.indices[i + 2] + vert_offset + 1
                f.write(f"f {i0}/{i0}/{i0} {i1}/{i1}/{i1} {i2}/{i2}/{i2}\n")

            vert_offset += len(mesh.vertices)
            f.write("\n")


# ── Main export logic ───────────────────────────────────────────────

def export_pac(pac_data: bytes, output_dir: str, name_hint: str = "",
               texture_rel_dir: str = "", lod: int = 0) -> dict:
    """Export a PAC file to OBJ + MTL.

    Args:
        pac_data: raw PAC file bytes
        output_dir: directory for output files
        name_hint: optional name for output files (default: first mesh name)
        texture_rel_dir: relative path from OBJ to texture directory
        lod: LOD level to export (0=highest detail, 3=lowest)

    Returns:
        dict with export stats
    """
    header = parse_header(pac_data)

    # Map section indices to our sections dict
    sec_by_idx = {s['index']: s for s in header['sections']}
    if 0 not in sec_by_idx:
        raise ValueError("No section 0 (metadata) found")

    sec0 = sec_by_idx[0]

    # LOD 0 → section 4, LOD 1 → section 3, etc.
    geom_section_idx = 4 - lod
    if geom_section_idx not in sec_by_idx:
        raise ValueError(f"No geometry section for LOD {lod} (section {geom_section_idx})")
    geom_sec = sec_by_idx[geom_section_idx]

    # Find mesh descriptors
    descriptors = find_mesh_descriptors(pac_data, sec0['offset'], sec0['size'])
    if not descriptors:
        raise ValueError("No mesh descriptors found in section 0")

    # Build meshes from geometry section
    # Layout: [all verts][possibly extra data][all indices at section end]
    total_verts = sum(d.vertex_counts[lod] for d in descriptors)
    total_indices = sum(d.index_counts[lod] for d in descriptors)

    meshes = []
    vert_byte_offset = 0
    index_byte_offset = geom_sec['size'] - total_indices * 2  # indices at end of section

    for desc in descriptors:
        vc = desc.vertex_counts[lod]
        ic = desc.index_counts[lod]

        if vc == 0:
            continue

        vertices = decode_vertices(
            pac_data, geom_sec['offset'], vc, desc,
            vertex_start=vert_byte_offset
        )
        indices = decode_indices(
            pac_data, geom_sec['offset'], ic, 0,
            index_start=index_byte_offset
        )

        meshes.append(Mesh(
            name=desc.display_name,
            material=desc.material_name,
            vertices=vertices,
            indices=indices,
        ))

        vert_byte_offset += vc * 40
        index_byte_offset += ic * 2

    if not meshes:
        raise ValueError("No meshes with geometry found")

    # Output filenames
    base_name = name_hint or meshes[0].name.lower()
    base_name = base_name.replace(' ', '_')
    obj_filename = base_name + '.obj'
    mtl_filename = base_name + '.mtl'

    os.makedirs(output_dir, exist_ok=True)
    obj_path = os.path.join(output_dir, obj_filename)
    mtl_path = os.path.join(output_dir, mtl_filename)

    write_obj(meshes, obj_path, mtl_filename)
    write_mtl(meshes, mtl_path, texture_rel_dir)

    total_verts_out = sum(len(m.vertices) for m in meshes)
    total_tris = sum(len(m.indices) // 3 for m in meshes)

    return {
        'obj': obj_path,
        'mtl': mtl_path,
        'meshes': len(meshes),
        'vertices': total_verts_out,
        'triangles': total_tris,
        'names': [m.name for m in meshes],
    }


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Export Crimson Desert PAC meshes to OBJ + MTL")
    parser.add_argument("pac_file", nargs='?', help="Path to .pac file on disk")
    parser.add_argument("-o", "--output", default=".", help="Output directory (default: current)")
    parser.add_argument("--name", help="Output filename base (default: from mesh name)")
    parser.add_argument("--textures", default="", help="Relative path from OBJ to textures dir")
    parser.add_argument("--lod", type=int, default=0, choices=[0,1,2,3],
                        help="LOD level: 0=highest detail (default), 3=lowest")

    # PAZ archive mode
    parser.add_argument("--paz-dir", help="Game directory with 0.pamt (e.g. .../0009)")
    parser.add_argument("--filter", help="Filter PAC files by glob/substring in PAZ mode")
    parser.add_argument("--batch", action="store_true",
                        help="Export all matching files (not just first match)")

    args = parser.parse_args()

    if args.pac_file:
        # Direct file mode
        with open(args.pac_file, 'rb') as f:
            pac_data = f.read()

        name = args.name or os.path.splitext(os.path.basename(args.pac_file))[0]
        result = export_pac(pac_data, args.output, name_hint=name,
                           texture_rel_dir=args.textures, lod=args.lod)

        print(f"Exported {result['meshes']} mesh(es): {result['vertices']} verts, {result['triangles']} tris")
        for n in result['names']:
            print(f"  - {n}")
        print(f"  {result['obj']}")
        print(f"  {result['mtl']}")

    elif args.paz_dir:
        # PAZ archive mode — requires lazorr410 unpacker
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lazorr410-unpacker', 'python'))
            from paz_parse import parse_pamt
            from paz_unpack import extract_entry
        except ImportError:
            print("Error: paz_parse/paz_unpack not found. Ensure lazorr410-unpacker is cloned.", file=sys.stderr)
            sys.exit(1)

        pamt_path = os.path.join(args.paz_dir, '0.pamt')
        if not os.path.exists(pamt_path):
            print(f"Error: {pamt_path} not found", file=sys.stderr)
            sys.exit(1)

        print(f"Parsing {pamt_path}...")
        entries = parse_pamt(pamt_path, paz_dir=args.paz_dir)

        # Filter to PAC files matching the pattern (uncompressed + type 1 supported)
        import fnmatch
        pattern = (args.filter or "*.pac").lower()
        matches = [
            e for e in entries
            if e.path.lower().endswith('.pac')
            and (not e.compressed or e.compression_type == 1)
            and (fnmatch.fnmatch(e.path.lower(), f"*{pattern}*")
                 or fnmatch.fnmatch(os.path.basename(e.path).lower(), pattern)
                 or pattern in e.path.lower())
        ]

        if not matches:
            print(f"No PAC files matching '{args.filter}'")
            sys.exit(1)

        if not args.batch:
            matches = matches[:1]

        print(f"Exporting {len(matches)} PAC file(s)...\n")

        for entry in matches:
            try:
                # Read raw PAC bytes from PAZ
                read_size = entry.comp_size if entry.compressed else entry.orig_size
                with open(entry.paz_file, 'rb') as f:
                    f.seek(entry.offset)
                    pac_data = f.read(read_size)

                # Decompress type 1 (internal section-level LZ4)
                if entry.compressed and entry.compression_type == 1:
                    pac_data = decompress_type1_pac(pac_data, entry.orig_size)

                pac_name = os.path.splitext(os.path.basename(entry.path))[0]
                result = export_pac(pac_data, args.output, name_hint=args.name or pac_name,
                                   texture_rel_dir=args.textures, lod=args.lod)

                print(f"{pac_name}: {result['meshes']} mesh(es), {result['vertices']} verts, {result['triangles']} tris")
                for n in result['names']:
                    print(f"    {n}")

            except Exception as e:
                print(f"  ERROR {os.path.basename(entry.path)}: {e}", file=sys.stderr)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
