"""复用相同数据库正文的不可变模型；不缓存 SQL 读取或发布证明。"""

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from typing import cast
from weakref import WeakKeyDictionary

from sqlalchemy import Connection, Engine

from app.domain.base import FrozenModel


@dataclass(frozen=True)
class _Entry:
    body_digest: bytes
    value: FrozenModel
    body_bytes: int


class DecodedModels:
    """至多三个对象，按来源 UTF-8 大小限制 2 MiB；仅保存指纹，不留正文。"""

    def __init__(self, *, max_body_bytes: int = 2 * 1024 * 1024) -> None:
        self.max_body_bytes = max_body_bytes
        self.retained_body_bytes = 0
        self._entries: OrderedDict[type[FrozenModel], _Entry] = OrderedDict()
        self._lock = Lock()

    def decode[Model: FrozenModel](self, model: type[Model], body: str) -> Model:
        with self._lock:
            encoded = body.encode("utf-8")
            size, digest = len(encoded), sha256(encoded).digest()
            del encoded
            entry = self._entries.get(model)
            if entry is not None and entry.body_digest == digest:
                self._entries.move_to_end(model)
                return cast(Model, entry.value)
            # 新正文在严格解析前撤下旧对象，失败不能回退成旧状态。
            if entry is not None:
                self.retained_body_bytes -= entry.body_bytes
                del self._entries[model]
            del entry
            if size > self.max_body_bytes:
                return model.model_validate_json(body)
            while self._entries and (
                len(self._entries) >= 3 or self.retained_body_bytes + size > self.max_body_bytes
            ):
                _, removed = self._entries.popitem(last=False)
                self.retained_body_bytes -= removed.body_bytes
                del removed
            value = model.model_validate_json(body)
            self._entries[model] = _Entry(digest, value, size)
            self.retained_body_bytes += size
            return value


_registry: WeakKeyDictionary[Engine, DecodedModels] = WeakKeyDictionary()
_registry_lock = Lock()


def decode_model[Model: FrozenModel](
    connection: Connection, model: type[Model], body: str
) -> Model:
    with _registry_lock:
        cache = _registry.get(connection.engine)
        if cache is None:
            cache = DecodedModels()
            _registry[connection.engine] = cache
    return cache.decode(model, body)


def release_decoded_models(engine: Engine) -> None:
    with _registry_lock:
        _registry.pop(engine, None)
