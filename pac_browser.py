"""Crimson Desert PAC Model Browser.

Interactive 3D browser for PAC skinned mesh files from Crimson Desert archives.
Left panel: searchable list of all PAC models. Right panel: OpenGL 3D preview.
First-run setup prompts user to locate game directory (saved to pac_browser.ini).
File → Export writes OBJ + MTL + DDS textures to a user-chosen folder.

Requirements:
    pip install PySide6 PyOpenGL numpy lz4 cryptography
"""

import os
import sys
import math
import configparser
import fnmatch
import numpy as np
from dataclasses import dataclass

# ── Path setup ──────────────────────────────────────────────────────

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TOOLS_DIR)
UNPACKER_DIR = os.path.join(PROJECT_DIR, "lazorr410-unpacker", "python")
sys.path.insert(0, UNPACKER_DIR)
sys.path.insert(0, TOOLS_DIR)

from paz_parse import parse_pamt, PazEntry
from paz_crypto import decrypt as paz_decrypt, lz4_decompress
from pac_export import (
    parse_header, find_mesh_descriptors, decode_vertices, decode_indices,
    decompress_type1_pac, export_pac, material_to_dds_basename, Vertex,
    write_obj, write_mtl, Mesh,
)

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QSplitter, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QLabel,
    QFileDialog, QMenuBar, QMessageBox,
)
from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QSurfaceFormat, QAction, QFont
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL.GL import *

# ── Settings ────────────────────────────────────────────────────────

INI_PATH = os.path.join(TOOLS_DIR, "pac_browser.ini")


def load_settings() -> dict:
    cfg = configparser.ConfigParser()
    if os.path.exists(INI_PATH):
        cfg.read(INI_PATH)
    return dict(cfg["pac_browser"]) if "pac_browser" in cfg else {}


def save_settings(**kwargs):
    cfg = configparser.ConfigParser()
    if os.path.exists(INI_PATH):
        cfg.read(INI_PATH)
    if "pac_browser" not in cfg:
        cfg["pac_browser"] = {}
    for k, v in kwargs.items():
        cfg["pac_browser"][k] = str(v)
    with open(INI_PATH, "w") as f:
        cfg.write(f)


def validate_game_dir(path: str) -> bool:
    """Check that the path looks like a Crimson Desert install."""
    return os.path.isfile(os.path.join(path, "0009", "0.pamt"))


# ── Data structures ─────────────────────────────────────────────────

@dataclass
class CatalogEntry:
    filename: str
    display_name: str
    paz_entry: PazEntry
    search_key: str


@dataclass
class GpuMesh:
    positions: "np.ndarray"
    normals: "np.ndarray"
    indices: "np.ndarray"
    center: "np.ndarray"
    radius: float


# ── Catalog builder ─────────────────────────────────────────────────

def build_catalog(game_dir: str) -> tuple[list[CatalogEntry], list[PazEntry]]:
    """Returns (catalog, all_pamt_entries). all_pamt_entries cached for texture export."""
    dir_0009 = os.path.join(game_dir, "0009")
    pamt_path = os.path.join(dir_0009, "0.pamt")
    all_entries = parse_pamt(pamt_path, paz_dir=dir_0009)

    pac_entries = [
        e for e in all_entries
        if e.path.lower().endswith('.pac')
        and (not e.compressed or e.compression_type == 1)
    ]

    catalog = []
    for e in pac_entries:
        fname = os.path.basename(e.path)
        stem = os.path.splitext(fname)[0]
        catalog.append(CatalogEntry(
            filename=fname, display_name=stem,
            paz_entry=e, search_key=stem.lower(),
        ))

    catalog.sort(key=lambda c: c.display_name.lower())
    return catalog, all_entries


# ── PAC loader (geometry for preview) ──────────────────────────────

def read_pac_bytes(entry: PazEntry) -> bytes:
    """Read and decompress raw PAC bytes from PAZ archive."""
    read_size = entry.comp_size if entry.compressed else entry.orig_size
    with open(entry.paz_file, 'rb') as f:
        f.seek(entry.offset)
        raw = f.read(read_size)
    if entry.compressed and entry.compression_type == 1:
        raw = decompress_type1_pac(raw, entry.orig_size)
    return raw


def load_pac_mesh(entry: PazEntry) -> GpuMesh:
    raw = read_pac_bytes(entry)
    header = parse_header(raw)
    sec_by_idx = {s['index']: s for s in header['sections']}

    if 0 not in sec_by_idx:
        raise ValueError("No metadata section")

    geom_sec, lod = None, 0
    for lod_idx in [4, 3, 2, 1]:
        if lod_idx in sec_by_idx:
            geom_sec = sec_by_idx[lod_idx]
            lod = 4 - lod_idx
            break
    if geom_sec is None:
        raise ValueError("No geometry sections")

    sec0 = sec_by_idx[0]
    descriptors = find_mesh_descriptors(raw, sec0['offset'], sec0['size'])
    if not descriptors:
        raise ValueError("No mesh descriptors")

    total_verts = sum(d.vertex_counts[lod] for d in descriptors)
    total_indices = sum(d.index_counts[lod] for d in descriptors)

    all_positions, all_normals, all_indices = [], [], []
    vert_offset = 0
    vert_byte_offset = 0
    idx_byte_offset = geom_sec['size'] - total_indices * 2  # indices at end of section

    for desc in descriptors:
        vc = desc.vertex_counts[lod]
        ic = desc.index_counts[lod]
        if vc == 0:
            continue

        vertices = decode_vertices(raw, geom_sec['offset'], vc, desc,
                                   vertex_start=vert_byte_offset)
        indices = decode_indices(raw, geom_sec['offset'], ic, 0,
                                index_start=idx_byte_offset)

        for v in vertices:
            x, y, z = v.pos
            all_positions.append([x, y, z])
            nx, ny, nz = v.normal
            all_normals.append([nx, ny, nz])

        for idx in indices:
            all_indices.append(idx + vert_offset)

        vert_offset += vc
        vert_byte_offset += vc * 40
        idx_byte_offset += ic * 2

    positions = np.array(all_positions, dtype=np.float32)
    normals = np.array(all_normals, dtype=np.float32)
    indices = np.array(all_indices, dtype=np.uint32)

    bbox_min = positions.min(axis=0)
    bbox_max = positions.max(axis=0)
    center = (bbox_min + bbox_max) / 2.0
    radius = float(np.linalg.norm(positions - center, axis=1).max())
    if radius < 1e-6:
        radius = 1.0

    return GpuMesh(positions, normals, indices, center, radius)


# ── Export (OBJ + MTL + DDS textures) ──────────────────────────────

def export_model_with_textures(entry: PazEntry, output_dir: str,
                               game_dir: str, progress_fn=None,
                               cached_entries: list[PazEntry] = None) -> dict:
    """Export PAC model as OBJ + MTL + DDS textures into a new subfolder.

    Creates: output_dir/model_name/
        model_name.obj
        model_name.mtl
        textures/*.dds

    Only references textures in the MTL that actually exist in the archive.
    """
    from paz_unpack import extract_entry as paz_extract_entry

    pac_data = read_pac_bytes(entry)
    model_name = os.path.splitext(os.path.basename(entry.path))[0]
    model_dir = os.path.join(output_dir, model_name)
    tex_dir = os.path.join(model_dir, "textures")
    os.makedirs(tex_dir, exist_ok=True)

    # Parse geometry
    header = parse_header(pac_data)
    sec_by_idx = {s['index']: s for s in header['sections']}
    sec0 = sec_by_idx[0]
    geom_sec_idx = next((i for i in [4,3,2,1] if i in sec_by_idx), None)
    if geom_sec_idx is None:
        raise ValueError("No geometry sections")
    geom_sec = sec_by_idx[geom_sec_idx]
    lod = 4 - geom_sec_idx

    descriptors = find_mesh_descriptors(pac_data, sec0['offset'], sec0['size'])
    if not descriptors:
        raise ValueError("No mesh descriptors")

    # Build meshes (same logic as export_pac)
    total_verts = sum(d.vertex_counts[lod] for d in descriptors)
    total_indices = sum(d.index_counts[lod] for d in descriptors)
    meshes = []
    vert_byte_offset = 0
    idx_byte_offset = geom_sec['size'] - total_indices * 2  # indices at end of section
    for desc in descriptors:
        vc = desc.vertex_counts[lod]
        ic = desc.index_counts[lod]
        if vc == 0:
            continue
        vertices = decode_vertices(pac_data, geom_sec['offset'], vc, desc,
                                   vertex_start=vert_byte_offset)
        indices = decode_indices(pac_data, geom_sec['offset'], ic, 0,
                                index_start=idx_byte_offset)
        meshes.append(Mesh(name=desc.display_name, material=desc.material_name,
                           vertices=vertices, indices=indices))
        vert_byte_offset += vc * 40
        idx_byte_offset += ic * 2

    # Step 1: Extract textures first, track what's available
    if progress_fn:
        progress_fn("Extracting textures...")

    dds_wanted = set()
    for desc in descriptors:
        if desc.material_name == "(null)":
            continue
        base = material_to_dds_basename(desc.material_name)
        for suffix in ['', '_n', '_sp', '_m', '_mg']:
            dds_wanted.add(base + suffix + '.dds')

    if cached_entries is not None:
        all_entries = cached_entries
    else:
        dir_0009 = os.path.join(game_dir, "0009")
        all_entries = parse_pamt(os.path.join(dir_0009, "0.pamt"), paz_dir=dir_0009)

    available = set()  # lowercase DDS basenames that were actually extracted
    extracted = 0
    for dds_name in dds_wanted:
        matches = [e for e in all_entries
                   if os.path.basename(e.path).lower() == dds_name.lower()]
        for m in matches:
            try:
                paz_extract_entry(m, tex_dir, decrypt_xml=False)
                # Move from nested path to flat textures dir
                nested = os.path.join(tex_dir, m.path.replace('/', os.sep))
                flat = os.path.join(tex_dir, os.path.basename(m.path))
                if os.path.exists(nested) and nested != flat:
                    os.replace(nested, flat)
                    try:
                        d = os.path.dirname(nested)
                        while d != tex_dir:
                            os.rmdir(d)
                            d = os.path.dirname(d)
                    except OSError:
                        pass
                available.add(dds_name.lower())
                extracted += 1
            except Exception:
                pass

    # Step 2: Write OBJ + MTL, only referencing textures that exist
    if progress_fn:
        progress_fn("Writing OBJ + MTL...")

    obj_path = os.path.join(model_dir, model_name + '.obj')
    mtl_path = os.path.join(model_dir, model_name + '.mtl')
    write_obj(meshes, obj_path, model_name + '.mtl')
    write_mtl(meshes, mtl_path, texture_rel_dir="textures",
              available_textures=available)

    total_verts_out = sum(len(m.vertices) for m in meshes)
    total_tris = sum(len(m.indices) // 3 for m in meshes)

    return {
        'obj': obj_path, 'mtl': mtl_path,
        'meshes': len(meshes), 'vertices': total_verts_out, 'triangles': total_tris,
        'names': [m.name for m in meshes],
        'textures_extracted': extracted, 'textures_expected': len(dds_wanted),
        'export_dir': model_dir,
    }


# ── Orbit camera ────────────────────────────────────────────────────

class OrbitCamera:
    def __init__(self):
        self.yaw = 0.0
        self.pitch = 0.3
        self.radius = 2.0
        self.target = np.zeros(3, dtype=np.float32)
        self.fov_y = 45.0
        self._last_x = 0
        self._last_y = 0

    def fit_to_sphere(self, center, radius):
        self.target = center.copy()
        half_fov = math.radians(self.fov_y * 0.5)
        self.radius = radius / math.sin(half_fov) * 1.3
        self.yaw = 0.0
        self.pitch = 0.3

    def eye_position(self):
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        return self.target + self.radius * np.array([cp * sy, sp, cp * cy], dtype=np.float32)

    def view_matrix(self):
        eye = self.eye_position()
        fwd = self.target - eye
        fwd_len = np.linalg.norm(fwd)
        if fwd_len < 1e-8:
            return np.eye(4, dtype=np.float32)
        fwd /= fwd_len
        world_up = np.array([0, 1, 0], dtype=np.float32)
        right = np.cross(fwd, world_up)
        r_len = np.linalg.norm(right)
        if r_len < 1e-8:
            right = np.array([1, 0, 0], dtype=np.float32)
        else:
            right /= r_len
        up = np.cross(right, fwd)

        m = np.eye(4, dtype=np.float32)
        m[0, :3] = right
        m[1, :3] = up
        m[2, :3] = -fwd
        m[0, 3] = -np.dot(right, eye)
        m[1, 3] = -np.dot(up, eye)
        m[2, 3] = np.dot(fwd, eye)
        return m

    def proj_matrix(self, aspect):
        near = max(self.radius * 0.001, 0.001)
        far = self.radius * 100.0
        f = 1.0 / math.tan(math.radians(self.fov_y) * 0.5)
        m = np.zeros((4, 4), dtype=np.float32)
        m[0, 0] = f / aspect
        m[1, 1] = f
        m[2, 2] = (far + near) / (near - far)
        m[2, 3] = (2 * far * near) / (near - far)
        m[3, 2] = -1.0
        return m

    def handle_press(self, x, y):
        self._last_x = x
        self._last_y = y

    def handle_move(self, buttons, x, y):
        dx = x - self._last_x
        dy = y - self._last_y
        self._last_x = x
        self._last_y = y

        if buttons & Qt.MouseButton.LeftButton:
            self.yaw -= dx * 0.005
            self.pitch += dy * 0.005
            self.pitch = max(-1.5, min(1.5, self.pitch))
        elif buttons & Qt.MouseButton.MiddleButton:
            cp, sp = math.cos(self.pitch), math.sin(self.pitch)
            cy, sy = math.cos(self.yaw), math.sin(self.yaw)
            right = np.array([cy, 0, -sy], dtype=np.float32)
            up = np.array([-sp * sy, cp, -sp * cy], dtype=np.float32)
            scale = self.radius * 0.002
            self.target += right * (-dx * scale) + up * (dy * scale)

    def handle_scroll(self, delta):
        self.radius *= 0.9 ** (delta / 120.0)
        self.radius = max(0.01, self.radius)


# ── OpenGL shaders ──────────────────────────────────────────────────

VERT_SRC = """#version 330 core
layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aNormal;
uniform mat4 uMVP;
out vec3 vNormal;
out vec3 vPos;
void main() {
    vPos = aPos;
    vNormal = aNormal;
    gl_Position = uMVP * vec4(aPos, 1.0);
}
"""

FRAG_SRC = """#version 330 core
in vec3 vNormal;
in vec3 vPos;
out vec4 FragColor;
uniform vec3 uLightDir;
uniform vec3 uColor;
void main() {
    vec3 N = normalize(vNormal);
    vec3 L = normalize(uLightDir);
    float diff = max(abs(dot(N, L)), 0.0);
    vec3 ambient = 0.18 * uColor;
    vec3 diffuse = 0.82 * diff * uColor;
    FragColor = vec4(ambient + diffuse, 1.0);
}
"""


# ── 3D viewer widget ───────────────────────────────────────────────

class ModelViewer(QOpenGLWidget):
    def __init__(self, parent=None):
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setSamples(4)
        fmt.setDepthBufferSize(24)
        super().__init__(parent)
        self.setFormat(fmt)
        self._camera = OrbitCamera()
        self._program = 0
        self._vao = 0
        self._vbo_pos = 0
        self._vbo_nor = 0
        self._ebo = 0
        self._index_count = 0
        self._has_mesh = False

    def initializeGL(self):
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_MULTISAMPLE)
        glClearColor(0.14, 0.14, 0.16, 1.0)
        self._compile_shaders()
        self._setup_buffers()

    def resizeGL(self, w, h):
        glViewport(0, 0, w, h)

    def paintGL(self):
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        if not self._has_mesh:
            return
        aspect = self.width() / max(self.height(), 1)
        MVP = self._camera.proj_matrix(aspect) @ self._camera.view_matrix()
        glUseProgram(self._program)
        glUniformMatrix4fv(glGetUniformLocation(self._program, "uMVP"),
                           1, GL_TRUE, MVP.astype(np.float32))
        light = np.array([0.6, 0.8, 0.5], dtype=np.float32)
        light /= np.linalg.norm(light)
        glUniform3fv(glGetUniformLocation(self._program, "uLightDir"), 1, light)
        glUniform3f(glGetUniformLocation(self._program, "uColor"), 0.72, 0.72, 0.76)
        glBindVertexArray(self._vao)
        glDrawElements(GL_TRIANGLES, self._index_count, GL_UNSIGNED_INT, None)
        glBindVertexArray(0)

    def load_mesh(self, mesh: GpuMesh):
        self.makeCurrent()
        glBindBuffer(GL_ARRAY_BUFFER, self._vbo_pos)
        glBufferData(GL_ARRAY_BUFFER, mesh.positions.nbytes,
                     mesh.positions.tobytes(), GL_STATIC_DRAW)
        glBindBuffer(GL_ARRAY_BUFFER, self._vbo_nor)
        glBufferData(GL_ARRAY_BUFFER, mesh.normals.nbytes,
                     mesh.normals.tobytes(), GL_STATIC_DRAW)
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, self._ebo)
        glBufferData(GL_ELEMENT_ARRAY_BUFFER, mesh.indices.nbytes,
                     mesh.indices.tobytes(), GL_STATIC_DRAW)
        self._index_count = len(mesh.indices)
        self._has_mesh = True
        self._camera.fit_to_sphere(mesh.center, mesh.radius)
        self.doneCurrent()
        self.update()

    def clear_mesh(self):
        self._has_mesh = False
        self.update()

    def mousePressEvent(self, e):
        self._camera.handle_press(e.position().x(), e.position().y())

    def mouseMoveEvent(self, e):
        self._camera.handle_move(e.buttons(), e.position().x(), e.position().y())
        self.update()

    def wheelEvent(self, e):
        self._camera.handle_scroll(e.angleDelta().y())
        self.update()

    def _compile_shaders(self):
        vs = glCreateShader(GL_VERTEX_SHADER)
        glShaderSource(vs, VERT_SRC)
        glCompileShader(vs)
        if not glGetShaderiv(vs, GL_COMPILE_STATUS):
            raise RuntimeError(glGetShaderInfoLog(vs).decode())
        fs = glCreateShader(GL_FRAGMENT_SHADER)
        glShaderSource(fs, FRAG_SRC)
        glCompileShader(fs)
        if not glGetShaderiv(fs, GL_COMPILE_STATUS):
            raise RuntimeError(glGetShaderInfoLog(fs).decode())
        self._program = glCreateProgram()
        glAttachShader(self._program, vs)
        glAttachShader(self._program, fs)
        glLinkProgram(self._program)
        if not glGetProgramiv(self._program, GL_LINK_STATUS):
            raise RuntimeError(glGetProgramInfoLog(self._program).decode())
        glDeleteShader(vs)
        glDeleteShader(fs)

    def _setup_buffers(self):
        self._vao = glGenVertexArrays(1)
        self._vbo_pos, self._vbo_nor = glGenBuffers(2)
        self._ebo = glGenBuffers(1)
        glBindVertexArray(self._vao)
        glBindBuffer(GL_ARRAY_BUFFER, self._vbo_pos)
        glBufferData(GL_ARRAY_BUFFER, 0, None, GL_STATIC_DRAW)
        glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 12, None)
        glEnableVertexAttribArray(0)
        glBindBuffer(GL_ARRAY_BUFFER, self._vbo_nor)
        glBufferData(GL_ARRAY_BUFFER, 0, None, GL_STATIC_DRAW)
        glVertexAttribPointer(1, 3, GL_FLOAT, GL_FALSE, 12, None)
        glEnableVertexAttribArray(1)
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, self._ebo)
        glBufferData(GL_ELEMENT_ARRAY_BUFFER, 0, None, GL_STATIC_DRAW)
        glBindVertexArray(0)


# ── Background workers ──────────────────────────────────────────────

class CatalogWorker(QThread):
    catalog_ready = Signal(list, list)  # (catalog, all_pamt_entries)
    progress = Signal(str)
    failed = Signal(str)

    def __init__(self, game_dir, parent=None):
        super().__init__(parent)
        self._game_dir = game_dir

    def run(self):
        try:
            self.progress.emit("Parsing PAMT index...")
            catalog, all_entries = build_catalog(self._game_dir)
            self.catalog_ready.emit(catalog, all_entries)
        except Exception as e:
            self.failed.emit(str(e))


class LoadWorker(QThread):
    mesh_ready = Signal(object)
    load_error = Signal(str)

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self._entry = entry

    def run(self):
        try:
            mesh = load_pac_mesh(self._entry.paz_entry)
            self.mesh_ready.emit(mesh)
        except Exception as e:
            self.load_error.emit(f"{self._entry.filename}: {e}")


class ExportWorker(QThread):
    export_done = Signal(dict)
    export_error = Signal(str)
    progress = Signal(str)

    def __init__(self, entry, output_dir, game_dir, cached_entries=None, parent=None):
        super().__init__(parent)
        self._entry = entry
        self._output_dir = output_dir
        self._game_dir = game_dir
        self._cached_entries = cached_entries

    def run(self):
        try:
            result = export_model_with_textures(
                self._entry.paz_entry, self._output_dir,
                self._game_dir, progress_fn=self.progress.emit,
                cached_entries=self._cached_entries)
            self.export_done.emit(result)
        except Exception as e:
            self.export_error.emit(str(e))


# ── Setup screen (first run) ───────────────────────────────────────

class SetupScreen(QWidget):
    """Shown on first run when no game directory is configured."""
    game_dir_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Crimson Desert PAC Browser")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(18)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        layout.addSpacing(20)

        hint = QLabel("Select your Crimson Desert installation folder to get started.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        layout.addSpacing(20)

        btn = QPushButton("Locate Crimson Desert")
        btn.setMinimumSize(QSize(300, 50))
        btn_font = QFont()
        btn_font.setPointSize(13)
        btn.setFont(btn_font)
        btn.clicked.connect(self._on_browse)

        # Center the button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addSpacing(10)
        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setStyleSheet("color: #cc4444;")
        layout.addWidget(self._status)

    def _on_browse(self):
        path = QFileDialog.getExistingDirectory(self, "Select Crimson Desert folder")
        if not path:
            return
        if validate_game_dir(path):
            save_settings(game_dir=path)
            self.game_dir_selected.emit(path)
        else:
            self._status.setText(
                "Invalid folder — expected 0009/0.pamt inside.\n"
                "Select the root Crimson Desert installation directory."
            )


# ── Main window ─────────────────────────────────────────────────────

class BrowserWindow(QMainWindow):
    def __init__(self, game_dir: str):
        super().__init__()
        self.setWindowTitle("Crimson Desert PAC Browser")
        self.resize(1280, 800)

        self._game_dir = game_dir
        self._catalog: list[CatalogEntry] = []
        self._all_entries: list[PazEntry] = []  # cached PAMT for fast texture export
        self._filtered: list[CatalogEntry] = []
        self._load_worker: LoadWorker | None = None
        self._export_worker: ExportWorker | None = None
        self._current_entry: CatalogEntry | None = None

        self._build_menu()
        self._build_ui()
        self._start_catalog_load()

    def _build_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        self._export_action = QAction("Export Model...", self)
        self._export_action.setShortcut("Ctrl+E")
        self._export_action.setEnabled(False)
        self._export_action.triggered.connect(self._on_export)
        file_menu.addAction(self._export_action)

        file_menu.addSeparator()

        change_dir_action = QAction("Change Game Directory...", self)
        change_dir_action.triggered.connect(self._on_change_dir)
        file_menu.addAction(change_dir_action)

    def _build_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search models...")
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection)
        layout.addWidget(self._list)

        # Right panel
        self._viewer = ModelViewer()

        splitter.addWidget(left)
        splitter.addWidget(self._viewer)
        splitter.setSizes([320, 960])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Starting...")

    def _start_catalog_load(self):
        self.statusBar().showMessage("Loading catalog...")
        self._cat_worker = CatalogWorker(self._game_dir, self)
        self._cat_worker.catalog_ready.connect(self._on_catalog_ready)
        self._cat_worker.progress.connect(self.statusBar().showMessage)
        self._cat_worker.failed.connect(lambda m: self.statusBar().showMessage(f"Error: {m}"))
        self._cat_worker.start()

    def _on_catalog_ready(self, catalog, all_entries):
        self._catalog = catalog
        self._all_entries = all_entries
        self._filtered = catalog
        self._populate_list(catalog)
        self.statusBar().showMessage(f"Loaded {len(catalog):,} PAC files")

    def _on_search(self, text):
        key = text.strip().lower()
        if not key:
            self._filtered = self._catalog
        else:
            terms = key.split()
            self._filtered = [
                e for e in self._catalog
                if all(t in e.search_key for t in terms)
            ]
        self._populate_list(self._filtered)
        self.statusBar().showMessage(f"{len(self._filtered):,} matches")

    def _populate_list(self, entries):
        self._list.setUpdatesEnabled(False)
        self._list.clear()
        for e in entries:
            item = QListWidgetItem(e.display_name)
            item.setData(Qt.ItemDataRole.UserRole, e)
            self._list.addItem(item)
        self._list.setUpdatesEnabled(True)

    def _on_selection(self, current, _prev):
        if current is None:
            return
        entry = current.data(Qt.ItemDataRole.UserRole)
        self._current_entry = entry
        self._export_action.setEnabled(True)
        self._load_model(entry)

    def _load_model(self, entry):
        if self._load_worker and self._load_worker.isRunning():
            self._load_worker.quit()
            self._load_worker.wait(500)
        self._viewer.clear_mesh()
        self.statusBar().showMessage(f"Loading {entry.filename}...")
        self._load_worker = LoadWorker(entry, self)
        self._load_worker.mesh_ready.connect(self._on_mesh_ready)
        self._load_worker.load_error.connect(self._on_load_error)
        self._load_worker.start()

    def _on_mesh_ready(self, mesh):
        self._viewer.load_mesh(mesh)
        tris = len(mesh.indices) // 3
        verts = len(mesh.positions)
        self.statusBar().showMessage(f"{verts:,} vertices, {tris:,} triangles")

    def _on_load_error(self, msg):
        self.statusBar().showMessage(f"Error: {msg}")

    # ── Export ──

    def _on_export(self):
        if not self._current_entry:
            return
        output_dir = QFileDialog.getExistingDirectory(self, "Export to folder")
        if not output_dir:
            return

        self._export_action.setEnabled(False)
        self.statusBar().showMessage("Exporting...")

        self._export_worker = ExportWorker(
            self._current_entry, output_dir, self._game_dir,
            cached_entries=self._all_entries, parent=self)
        self._export_worker.progress.connect(self.statusBar().showMessage)
        self._export_worker.export_done.connect(self._on_export_done)
        self._export_worker.export_error.connect(self._on_export_error)
        self._export_worker.start()

    def _on_export_done(self, result):
        self._export_action.setEnabled(True)
        tex = result.get('textures_extracted', 0)
        tex_total = result.get('textures_expected', 0)
        path = result.get('export_dir', '')
        self.statusBar().showMessage(
            f"Exported to {path} — {result['vertices']} verts, "
            f"{result['triangles']} tris, {tex}/{tex_total} textures")

    def _on_export_error(self, msg):
        self._export_action.setEnabled(True)
        self.statusBar().showMessage(f"Export error: {msg}")

    # ── Change game dir ──

    def _on_change_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Crimson Desert folder")
        if not path:
            return
        if validate_game_dir(path):
            save_settings(game_dir=path)
            QMessageBox.information(self, "Restart Required",
                                    "Game directory changed. Restart the application to reload.")
        else:
            QMessageBox.warning(self, "Invalid Directory",
                                "Expected 0009/0.pamt inside the selected folder.")


# ── Entry point ─────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    settings = load_settings()
    game_dir = settings.get("game_dir", "")

    if game_dir and validate_game_dir(game_dir):
        win = BrowserWindow(game_dir)
    else:
        # Show setup screen inside a plain window
        win = QMainWindow()
        win.setWindowTitle("Crimson Desert PAC Browser")
        win.resize(800, 400)
        setup = SetupScreen(win)

        def on_dir_selected(path):
            win.close()
            browser = BrowserWindow(path)
            browser.show()
            # Keep reference so it doesn't get garbage collected
            app._browser = browser

        setup.game_dir_selected.connect(on_dir_selected)
        win.setCentralWidget(setup)

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
