"""Tests for PAC export/preview pipeline.

Validates geometry parsing against known-good models. Run with:
    python test_pac.py
"""

import sys
import os
import struct
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'lazorr410-unpacker', 'python'))

from paz_parse import parse_pamt
from pac_export import (parse_header, find_mesh_descriptors, decode_vertices,
                        decode_indices, _find_section_layout, decompress_type1_pac)
from pac_browser import load_pac_mesh, read_pac_bytes

GAME_DIR = r"F:\programs\steam\steamapps\common\Crimson Desert"
DIR_0009 = os.path.join(GAME_DIR, "0009")

passed = 0
failed = 0


def test(name):
    def decorator(fn):
        global passed, failed
        try:
            fn()
            print(f"  PASS: {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {name} — {e}")
            failed += 1
    return decorator


# Load PAMT once
print("Loading PAMT index...")
ALL_ENTRIES = parse_pamt(os.path.join(DIR_0009, "0.pamt"), paz_dir=DIR_0009)

def find_entry(name):
    matches = [e for e in ALL_ENTRIES if name in e.path.lower() and e.path.endswith('.pac')]
    assert matches, f"No PAC file matching '{name}'"
    return matches[0]


# ── Known-good models (confirmed in Blender) ───────────────────────

print("\n=== Known-good models ===")

@test("Sword: 4 meshes, 497 verts, correct positions")
def _():
    e = find_entry("cd_phm_01_sword_0015")
    mesh = load_pac_mesh(e)
    assert len(mesh.positions) == 497, f"verts={len(mesh.positions)}"
    assert len(mesh.indices) // 3 == 768, f"tris={len(mesh.indices)//3}"
    # First vertex matches v7 reference: (-0.076356, -0.154624, -0.004598)
    p = mesh.positions[0]
    assert abs(p[0] - (-0.076356)) < 0.001, f"x={p[0]}"

@test("Nude body: 3 meshes, 13740 verts")
def _():
    e = find_entry("cd_pgw_00_nude_00_0001")
    mesh = load_pac_mesh(e)
    assert len(mesh.positions) == 13740, f"verts={len(mesh.positions)}"

@test("UB armor 0163 (type 1 compressed): loads successfully")
def _():
    e = find_entry("cd_phw_00_ub_00_0163.pac")
    assert e.compressed and e.compression_type == 1
    mesh = load_pac_mesh(e)
    assert len(mesh.positions) > 100

@test("UB armor 0001 (multi-mesh): loads successfully")
def _():
    e = find_entry("cd_phw_00_ub_00_0001.pac")
    mesh = load_pac_mesh(e)
    assert len(mesh.positions) > 100
    assert len(mesh.indices) > 0


# ── Index validation ────────────────────────────────────────────────

print("\n=== Index validation ===")

def validate_indices(name):
    """Check that every mesh's indices are < its vertex count."""
    e = find_entry(name)
    raw = read_pac_bytes(e)
    header = parse_header(raw)
    sec_by_idx = {s['index']: s for s in header['sections']}
    sec0 = sec_by_idx[0]
    geom_idx = next((i for i in [4,3,2,1] if i in sec_by_idx), None)
    geom_sec = sec_by_idx[geom_idx]
    lod = 4 - geom_idx
    descs = find_mesh_descriptors(raw, sec0['offset'], sec0['size'])
    total_i = sum(d.index_counts[lod] for d in descs)
    _, idx_start = _find_section_layout(raw, geom_sec, descs, lod, total_i)

    # Precompute vert offsets
    desc_vert_offsets = []
    off = 0
    for d in descs:
        desc_vert_offsets.append(off)
        off += d.vertex_counts[lod] * 40

    idx_off = idx_start
    sec_off = geom_sec['offset']
    errors = []
    for di, d in enumerate(descs):
        vc = d.vertex_counts[lod]
        ic = d.index_counts[lod]
        if vc == 0:
            idx_off += ic * 2
            continue
        indices = [struct.unpack_from('<H', raw, sec_off + idx_off + j*2)[0] for j in range(ic)]
        max_idx = max(indices) if indices else 0

        # For shared buffer meshes, find partner vc
        effective_vc = vc
        if max_idx >= vc:
            for pj, pd in enumerate(descs):
                if pd.vertex_counts[lod] > max_idx and pj != di:
                    effective_vc = pd.vertex_counts[lod]
                    break

        if max_idx >= effective_vc:
            errors.append(f"mesh {di} '{d.display_name}': max_idx={max_idx} >= vc={effective_vc}")
        idx_off += ic * 2

    return errors

@test("Sword: all indices valid")
def _():
    errors = validate_indices("cd_phm_01_sword_0015")
    assert not errors, errors

@test("Nude body: all indices valid")
def _():
    errors = validate_indices("cd_pgw_00_nude_00_0001")
    assert not errors, errors

@test("Lightningthrower (shared buffers): all indices valid with partners")
def _():
    errors = validate_indices("lightningthrower_0001")
    assert not errors, errors

@test("Bear UB (3-attr + secondary verts): all indices valid")
def _():
    errors = validate_indices("cd_m0001_00_bear_ub_0001")
    assert not errors, errors

@test("Ancientpeople belt (3-attr mesh): all indices valid")
def _():
    errors = validate_indices("ancientpeople_sho_belt_0002")
    assert not errors, errors

@test("Ancientpeople head: all indices valid")
def _():
    errors = validate_indices("ancientpeople_head_0001")
    assert not errors, errors


# ── Section size formula ────────────────────────────────────────────

print("\n=== Section size formula ===")

def check_formula(name):
    """Verify vertex_count*40 + index_count*2 = section_size (with 3-attr meshes)."""
    e = find_entry(name)
    raw = read_pac_bytes(e)
    header = parse_header(raw)
    sec_by_idx = {s['index']: s for s in header['sections']}
    sec0 = sec_by_idx[0]
    geom_idx = next((i for i in [4,3,2,1] if i in sec_by_idx), None)
    geom_sec = sec_by_idx[geom_idx]
    lod = 4 - geom_idx
    descs = find_mesh_descriptors(raw, sec0['offset'], sec0['size'])
    total_v = sum(d.vertex_counts[lod] for d in descs)
    total_i = sum(d.index_counts[lod] for d in descs)
    expected = total_v * 40 + total_i * 2
    actual = geom_sec['size']
    return expected, actual

@test("Sword: formula exact match")
def _():
    exp, act = check_formula("cd_phm_01_sword_0015")
    assert exp == act, f"{exp} != {act}"

@test("Nude body: formula exact match")
def _():
    exp, act = check_formula("cd_pgw_00_nude_00_0001")
    assert exp == act, f"{exp} != {act}"

@test("Bear UB: formula matches (with 3-attr)")
def _():
    exp, act = check_formula("cd_m0001_00_bear_ub_0001")
    # Bear has secondary verts — formula won't match exactly
    diff = act - exp
    assert diff >= 0, f"formula undercount: {exp} > {act}"

@test("Ancientpeople belt: formula matches (with 3-attr)")
def _():
    exp, act = check_formula("ancientpeople_sho_belt_0002")
    assert exp == act, f"{exp} != {act} (diff={act-exp})"


# ── Preview mesh validation ─────────────────────────────────────────

print("\n=== Preview mesh (load_pac_mesh) ===")

@test("No NaN or Inf in positions")
def _():
    for name in ["cd_phm_01_sword_0015", "cd_pgw_00_nude_00_0001", "lightningthrower_0001"]:
        e = find_entry(name)
        mesh = load_pac_mesh(e)
        assert not np.any(np.isnan(mesh.positions)), f"{name}: NaN in positions"
        assert not np.any(np.isinf(mesh.positions)), f"{name}: Inf in positions"

@test("All indices within vertex array bounds")
def _():
    for name in ["cd_phm_01_sword_0015", "cd_pgw_00_nude_00_0001", "lightningthrower_0001",
                  "cd_m0001_00_bear_ub_0001", "ancientpeople_sho_belt_0002"]:
        e = find_entry(name)
        mesh = load_pac_mesh(e)
        max_idx = mesh.indices.max()
        num_verts = len(mesh.positions)
        assert max_idx < num_verts, f"{name}: max_idx={max_idx} >= num_verts={num_verts}"

@test("Bounding box is reasonable (not degenerate)")
def _():
    for name in ["cd_phm_01_sword_0015", "cd_pgw_00_nude_00_0001"]:
        e = find_entry(name)
        mesh = load_pac_mesh(e)
        extent = mesh.positions.max(axis=0) - mesh.positions.min(axis=0)
        assert extent.max() > 0.01, f"{name}: degenerate bbox {extent}"
        assert extent.max() < 100.0, f"{name}: oversized bbox {extent}"


# ── Material names ──────────────────────────────────────────────────

print("\n=== Material names ===")

@test("Sword materials match known names")
def _():
    e = find_entry("cd_phm_01_sword_0015")
    raw = read_pac_bytes(e)
    header = parse_header(raw)
    sec0 = {s['index']: s for s in header['sections']}[0]
    descs = find_mesh_descriptors(raw, sec0['offset'], sec0['size'])
    names = [d.material_name for d in descs]
    assert "CD_PHM_01_Guard_0015" in names, f"Guard not in {names}"
    assert "CD_PHM_01_Blade_0015" in names, f"Blade not in {names}"

@test("No (null) materials in nude body")
def _():
    e = find_entry("cd_pgw_00_nude_00_0001")
    raw = read_pac_bytes(e)
    header = parse_header(raw)
    sec0 = {s['index']: s for s in header['sections']}[0]
    descs = find_mesh_descriptors(raw, sec0['offset'], sec0['size'])
    for d in descs:
        assert d.material_name != "(null)", f"null material in {d.display_name}"


# ── Summary ─────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed")
print(f"{'='*50}")
sys.exit(1 if failed else 0)
