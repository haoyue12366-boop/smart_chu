"""内存只读菜单查询与版本租约；没有 Neo4j、HTTP 或求解器依赖。"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from app.domain.ids import RecipeId
from app.domain.knowledge import MenuKnowledgeView, ReleaseRef, SnapshotHandle
from app.knowledge.loader import LoadedRelease, load_release


class SnapshotLease:
    def __init__(self, ref: ReleaseRef, loaded: LoadedRelease) -> None:
        self.ref = ref
        self.loaded = loaded

    def select(self, recipe_ids: tuple[RecipeId, ...]) -> MenuKnowledgeView:
        source = self.loaded.snapshot.knowledge
        by_id = {r.recipe_id: r for r in source.recipes}
        selected = tuple(dict.fromkeys(recipe_ids))
        if not selected or not set(selected) <= by_id.keys():
            raise ValueError("菜单为空或含发布中不存在的菜谱")
        return MenuKnowledgeView(
            release=self.ref,
            snapshot_schema_version=self.loaded.snapshot.snapshot_schema_version,
            snapshot_hash=self.loaded.snapshot.content_hash,
            recipes=tuple(by_id[rid] for rid in selected),
            devices=source.scope.devices,
            profiles=source.profiles,
            rules=source.rules,
            provenance_index=self.loaded.index.evidence,
            device_choices=source.scope.device_choices,
            recipe_contexts=tuple(
                c for c in source.scope.recipe_contexts if c.recipe_id in selected
            ),
        )


class SnapshotKnowledgeRepository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[ReleaseRef, LoadedRelease] = {}
        self._references: dict[ReleaseRef, int] = {}
        self._current: ReleaseRef | None = None
        self._lock = RLock()

    def _get(self, ref: ReleaseRef) -> LoadedRelease:
        if ref not in self._cache:
            self._cache[ref] = load_release(self.root, ref)
        return self._cache[ref]

    def load(self, release: ReleaseRef) -> SnapshotHandle:
        with self._lock:
            loaded = self._get(release)
            self._current = release
            return SnapshotHandle(
                release=release,
                snapshot_schema_version=loaded.snapshot.snapshot_schema_version,
                snapshot_hash=loaded.snapshot.content_hash,
                recipe_count=len(loaded.snapshot.knowledge.recipes),
            )

    def select(self, recipe_ids: tuple[RecipeId, ...]) -> MenuKnowledgeView:
        with self._lock:
            if self._current is None:
                raise ValueError("知识仓储尚未加载任何有效发布")
            return SnapshotLease(self._current, self._get(self._current)).select(recipe_ids)

    @contextmanager
    def acquire(self, release: ReleaseRef) -> Iterator[SnapshotLease]:
        with self._lock:
            loaded = self._get(release)
            self._references[release] = self._references.get(release, 0) + 1
        try:
            yield SnapshotLease(release, loaded)
        finally:
            with self._lock:
                self._references[release] -= 1

    def unload(self, release: ReleaseRef) -> None:
        with self._lock:
            if self._current == release or self._references.get(release, 0):
                raise ValueError("仍被当前版本或活动会话引用，不能卸载")
            self._cache.pop(release, None)
            self._references.pop(release, None)

    def trim_unused_cache(self) -> int:
        """仅释放无租约的物化对象；当前发布绑定不变，后续重新加载仍完整核验。"""
        with self._lock:
            unused = tuple(ref for ref in self._cache if not self._references.get(ref, 0))
            for ref in unused:
                del self._cache[ref]
            return len(unused)
