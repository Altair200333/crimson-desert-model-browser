import os

from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from PySide6.QtCore import Qt, QThread, Signal, QSize, QAbstractListModel, QModelIndex
from PySide6.QtGui import QFont, QColor

from browser.models import (
    CatalogEntry, GpuMesh,
    _SEPARATOR_SENTINEL, _ITEM_SECTION, _CHAR_SECTION, _MODEL_SECTION,
    _ItemHeaderRow, _ItemChildRow, _CharHeaderRow,
)
from item_db import build_item_index, build_character_index, build_prefab_pac_map
from paz_parse import PazEntry
from pamt_cache import parse_pamt_cached as parse_pamt


DIR_CONFIG = {
    "0009": ("characters", {".pac"}),
    "0000": ("objects", {".pam"}),
    "0007": ("effects", {".pam"}),
    "0015": ("terrain", {".pam"}),
}


def build_catalog(game_dir: str, progress_fn=None) -> tuple[list[CatalogEntry], list[PazEntry]]:
    """Returns (catalog, all_entries). Scans multiple game directories.

    all_entries is a flat list of PazEntry (filtered to .pac/.pam/.dds only)
    cached for texture export across all directories.
    """
    catalog = []
    all_entries = []

    scan_dirs = [(game_dir, "")]
    dlc_root = os.path.join(game_dir, "dlc")
    if os.path.isdir(dlc_root):
        for item in sorted(os.listdir(dlc_root)):
            item_path = os.path.join(dlc_root, item)
            if os.path.isdir(item_path):
                scan_dirs.append((item_path, f" ({item})"))

    for base_dir, label in scan_dirs:
        for dir_name, (category, extensions) in DIR_CONFIG.items():
            sub_dir = os.path.join(base_dir, dir_name)
            pamt_path = os.path.join(sub_dir, "0.pamt")
            if not os.path.isfile(pamt_path):
                continue

            if progress_fn:
                progress_fn(f"Parsing {dir_name}{label} ({category})...")

            entries = parse_pamt(pamt_path, paz_dir=sub_dir)

            # Cache mesh, texture, prefab, appearance, and pac.xml entries
            useful_exts = extensions | {".dds", ".prefab", ".pac.xml", ".app.xml"}
            for e in entries:
                lower = e.path.lower()
                if any(lower.endswith(ext) for ext in useful_exts):
                    all_entries.append(e)

            # Build catalog entries for mesh files
            file_type = "pac" if ".pac" in extensions else "pam"
            for e in entries:
                lower = e.path.lower()
                if not any(lower.endswith(ext) for ext in extensions):
                    continue
                if e.compressed and e.compression_type not in (0, 1):
                    continue

                fname = os.path.basename(e.path)
                stem = os.path.splitext(fname)[0]
                skey = stem.lower()
                catalog.append(CatalogEntry(
                    filename=fname, display_name=stem,
                    paz_entry=e, search_key=skey,
                    file_type=file_type, category=category,
                    is_dlc=bool(label),
                ))

    catalog.sort(key=lambda c: c.display_name.lower())
    return catalog, all_entries


class CatalogModel(QAbstractListModel):
    """Virtual list model -- Qt only requests data for visible rows.

    Supports an optional separator between exact and fuzzy results.
    Separator rows store _SEPARATOR_SENTINEL and are non-selectable.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_rows = []   # full result set
        self._rows = []       # currently visible subset (lazy-loaded pages)
        self._match_count = 0 # actual search hits (headers + model entries, not children/sentinels)
        self._show_tags = True
        self._pac_lookup: dict[str, CatalogEntry] = {}  # pac filename -> entry

    def set_pac_lookup(self, lookup: dict):
        self._pac_lookup = lookup

    _PAGE_SIZE = 500  # rows loaded per page

    def set_results(self, exact: list, fuzzy: list, show_tags: bool = True):
        """Set search results with optional separator between groups."""
        self.beginResetModel()
        self._show_tags = show_tags
        self._match_count = len(exact) + len(fuzzy)
        self._all_rows = list(exact)
        if exact and fuzzy:
            self._all_rows.append(_SEPARATOR_SENTINEL)
        self._all_rows.extend(fuzzy)
        self._rows = self._all_rows[:self._PAGE_SIZE]
        self.endResetModel()

    def set_items(self, items: list, show_tags: bool = True):
        """Set plain item list (no separator)."""
        self.beginResetModel()
        self._show_tags = show_tags
        self._match_count = len(items)
        self._all_rows = list(items)
        self._rows = self._all_rows[:self._PAGE_SIZE]
        self.endResetModel()

    def set_item_rows(self, item_rows: list[_ItemHeaderRow], show_tags: bool = True):
        """Set item list with resolved PAC child rows."""
        self.beginResetModel()
        self._show_tags = show_tags
        self._all_rows = []
        hits = 0
        for header in item_rows:
            children = self._resolve_pac_children(header.pac_files)
            if children:
                hits += 1
                self._all_rows.append(header)
                self._all_rows.extend(children)
        self._match_count = hits
        self._rows = self._all_rows[:self._PAGE_SIZE]
        self.endResetModel()

    def _resolve_pac_children(self, pac_files: list) -> list[_ItemChildRow]:
        """Resolve pac filenames to catalog entries, return child rows."""
        children = []
        for pac_name in pac_files:
            entry = self._pac_lookup.get(pac_name)
            if entry:
                children.append(entry)
            else:
                base = pac_name.replace('.pac', '')
                for sfx in ('_l.pac', '_r.pac', '_sub01.pac'):
                    e = self._pac_lookup.get(base + sfx)
                    if e and e not in children:
                        children.append(e)
        return [_ItemChildRow(catalog_entry=e, is_last=(i == len(children) - 1))
                for i, e in enumerate(children)]

    def set_search_results(self, item_rows: list[_ItemHeaderRow],
                           char_rows: list[_CharHeaderRow],
                           model_exact: list, model_fuzzy: list,
                           show_tags: bool = True):
        """Set combined item + character + model search results."""
        self.beginResetModel()
        self._show_tags = show_tags
        self._all_rows = []
        hits = 0
        has_grouped = False

        if char_rows:
            filtered = []
            for header in char_rows:
                children = self._resolve_pac_children(header.pac_files)
                if children:
                    filtered.append((header, children))
            if filtered:
                has_grouped = True
                hits += len(filtered)
                self._all_rows.append(_CHAR_SECTION)
                for header, children in filtered:
                    self._all_rows.append(header)
                    self._all_rows.extend(children)

        if item_rows:
            filtered = []
            for header in item_rows:
                children = self._resolve_pac_children(header.pac_files)
                if children:
                    filtered.append((header, children))
            if filtered:
                has_grouped = True
                hits += len(filtered)
                self._all_rows.append(_ITEM_SECTION)
                for header, children in filtered:
                    self._all_rows.append(header)
                    self._all_rows.extend(children)

        hits += len(model_exact) + len(model_fuzzy)
        if model_exact or model_fuzzy:
            if has_grouped:
                self._all_rows.append(_MODEL_SECTION)
            self._all_rows.extend(model_exact)
            if model_exact and model_fuzzy:
                self._all_rows.append(_SEPARATOR_SENTINEL)
            self._all_rows.extend(model_fuzzy)

        self._match_count = hits
        self._rows = self._all_rows[:self._PAGE_SIZE]
        self.endResetModel()

    def match_count(self) -> int:
        """Actual search hits (headers + model entries, not children/sentinels)."""
        return self._match_count

    def total_row_count(self) -> int:
        """Total rows including not-yet-paged ones (for paging logic)."""
        return len(self._all_rows)

    def can_load_more(self) -> bool:
        return len(self._rows) < len(self._all_rows)

    def load_more(self):
        """Append next page of rows."""
        if not self.can_load_more():
            return
        cur = len(self._rows)
        nxt = min(cur + self._PAGE_SIZE, len(self._all_rows))
        self.beginInsertRows(QModelIndex(), cur, nxt - 1)
        self._rows = self._all_rows[:nxt]
        self.endInsertRows()

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self._rows[index.row()]

        # Sentinel rows
        if item is _SEPARATOR_SENTINEL:
            return "\u2500  closest matches  \u2500" if role == Qt.ItemDataRole.DisplayRole else None
        if item is _ITEM_SECTION:
            return "ITEMS" if role == Qt.ItemDataRole.DisplayRole else None
        if item is _CHAR_SECTION:
            return "CHARACTERS" if role == Qt.ItemDataRole.DisplayRole else None
        if item is _MODEL_SECTION:
            return "MODELS" if role == Qt.ItemDataRole.DisplayRole else None

        # Item header row
        if isinstance(item, _ItemHeaderRow):
            if role == Qt.ItemDataRole.DisplayRole:
                dlc_tag = "  [DLC]" if item.is_dlc else ""
                return f"  {item.display_name}  ({item.internal_name}){dlc_tag}"
            if role == Qt.ItemDataRole.UserRole:
                return item
            return None

        # Character header row
        if isinstance(item, _CharHeaderRow):
            if role == Qt.ItemDataRole.DisplayRole:
                dlc_tag = "  [DLC]" if item.is_dlc else ""
                return f"  {item.display_label}  ({item.app_name}){dlc_tag}"
            if role == Qt.ItemDataRole.UserRole:
                return item
            return None

        # Item child row (PAC file under an item)
        if isinstance(item, _ItemChildRow):
            if role == Qt.ItemDataRole.DisplayRole:
                glyph = "\u2514\u2500 " if item.is_last else "\u251c\u2500 "
                dlc_tag = "  [DLC]" if item.catalog_entry.is_dlc else ""
                return f"      {glyph}{item.catalog_entry.display_name}{dlc_tag}"
            if role == Qt.ItemDataRole.UserRole:
                return item.catalog_entry
            return None

        # Standard CatalogEntry
        if role == Qt.ItemDataRole.DisplayRole:
            dlc_tag = "  [DLC]" if item.is_dlc else ""
            if self._show_tags:
                tag = "PAC" if item.file_type == "pac" else "PAM"
                return f"{item.display_name}  [{tag}]{dlc_tag}"
            return f"{item.display_name}{dlc_tag}" if dlc_tag else item.display_name
        if role == Qt.ItemDataRole.UserRole:
            return item
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        item = self._rows[index.row()]
        if item in (_SEPARATOR_SENTINEL, _ITEM_SECTION, _CHAR_SECTION, _MODEL_SECTION):
            return Qt.ItemFlag.NoItemFlags
        if isinstance(item, (_ItemHeaderRow, _CharHeaderRow)):
            return Qt.ItemFlag.ItemIsEnabled  # clickable, previews first PAC
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class SeparatorDelegate(QStyledItemDelegate):
    """Custom delegate for separator, section header, and item rows."""

    def _draw_section(self, painter, option, text, color_hex="#b0a060"):
        """Draw a section header row (ITEMS / MODELS)."""
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(option.palette.base())
        painter.drawRect(option.rect)
        painter.setPen(QColor(color_hex))
        font = QFont(option.font)
        font.setPointSize(font.pointSize() - 1)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(option.rect.adjusted(8, 0, 0, 0),
                         Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

    def paint(self, painter, option, index):
        item = index.model()._rows[index.row()] if index.isValid() else None

        if item is _CHAR_SECTION:
            self._draw_section(painter, option, "\u25c6  CHARACTERS", "#70b080")
            return
        if item is _ITEM_SECTION:
            self._draw_section(painter, option, "\u25c6  ITEMS", "#c0a050")
            return
        if item is _MODEL_SECTION:
            self._draw_section(painter, option, "\u25c6  MODELS", "#7090b0")
            return
        if item is _SEPARATOR_SENTINEL:
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(option.palette.base())
            painter.drawRect(option.rect)
            painter.setPen(option.palette.color(option.palette.ColorRole.PlaceholderText))
            font = QFont(option.font)
            font.setPointSize(font.pointSize() - 1)
            font.setItalic(True)
            painter.setFont(font)
            painter.drawText(option.rect.adjusted(8, 0, 0, 0),
                             Qt.AlignmentFlag.AlignVCenter, "\u2500  closest matches  \u2500")
            painter.restore()
            return
        if isinstance(item, _ItemHeaderRow):
            painter.save()
            # Draw selection/hover background
            if option.state & QStyle.StateFlag.State_Selected:
                painter.fillRect(option.rect, option.palette.highlight())
            else:
                painter.fillRect(option.rect, option.palette.base())

            font = QFont(option.font)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#e0d0a0"))
            r = option.rect.adjusted(12, 0, 0, 0)
            painter.drawText(r, Qt.AlignmentFlag.AlignVCenter, item.display_name)
            # Draw internal name in dim
            fm = painter.fontMetrics()
            name_w = fm.horizontalAdvance(item.display_name + "  ")
            font.setBold(False)
            font.setPointSize(font.pointSize() - 1)
            painter.setFont(font)
            painter.setPen(QColor("#808080"))
            tag_text = item.internal_name + ("  [DLC]" if item.is_dlc else "")
            painter.drawText(r.adjusted(name_w, 0, 0, 0),
                             Qt.AlignmentFlag.AlignVCenter, tag_text)
            painter.restore()
            return
        if isinstance(item, _CharHeaderRow):
            painter.save()
            if option.state & QStyle.StateFlag.State_Selected:
                painter.fillRect(option.rect, option.palette.highlight())
            else:
                painter.fillRect(option.rect, option.palette.base())

            font = QFont(option.font)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#90d0a0"))
            r = option.rect.adjusted(12, 0, 0, 0)
            painter.drawText(r, Qt.AlignmentFlag.AlignVCenter, item.display_label)
            fm = painter.fontMetrics()
            name_w = fm.horizontalAdvance(item.display_label + "  ")
            font.setBold(False)
            font.setPointSize(font.pointSize() - 1)
            painter.setFont(font)
            painter.setPen(QColor("#808080"))
            tag_text = item.app_name + ("  [DLC]" if item.is_dlc else "")
            painter.drawText(r.adjusted(name_w, 0, 0, 0),
                             Qt.AlignmentFlag.AlignVCenter, tag_text)
            painter.restore()
            return
        if isinstance(item, _ItemChildRow):
            painter.save()
            if option.state & QStyle.StateFlag.State_Selected:
                painter.fillRect(option.rect, option.palette.highlight())
            else:
                painter.fillRect(option.rect, option.palette.base())

            glyph = "\u2514\u2500 " if item.is_last else "\u251c\u2500 "
            dlc_tag = "  [DLC]" if item.catalog_entry.is_dlc else ""
            painter.setPen(QColor("#909090"))
            painter.drawText(option.rect.adjusted(24, 0, 0, 0),
                             Qt.AlignmentFlag.AlignVCenter,
                             f"{glyph}{item.catalog_entry.display_name}{dlc_tag}")
            painter.restore()
            return

        super().paint(painter, option, index)

    def sizeHint(self, option, index):
        item = index.model()._rows[index.row()] if index.isValid() else None
        if item in (_SEPARATOR_SENTINEL, _ITEM_SECTION, _CHAR_SECTION, _MODEL_SECTION):
            return QSize(option.rect.width(), 24)
        return super().sizeHint(option, index)


class CatalogWorker(QThread):
    catalog_ready = Signal(list, list, object, object)  # (catalog, entries, item_index, char_index)
    progress = Signal(str)
    failed = Signal(str)

    def __init__(self, game_dir, parent=None):
        super().__init__(parent)
        self._game_dir = game_dir

    def run(self):
        try:
            catalog, all_entries = build_catalog(self._game_dir,
                                                 progress_fn=self.progress.emit)
            # Build item index in same thread -- available immediately
            item_index = None
            char_index = None
            try:
                item_index = build_item_index(self._game_dir, all_entries,
                                              progress_fn=self.progress.emit)
            except Exception as ei:
                import traceback
                traceback.print_exc()
                self.progress.emit(f"Item index failed: {ei}")

            # Build character index using prefab map from item index
            try:
                prefab_map = item_index.prefab_pac_map if item_index else {}
                if not prefab_map:
                    prefab_map = build_prefab_pac_map(all_entries, self.progress.emit)
                char_index = build_character_index(all_entries, prefab_map,
                                                   progress_fn=self.progress.emit)
            except Exception as ec:
                import traceback
                traceback.print_exc()
                self.progress.emit(f"Character index failed: {ec}")

            self.catalog_ready.emit(catalog, all_entries, item_index, char_index)
        except Exception as e:
            self.failed.emit(str(e))
