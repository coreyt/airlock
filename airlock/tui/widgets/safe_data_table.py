"""_SafeDataTable — DataTable subclass that guards against an LRU cache race.

Textual bug: DataTable._y_offsets checks `_update_count in _offset_cache` then
immediately reads `_offset_cache[_update_count]`.  The LRU cache has maxsize=1,
so a concurrent worker thread bumping _update_count between those two operations
evicts the entry and causes a KeyError that crashes the render loop.

Fix: catch KeyError and fall back to recomputing the offsets directly, the same
way the else-branch of the original property does.
"""

from __future__ import annotations

from textual.widgets import DataTable


class _SafeDataTable(DataTable):
    """DataTable with a bounds guard on _y_offsets for thread-safety."""

    @property
    def _y_offsets(self):
        try:
            return super()._y_offsets
        except KeyError:
            # Cache miss due to thread race — recompute without caching.
            y_offsets: list[tuple] = []
            for row in self.ordered_rows:
                y_offsets += [(row.key, y) for y in range(row.height)]
            return y_offsets
