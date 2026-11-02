"""Main window, setup screen, and export dialog for the model browser."""

import os
import sys

from PySide6.QtWidgets import (
    QMainWindow, QSplitter, QStackedWidget, QWidget,
    QVBoxLayout, QHBoxLayout, QLineEdit, QListView,
    QPushButton, QLabel, QFileDialog, QMenuBar, QMessageBox, QComboBox,
    QDialog, QCheckBox,
)
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QAction

from browser.settings import load_settings, save_settings, validate_game_dir
from browser.models import (
    CatalogEntry, ItemSearchEntry, GpuMesh, RenderMode, TrigramIndex, fuzzy_match,
    _SEPARATOR_SENTINEL, _ITEM_SECTION, _CHAR_SECTION, _MODEL_SECTION,
    _ItemHeaderRow, _ItemChildRow, _CharHeaderRow,
)
from browser.catalog import CatalogModel, SeparatorDelegate, CatalogWorker
from browser.loaders import LoadWorker, ExportWorker
from browser.viewer import ModelViewer
from item_db import ItemIndex, ItemRecord, CharacterIndex, CharacterRecord


# ── Export dialog ────────────────────────────────────────────────────

class ExportDialog(QDialog):
    """Export dialog with format selector, path selector, and dye color option."""

    _FORMAT_OPTIONS = [
        ("fbx", "FBX — mesh, materials, textures, tangents, skeleton"),
        ("gltf", "glTF — mesh, materials, textures, tangents, skeleton"),
        ("obj", "OBJ + MTL — mesh, materials, textures"),
    ]

    def __init__(self, default_dir: str = "", default_format: str = "fbx",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Model")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        # Format selector
        fmt_label = QLabel("Format")
        fmt_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(fmt_label)

        self._format_combo = QComboBox()
        for fmt_id, desc in self._FORMAT_OPTIONS:
            self._format_combo.addItem(desc, fmt_id)
        # Set default
        for i, (fmt_id, _) in enumerate(self._FORMAT_OPTIONS):
            if fmt_id == default_format:
                self._format_combo.setCurrentIndex(i)
                break
        self._format_combo.setStyleSheet("padding: 4px; font-size: 12px;")
        layout.addWidget(self._format_combo)
        layout.addSpacing(12)

        # Path selector row
        path_label = QLabel("Output directory")
        path_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(path_label)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit(default_dir)
        self._path_edit.setPlaceholderText("Export directory...")
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(self._path_edit, 1)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)
        layout.addSpacing(12)

        # Dye color checkbox
        self._dye_check = QCheckBox("Apply default dye colors")
        layout.addWidget(self._dye_check)

        hint = QLabel("Export raw color mask or apply default in-game dye colors to mesh textures")
        hint.setStyleSheet("color: gray; font-size: 11px; margin-top: -2px;")
        hint.setWordWrap(True)
        hint.setContentsMargins(22, 0, 0, 0)
        layout.addWidget(hint)
        layout.addSpacing(8)

        # Export button
        export_btn = QPushButton("Export")
        export_btn.setDefault(True)
        export_btn.clicked.connect(self.accept)
        layout.addWidget(export_btn)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Export to folder", self._path_edit.text())
        if d:
            self._path_edit.setText(d)

    def output_dir(self) -> str:
        return self._path_edit.text().strip()

    def export_format(self) -> str:
        return self._format_combo.currentData()

    def apply_dye_colors(self) -> bool:
        return self._dye_check.isChecked()


# ── Setup screen (first run) ───────────────────────────────────────

class SetupScreen(QWidget):
    """Shown on first run when no game directory is configured."""

    from PySide6.QtCore import Signal
    game_dir_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Crimson Desert Model Browser")
        title.setObjectName("setupTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        layout.addSpacing(12)

        hint = QLabel("Select your Crimson Desert installation folder to get started.")
        hint.setObjectName("setupHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        layout.addSpacing(24)

        btn = QPushButton("Locate Crimson Desert")
        btn.setMinimumSize(QSize(300, 50))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self._on_browse)

        # Center the button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addSpacing(10)
        self._status = QLabel("")
        self._status.setObjectName("setupError")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        self.setWindowTitle("Crimson Desert Model Browser")
        self.resize(1280, 800)

        self._game_dir = game_dir
        self._catalog: list[CatalogEntry] = []
        self._all_entries: list = []  # cached PAMT for fast texture export
        self._is_filtered = False
        self._trigram_index: TrigramIndex | None = None
        self._item_index: ItemIndex | None = None
        self._item_trigram: TrigramIndex | None = None
        self._item_search_entries: list = []
        self._char_index: CharacterIndex | None = None
        self._char_trigram: TrigramIndex | None = None
        self._char_search_entries: list = []
        self._all_item_rows: list[_ItemHeaderRow] = []
        self._pac_lookup: dict[str, CatalogEntry] = {}
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

        # View menu — render modes
        view_menu = menubar.addMenu("View")
        self._render_mode_actions = {}
        shortcuts = {
            RenderMode.SOLID: "1",
            RenderMode.WIREFRAME: "2",
            RenderMode.NORMALS: "3",
            RenderMode.UV: "4",
        }
        for mode in RenderMode:
            action = QAction(mode.name.capitalize(), self)
            action.setCheckable(True)
            action.setChecked(mode == RenderMode.SOLID)
            if mode in shortcuts:
                action.setShortcut(shortcuts[mode])
            action.triggered.connect(lambda checked, m=mode: self._on_render_mode(m))
            view_menu.addAction(action)
            self._render_mode_actions[mode] = action

    def _on_render_mode(self, mode: RenderMode):
        self._viewer.set_render_mode(mode)
        for m, action in self._render_mode_actions.items():
            action.setChecked(m == mode)

    def _build_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(8, 8, 4, 8)
        layout.setSpacing(6)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        self._category_filter = QComboBox()
        self._category_filter.addItems(["All", "Items", "Characters", "Objects", "Effects", "Terrain"])
        self._category_filter.setEnabled(False)
        self._category_filter.currentTextChanged.connect(self._on_category_changed)
        filter_row.addWidget(self._category_filter, 1)

        self._source_filter = QComboBox()
        self._source_filter.addItems(["All Content", "Base Game", "DLC"])
        self._source_filter.setEnabled(False)
        self._source_filter.currentTextChanged.connect(self._on_source_changed)
        filter_row.addWidget(self._source_filter, 1)

        layout.addLayout(filter_row)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search models...")
        self._search.setClearButtonEnabled(True)
        self._search.setEnabled(False)
        self._search.textChanged.connect(self._on_search_text_changed)
        layout.addWidget(self._search)
        self._count_label = QLabel("")
        self._count_label.setObjectName("countLabel")
        layout.addWidget(self._count_label)

        # Virtual list model -- only renders visible rows
        self._list_model = CatalogModel(self)
        self._list = QListView()
        self._list.setUniformItemSizes(True)
        self._list.setModel(self._list_model)
        self._list.setItemDelegate(SeparatorDelegate(self._list))
        self._list.clicked.connect(self._on_selection)
        self._list.verticalScrollBar().valueChanged.connect(self._on_scroll)
        layout.addWidget(self._list)

        # Debounce timer for search input (150ms)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(30)
        self._search_timer.timeout.connect(self._apply_filters)

        # Cache for category-filtered subset (avoids re-filtering 63K on every keystroke)
        self._category_subset: list[CatalogEntry] = []

        # Right panel (loading screen / viewer + info strip)
        self._right_stack = QStackedWidget()

        # Page 0: loading screen
        loading_page = QWidget()
        loading_layout = QVBoxLayout(loading_page)
        loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label = QLabel("Loading catalog...")
        self._loading_label.setObjectName("loadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(self._loading_label)
        self._right_stack.addWidget(loading_page)

        # Page 1: viewer + info strip
        viewer_page = QWidget()
        viewer_layout = QVBoxLayout(viewer_page)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.setSpacing(0)
        self._viewer = ModelViewer()
        viewer_layout.addWidget(self._viewer, 1)
        self._info_strip = QLabel("Select a model to preview")
        self._info_strip.setObjectName("infoStrip")
        self._info_strip.setFixedHeight(28)
        viewer_layout.addWidget(self._info_strip)
        self._right_stack.addWidget(viewer_page)

        splitter.addWidget(left)
        splitter.addWidget(self._right_stack)
        splitter.setSizes([320, 960])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Starting...")

    def _start_catalog_load(self):
        self.statusBar().showMessage("Loading catalog...")
        self._cat_worker = CatalogWorker(self._game_dir, self)
        self._cat_worker.catalog_ready.connect(self._on_catalog_ready)
        self._cat_worker.progress.connect(self.statusBar().showMessage)
        self._cat_worker.progress.connect(self._loading_label.setText)
        self._cat_worker.failed.connect(self._on_catalog_failed)
        self._cat_worker.start()

    def _on_catalog_ready(self, catalog, all_entries, item_index, char_index):
        self._catalog = catalog
        self._all_entries = all_entries
        self._category_subset = catalog
        self._trigram_index = TrigramIndex(catalog)
        # Count labels use model.match_count() + model.total_row_count()

        # Item search index
        self._item_index = item_index
        self._pac_lookup = {e.filename: e for e in catalog}
        self._list_model.set_pac_lookup(self._pac_lookup)

        # Build all item rows (with is_dlc flag) and trigram index
        if item_index and item_index.items:
            self._all_item_rows = []
            for rec in item_index.items:
                is_dlc = any(getattr(self._pac_lookup.get(pf), 'is_dlc', False) for pf in rec.pac_files)
                self._all_item_rows.append(_ItemHeaderRow(
                    display_name=rec.display_name,
                    internal_name=rec.internal_name,
                    pac_files=rec.pac_files,
                    is_dlc=is_dlc,
                ))
            self._item_search_entries = [
                ItemSearchEntry(
                    search_key=f"{rec.display_name} {rec.internal_name}".lower(),
                    record=rec,
                )
                for rec in item_index.items
            ]
            self._item_trigram = TrigramIndex(self._item_search_entries)
        else:
            self._all_item_rows = []
            self._item_search_entries = []
            self._item_trigram = None

        # Build character appearance trigram index
        self._char_index = char_index
        if char_index and char_index.characters:
            from browser.models import ItemSearchEntry as _SE
            self._char_search_entries = [
                _SE(search_key=rec.search_key, record=rec)
                for rec in char_index.characters
            ]
            self._char_trigram = TrigramIndex(self._char_search_entries)
        else:
            self._char_search_entries = []
            self._char_trigram = None

        self._list_model.set_items(catalog, show_tags=True)
        pac_count = sum(1 for e in catalog if e.file_type == "pac")
        pam_count = sum(1 for e in catalog if e.file_type == "pam")
        item_count = len(item_index.items) if item_index else 0
        char_count = len(char_index.characters) if char_index else 0
        self._count_label.setText(f"{len(catalog):,} models")
        self._search.setEnabled(True)
        self._category_filter.setEnabled(True)
        self._source_filter.setEnabled(True)
        self._search.setFocus()
        self._right_stack.setCurrentIndex(1)
        msg = f"Loaded {pac_count:,} PAC + {pam_count:,} PAM = {len(catalog):,} models"
        if item_count:
            msg += f" + {item_count:,} items"
        if char_count:
            msg += f" + {char_count:,} characters"
        self.statusBar().showMessage(msg)

    def _on_catalog_failed(self, msg):
        self._loading_label.setText(f"Failed to load catalog:\n{msg}")
        self.statusBar().showMessage(f"Error: {msg}")

    def _on_category_changed(self, _=None):
        """Rebuild category subset, trigram index, and re-apply text filter."""
        category = self._category_filter.currentText().lower()
        if category == "items":
            self._search.setPlaceholderText("Search items...")
        else:
            self._search.setPlaceholderText("Search models...")

        if category == "all":
            self._category_subset = self._catalog
        elif category != "items":
            self._category_subset = [e for e in self._catalog if e.category == category]
        else:
            self._category_subset = []
        self._trigram_index = TrigramIndex(self._category_subset) if self._category_subset else None
        self._apply_filters()

    def _on_source_changed(self, _=None):
        """Re-apply filters when DLC/Base Game source filter changes."""
        self._apply_filters()

    def _on_search_text_changed(self, _=None):
        """Filter immediately -- trigram index makes this fast enough."""
        self._apply_filters()

    def _apply_filters(self):
        category = self._category_filter.currentText().lower()
        source = self._source_filter.currentText()  # "All Content", "Base Game", "DLC"
        key = self._search.text().strip().lower()

        # Handle Items category separately
        if category == "items":
            items = self._all_item_rows
            if source == "Base Game":
                items = [it for it in items if not it.is_dlc]
            elif source == "DLC":
                items = [it for it in items if it.is_dlc]

            if key:
                terms = key.split()
                items = [it for it in items if all(t in (it.display_name + " " + it.internal_name).lower() for t in terms)]

            self._list_model.set_item_rows(items, show_tags=False)
            self._is_filtered = bool(key) or source != "All Content" or category != "all"
            self._update_count_label()
            return

        # Model categories (All, Characters, Objects, Effects, Terrain)
        show_tags = self._category_filter.currentText() == "All"
        subset = self._category_subset

        if source == "Base Game":
            subset = [e for e in subset if not e.is_dlc]
        elif source == "DLC":
            subset = [e for e in subset if e.is_dlc]

        idx = TrigramIndex(subset) if subset else None

        if key:
            terms = key.split()
            show_grouped = category in ("all", "characters")

            # Search character appearances (app.xml)
            char_rows = []
            if show_grouped and self._char_trigram and len(key) >= 3:
                char_hits = self._char_trigram.multi_term_matches(terms)
                scored = []
                for entry in char_hits:
                    rec = entry.record
                    if rec and rec.pac_files:
                        is_dlc = any(getattr(self._pac_lookup.get(pf), 'is_dlc', False) for pf in rec.pac_files)
                        if source == "Base Game" and is_dlc:
                            continue
                        if source == "DLC" and not is_dlc:
                            continue
                        scored.append((len(rec.app_name), rec, is_dlc))
                scored.sort(key=lambda x: x[0])
                for _, rec, is_dlc in scored:
                    char_rows.append(_CharHeaderRow(
                        display_label=rec.display_label,
                        app_name=rec.app_name,
                        pac_files=rec.pac_files,
                        is_dlc=is_dlc))

            # Search item names (display name + internal name)
            item_rows = []
            if show_grouped and self._item_trigram and len(key) >= 3:
                item_hits = self._item_trigram.multi_term_matches(terms)
                scored = []
                for entry in item_hits:
                    rec = entry.record if isinstance(entry, ItemSearchEntry) else None
                    if rec and rec.pac_files:
                        is_dlc = any(getattr(self._pac_lookup.get(pf), 'is_dlc', False) for pf in rec.pac_files)
                        if source == "Base Game" and is_dlc:
                            continue
                        if source == "DLC" and not is_dlc:
                            continue
                        scored.append((len(rec.display_name), rec, is_dlc))
                scored.sort(key=lambda x: x[0])
                for _, rec, is_dlc in scored:
                    item_rows.append(_ItemHeaderRow(
                        display_name=rec.display_name,
                        internal_name=rec.internal_name,
                        pac_files=rec.pac_files,
                        is_dlc=is_dlc))

            # Fast substring matching on model filenames via trigram index
            exact = idx.multi_term_matches(terms) if idx else [
                e for e in subset if all(t in e.search_key for t in terms)]

            # Fuzzy only when exact results are few
            fuzzy_entries = []
            if len(exact) < 200:
                exact_set = set(id(e) for e in exact)
                fuzzy = []
                for e in subset:
                    if id(e) not in exact_set:
                        matched, score = fuzzy_match(key, e.search_key)
                        if matched:
                            fuzzy.append((score, e))
                fuzzy.sort(key=lambda x: -x[0])
                fuzzy_entries = [e for _, e in fuzzy]

            if char_rows or item_rows:
                self._list_model.set_search_results(
                    item_rows, char_rows, exact, fuzzy_entries,
                    show_tags=show_tags)
            else:
                self._list_model.set_results(exact, fuzzy_entries, show_tags=show_tags)

        else:
            self._list_model.set_items(subset, show_tags=show_tags)

        self._is_filtered = bool(key) or self._category_filter.currentText() != "All" or source != "All Content"
        self._update_count_label()

    def _update_count_label(self):
        """Update status bar count from model state."""
        count = self._list_model.match_count()
        displayed = self._list_model.rowCount()
        total_rows = self._list_model.total_row_count()
        cat = self._category_filter.currentText().lower()
        if cat == "items":
            word = "items"
        else:
            word = "matches" if self._is_filtered else "models"
        if displayed < total_rows:
            label = f"{count:,} {word} (showing {displayed:,})"
        else:
            label = f"{count:,} {word}"
        self._count_label.setText(label)
        self.statusBar().showMessage(label)
        self._count_label.setText(label)
        self.statusBar().showMessage(label)

    def _on_scroll(self, value):
        """Load more rows when scrollbar nears bottom."""
        sb = self._list.verticalScrollBar()
        if sb.maximum() > 0 and value >= sb.maximum() - 50:
            if self._list_model.can_load_more():
                self._list_model.load_more()
                self._update_count_label()

    def _on_selection(self, index):
        if not index.isValid():
            return
        entry = self._list_model.data(index, Qt.ItemDataRole.UserRole)
        if entry is None:
            return
        # Item header click -- preview first PAC file
        if isinstance(entry, _ItemHeaderRow):
            if entry.pac_files:
                cat_entry = self._pac_lookup.get(entry.pac_files[0])
                if cat_entry:
                    self._current_entry = cat_entry
                    self._export_action.setEnabled(True)
                    self._load_model(cat_entry)
                    self._info_strip.setText(
                        f"{entry.display_name}  ({len(entry.pac_files)} model files)")
            return
        # Character header click -- preview first PAC file
        if isinstance(entry, _CharHeaderRow):
            if entry.pac_files:
                cat_entry = self._pac_lookup.get(entry.pac_files[0])
                if cat_entry:
                    self._current_entry = cat_entry
                    self._export_action.setEnabled(True)
                    self._load_model(cat_entry)
                    self._info_strip.setText(
                        f"{entry.display_label}  ({len(entry.pac_files)} model files)")
            return
        self._current_entry = entry
        self._export_action.setEnabled(True)
        self._load_model(entry)

    def _load_model(self, entry):
        if self._load_worker and self._load_worker.isRunning():
            self._load_worker.quit()
            self._load_worker.wait(500)
        self._viewer.clear_mesh()
        self._info_strip.setText(f"Loading {entry.display_name}...")
        self.statusBar().showMessage(f"Loading {entry.filename}...")
        self._load_worker = LoadWorker(entry, self)
        self._load_worker.mesh_ready.connect(self._on_mesh_ready)
        self._load_worker.load_error.connect(self._on_load_error)
        self._load_worker.start()

    def _on_mesh_ready(self, mesh):
        self._viewer.load_mesh(mesh)
        tris = len(mesh.indices) // 3
        verts = len(mesh.positions)
        self._info_strip.setText(f"{verts:,} vertices  \u00b7  {tris:,} triangles")
        self.statusBar().showMessage(f"{verts:,} vertices, {tris:,} triangles")

    def _on_load_error(self, msg):
        self._info_strip.setText("Failed to load model")
        self.statusBar().showMessage(f"Error: {msg}")

    # -- Export --

    def _on_export(self):
        if not self._current_entry:
            return
        settings = load_settings()
        last_dir = settings.get("export_dir", "")
        last_fmt = settings.get("export_format", "fbx")
        dlg = ExportDialog(default_dir=last_dir, default_format=last_fmt,
                           parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        output_dir = dlg.output_dir()
        if not output_dir:
            return
        save_settings(export_dir=output_dir)

        export_format = dlg.export_format()
        save_settings(export_format=export_format)
        self._export_action.setEnabled(False)
        self.statusBar().showMessage(f"Exporting ({export_format.upper()})...")

        self._export_worker = ExportWorker(
            self._current_entry, output_dir, self._game_dir,
            cached_entries=self._all_entries,
            apply_dye_colors=dlg.apply_dye_colors(),
            export_format=export_format, parent=self)
        self._export_worker.progress.connect(self.statusBar().showMessage)
        self._export_worker.export_done.connect(self._on_export_done)
        self._export_worker.export_error.connect(self._on_export_error)
        self._export_worker.start()

    def _on_export_done(self, result):
        self._export_action.setEnabled(True)
        tex = result.get('textures_extracted', 0)
        tex_total = result.get('textures_expected', 0)
        path = result.get('export_dir', '')
        fmt = result.get('format', 'obj').upper()
        self.statusBar().showMessage(
            f"Exported {fmt} to {path} — {result['vertices']} verts, "
            f"{result['triangles']} tris, {tex}/{tex_total} textures")

    def _on_export_error(self, msg):
        self._export_action.setEnabled(True)
        self.statusBar().showMessage(f"Export error: {msg}")

    # -- Change game dir --

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
    from PySide6.QtWidgets import QApplication
    from browser.settings import DARK_STYLE

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_STYLE)

    settings = load_settings()
    game_dir = settings.get("game_dir", "")

    if game_dir and validate_game_dir(game_dir):
        win = BrowserWindow(game_dir)
    else:
        # Show setup screen inside a plain window
        win = QMainWindow()
        win.setWindowTitle("Crimson Desert Model Browser")
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
