import numpy as np
from dataclasses import dataclass, field
from enum import Enum, auto


@dataclass
class CatalogEntry:
    filename: str
    display_name: str
    paz_entry: "PazEntry"
    search_key: str
    file_type: str = "pac"        # "pac" or "pam"
    category: str = "characters"  # "characters", "objects", "effects", "terrain"
    is_dlc: bool = False


@dataclass
class ItemSearchEntry:
    """Item database record wrapped for search compatibility."""
    search_key: str
    record: object  # ItemRecord from item_db


@dataclass
class GpuMesh:
    positions: "np.ndarray"
    normals: "np.ndarray"
    indices: "np.ndarray"
    center: "np.ndarray"
    radius: float


class RenderMode(Enum):
    SOLID = auto()
    WIREFRAME = auto()
    NORMALS = auto()
    UV = auto()


@dataclass
class SubmeshInfo:
    name: str
    material_name: str
    index_offset: int     # byte offset into index buffer for glDrawElements
    index_count: int
    visible: bool = True
    highlighted: bool = False
    base_color: tuple = (0.72, 0.72, 0.76)


@dataclass
class SceneMesh:
    """Rich mesh data for the viewer — replaces GpuMesh for new code."""
    positions: "np.ndarray"    # (N, 3) float32
    normals: "np.ndarray"      # (N, 3) float32
    uvs: "np.ndarray"          # (N, 2) float32
    indices: "np.ndarray"      # (M,) uint32
    submeshes: list            # list[SubmeshInfo]
    center: "np.ndarray"
    radius: float
    available_lods: list       # list[int]
    current_lod: int = 0


_SEPARATOR_SENTINEL = object()  # unique marker for separator rows
_ITEM_SECTION = object()        # "ITEMS" section header
_CHAR_SECTION = object()        # "CHARACTERS" section header
_MODEL_SECTION = object()       # "MODELS" section header


@dataclass
class _ItemHeaderRow:
    """Item name row in search results."""
    display_name: str
    internal_name: str
    pac_files: list  # list of pac filename strings
    is_dlc: bool = False


@dataclass
class _ItemChildRow:
    """PAC file shown as child of an item header."""
    catalog_entry: CatalogEntry  # the actual model entry
    is_last: bool                # for tree glyph rendering


@dataclass
class _CharHeaderRow:
    """Character appearance row in search results."""
    display_label: str   # e.g. "macduff" or "bear"
    app_name: str        # e.g. "cd_phm_macduff_00000"
    pac_files: list      # list of pac filename strings
    is_dlc: bool = False


def fuzzy_match(query: str, target: str) -> tuple[bool, int]:
    """Subsequence fuzzy match like VS Code Ctrl+P.

    Returns (matched, score). Higher score = better match.
    Bonuses for: consecutive chars, match after '_' boundary, match at start.
    """
    qi = 0
    qlen = len(query)
    if qlen == 0:
        return True, 0

    score = 0
    prev_matched = False
    consecutive = 0

    for ti, ch in enumerate(target):
        if qi < qlen and ch == query[qi]:
            qi += 1
            # Consecutive bonus
            if prev_matched:
                consecutive += 1
                score += consecutive * 3
            else:
                consecutive = 1
            # Boundary bonus: start of string or after '_'
            if ti == 0 or target[ti - 1] == '_':
                score += 5
            score += 1
            prev_matched = True
        else:
            prev_matched = False
            consecutive = 0

    if qi < qlen:
        return False, 0
    return True, score


class TrigramIndex:
    """Trigram inverted index for fast substring search over a fixed set of strings.

    Build once, then query with intersect_entries(term) to get all entries
    whose search_key contains that term as a substring. For terms shorter
    than 3 chars, falls back to linear scan of a smaller candidate set.
    """

    def __init__(self, entries: list):
        self._entries = entries
        self._index: dict[str, set[int]] = {}
        for i, e in enumerate(entries):
            key = e.search_key
            for j in range(len(key) - 2):
                tri = key[j:j+3]
                if tri not in self._index:
                    self._index[tri] = set()
                self._index[tri].add(i)

    def substring_matches(self, term: str) -> list:
        """Return entries whose search_key contains term as a substring."""
        if len(term) < 3:
            # Fallback: linear scan (but still fast — short terms are rare as sole query)
            return [e for e in self._entries if term in e.search_key]

        # Intersect posting lists for all trigrams in the term
        trigrams = [term[j:j+3] for j in range(len(term) - 2)]
        # Start with smallest posting list for efficiency
        sets = [self._index.get(tri) for tri in trigrams]
        if any(s is None for s in sets):
            return []
        sets.sort(key=len)
        candidates = sets[0]
        for s in sets[1:]:
            candidates = candidates & s
            if not candidates:
                return []

        # Verify actual substring match (trigram intersection can have false positives)
        return [self._entries[i] for i in candidates if term in self._entries[i].search_key]

    def multi_term_matches(self, terms: list[str]) -> list:
        """Return entries matching ALL terms as substrings."""
        if not terms:
            return list(self._entries)
        # Start with the longest term (most selective)
        sorted_terms = sorted(terms, key=len, reverse=True)
        result = self.substring_matches(sorted_terms[0])
        for term in sorted_terms[1:]:
            result = [e for e in result if term in e.search_key]
            if not result:
                return []
        return result
