"""Loro engines behind the replicated SPI."""

import base64
import json
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import version as distribution_version
from typing import Any

from squid_reactivity import ConflictDetail
from squid_replication.document import ReplicationBackendIntegrityError, ReplicationCorruptUpdateError
from squid_replication.model import (
    ReplicatedItem,
    ReplicatedSnapshot,
    ReplicatedTreeNode,
    ReplicatedTreeSnapshot,
    freeze_value,
    thaw_value,
)

_MAX_UPDATE_BYTES = 1_048_576
_MAX_OPERATIONS = 10_000
_MAX_PATH_BYTES = 512
_ROOT_PREFIX = "$squid$"


@dataclass(frozen=True, slots=True)
class LoroTextOperation:
    kind: str
    index: int
    value: str | int


@dataclass(frozen=True, slots=True)
class LoroPrepared:
    base: bytes | None
    update: bytes
    before: bytes
    after: bytes


@dataclass(frozen=True, slots=True)
class LoroChangeToken:
    before: bytes
    after: bytes

    def encode(self) -> bytes:
        before_size = len(self.before).to_bytes(4, "big")
        return before_size + self.before + self.after

    @classmethod
    def decode(cls, data: bytes) -> LoroChangeToken:
        size = int.from_bytes(data[:4], "big")
        return cls(data[4 : 4 + size], data[4 + size :])


class LoroTextBranch:
    def __init__(self, engine: LoroTextEngine) -> None:
        self._engine = engine
        self.doc = engine.doc.fork()
        self.doc.peer_id = engine.doc.peer_id
        self._base_vv = engine.doc.oplog_vv
        self._before = engine.doc.oplog_frontiers.encode()

    def apply(self, operation: LoroTextOperation) -> None:
        text = self.doc.get_text("text")
        if operation.kind == "insert":
            assert isinstance(operation.value, str)
            text.insert(operation.index, operation.value)
        elif operation.kind == "delete":
            assert isinstance(operation.value, int)
            text.delete(operation.index, operation.value)
        else:
            message = f"unknown Loro text operation {operation.kind!r}"
            raise ValueError(message)

    def snapshot(self) -> str:
        return self.doc.get_text("text").to_string()

    def prepare(self, base: object) -> LoroPrepared:
        if base != self._before or self._engine.version() != self._before:
            message = "Loro document changed after this branch was staged"
            raise RuntimeError(message)
        loro = self._engine.module
        self.doc.commit()
        update = self.doc.export(loro.ExportMode.Updates(self._base_vv))
        return LoroPrepared(self._before, update, self._before, self.doc.oplog_frontiers.encode())


class LoroTextEngine:
    """A Loro text adapter used by the backend gate; public Squid values remain strings."""

    backend_id = "loro-text-v1"

    def __init__(self) -> None:
        try:
            import loro
        except ImportError as error:
            message = "install squid-replication[loro] to use LoroTextEngine"
            raise RuntimeError(message) from error
        self.module = loro
        self.doc = loro.LoroDoc()
        self.doc.get_text("text")

    def snapshot(self) -> str:
        return self.doc.get_text("text").to_string()

    def version(self) -> bytes:
        return self.doc.oplog_frontiers.encode()

    def branch(self) -> LoroTextBranch:
        return LoroTextBranch(self)

    def apply(self, prepared: LoroPrepared) -> LoroChangeToken:
        self.doc.import_(prepared.update)
        return LoroChangeToken(prepared.before, prepared.after)

    def prepare_remote(self, update: bytes) -> LoroPrepared:
        before = self.version()
        branch = self.doc.fork()
        branch.import_(update)
        return LoroPrepared(None, update, before, branch.oplog_frontiers.encode())

    def export_since(self, version: object | None = None) -> bytes:
        if version is None:
            mode = self.module.ExportMode.Snapshot()
        else:
            frontiers = self.module.Frontiers.decode(version)
            mode = self.module.ExportMode.Updates(self.doc.frontiers_to_vv(frontiers))
        return self.doc.export(mode)

    def plan_inverse(self, token: LoroChangeToken) -> LoroPrepared:
        before = self.module.Frontiers.decode(token.before)
        after = self.module.Frontiers.decode(token.after)
        branch = self.doc.fork()
        branch.peer_id = self.doc.peer_id
        base_vv = self.doc.oplog_vv
        branch.apply_diff(branch.diff(after, before))
        branch.commit()
        update = branch.export(self.module.ExportMode.Updates(base_vv))
        return LoroPrepared(self.version(), update, self.version(), branch.oplog_frontiers.encode())


@dataclass(frozen=True, slots=True)
class LoroOperation:
    """One semantic mutation staged through the production Loro adapter."""

    identity: str
    kind: str
    path: str
    data: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class LoroDocumentToken:
    """One durable, action-addressable Loro inverse token."""

    before: bytes
    after: bytes
    operations: tuple[LoroOperation, ...]
    shallow_root: bytes
    backend_version: str


@dataclass(frozen=True, slots=True)
class LoroDocumentPrepared:
    base: bytes | None
    update: bytes
    before: bytes
    after: bytes
    operations: tuple[LoroOperation, ...]


@dataclass(frozen=True, slots=True)
class LoroDocumentInverse:
    operations: tuple[LoroOperation, ...]
    safe_diff: object
    safe_containers: tuple[tuple[str, str], ...]


def _operation(kind: str, path: str, **data: Any) -> LoroOperation:
    return LoroOperation(str(uuid.uuid7()), kind, path, _json_object(data))


def _json_value(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    return value


def _json_object(value: Mapping[str, object]) -> dict[str, Any]:
    return {key: _json_value(item) for key, item in value.items()}


def _root_name(kind: str, path: str) -> str:
    encoded = base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")
    return f"{_ROOT_PREFIX}{kind}${encoded}"


def _parse_root(name: str) -> tuple[str, str] | None:
    if not name.startswith(_ROOT_PREFIX):
        return None
    try:
        _, _, kind, encoded = name.split("$", 3)
        padding = "=" * (-len(encoded) % 4)
        return kind, base64.urlsafe_b64decode(encoded + padding).decode()
    except UnicodeDecodeError, ValueError:
        return None


def _lineage(identity: str | None) -> int:
    return -1 if identity is None else uuid.UUID(identity).int


def _lineage_from_frontier(frontier: bytes) -> int:
    return int.from_bytes(frontier[:16], "big")


def _freeze_map_value(value: object) -> object:
    return thaw_value(freeze_value(value))


def _register_value(operation: LoroOperation, value: object, *, present: bool) -> dict[str, object]:
    return {
        "$squid": 1,
        "authority": operation.identity,
        "present": present,
        "value": _freeze_map_value(value) if present else None,
    }


def _decode_register(value: object) -> tuple[str, bool, object] | None:
    if not isinstance(value, dict):
        return None
    authority = value.get("authority")
    present = value.get("present")
    if value.get("$squid") != 1 or not isinstance(authority, str) or not isinstance(present, bool):
        return None
    return authority, present, value.get("value")


def _item_value(operation: LoroOperation, item_id: str, value: object) -> dict[str, object]:
    return {
        "$squid": 1,
        "authority": operation.identity,
        "id": item_id,
        "value": _freeze_map_value(value),
    }


def _decode_item(value: object) -> tuple[str, str, object] | None:
    if not isinstance(value, dict) or value.get("$squid") != 1:
        return None
    item_id = value.get("id")
    authority = value.get("authority")
    if not isinstance(item_id, str) or not isinstance(authority, str):
        return None
    return item_id, authority, value.get("value")


def _authority_root(kind: str, path: str) -> str:
    return _root_name(f"{kind}-authority", path)


def _find_item(container: Any, item_id: str) -> tuple[int, tuple[str, str, object]] | None:
    values = container.get_deep_value()
    if not isinstance(values, list):
        return None
    for index, value in enumerate(values):
        decoded = _decode_item(value)
        if decoded is not None and decoded[0] == item_id:
            return index, decoded
    return None


def _anchors(container: Any, index: int, count: int = 1) -> tuple[str | None, str | None]:
    values = container.get_deep_value()
    if not isinstance(values, list):
        return None, None
    before = _decode_item(values[index - 1]) if index > 0 else None
    after_index = index + count
    after = _decode_item(values[after_index]) if after_index < len(values) else None
    return (None if before is None else before[0], None if after is None else after[0])


def _anchor_index(container: Any, before_id: object, after_id: object) -> int:
    if isinstance(after_id, str):
        found = _find_item(container, after_id)
        if found is not None:
            return found[0]
    if isinstance(before_id, str):
        found = _find_item(container, before_id)
        if found is not None:
            return found[0] + 1
    return len(container)


def _tree_lookup(tree: Any, logical_id: str) -> Any | None:
    for node_id in tree.nodes():
        if tree.is_node_deleted(node_id):
            continue
        if tree.get_meta(node_id).get_value().get("$id") == logical_id:
            return node_id
    return None


def _tree_logical_id(tree: Any, node_id: Any | None) -> str | None:
    if node_id is None:
        return None
    value = tree.get_meta(node_id).get_value().get("$id")
    return value if isinstance(value, str) else None


def _tree_subtree(tree: Any, target: Any) -> dict[str, Any]:
    metadata = tree.get_meta(target).get_value()
    logical_id = metadata.get("$id")
    if not isinstance(logical_id, str):
        message = "tree node is missing its logical identity"
        raise TypeError(message)
    parent = tree.parent(target)
    index = next(node.index for node in tree.get_nodes(with_deleted=False) if node.id == target)
    return {
        "node_id": logical_id,
        "parent_id": _tree_logical_id(tree, parent),
        "index": index,
        "metadata": {key: value for key, value in metadata.items() if key != "$id"},
        "children": [_tree_subtree(tree, child) for child in (tree.children(target) or [])],
    }


def _restore_tree_subtree(tree: Any, subtree: Mapping[str, Any], operation: LoroOperation) -> Any:
    parent_id = subtree.get("parent_id")
    parent = None if parent_id is None else _tree_lookup(tree, parent_id)
    index = subtree.get("index")
    created = tree.create(parent) if index is None else tree.create_at(index, parent)
    metadata = tree.get_meta(created)
    metadata.insert("$id", subtree["node_id"])
    for key, value in subtree["metadata"].items():
        decoded = _decode_register(value)
        metadata.insert(
            key,
            value if decoded is not None else _register_value(operation, value, present=True),
        )
    for child in subtree["children"]:
        restored = dict(child)
        restored["parent_id"] = subtree["node_id"]
        _restore_tree_subtree(tree, restored, operation)
    return created


class LoroDocumentBranch:
    """An isolated Loro branch discarded unless its exact base commits."""

    def __init__(self, engine: LoroEngine) -> None:
        self._engine = engine
        self.doc = engine.doc.fork()
        self.doc.peer_id = engine.peer_id
        self._base_vv = engine.doc.oplog_vv
        self._before = engine.version()
        self._operations: list[LoroOperation] = []

    @property
    def base(self) -> bytes:
        return self._before

    @property
    def operations(self) -> tuple[LoroOperation, ...]:
        return tuple(self._operations)

    def apply(self, operation: LoroOperation) -> None:
        applied = self._apply(operation)
        self._operations.append(applied)

    def _apply(self, operation: LoroOperation) -> LoroOperation:
        data = dict(operation.data)
        if operation.kind == "increment":
            self.doc.get_map(_root_name("counter", operation.path)).insert(operation.identity, data["value"])
        elif operation.kind == "add":
            self.doc.get_map(_root_name("set", operation.path)).insert(f"a:{operation.identity}", data["value"])
        elif operation.kind == "remove":
            self.doc.get_map(_root_name("set", operation.path)).insert(
                f"r:{operation.identity}",
                {"tags": list(data["tags"]), "value": data["value"]},
            )
        elif operation.kind == "restore":
            self.doc.get_map(_root_name("set", operation.path)).insert(
                f"u:{operation.identity}",
                {"removal": data["undoes"], "tags": list(data["tags"]), "value": data["value"]},
            )
        elif operation.kind == "text_insert":
            self.doc.get_text(_root_name("text", operation.path)).insert(data["index"], data["value"])
        elif operation.kind == "text_delete":
            self.doc.get_text(_root_name("text", operation.path)).delete(data["index"], data["count"])
        elif operation.kind == "list_insert":
            container = self.doc.get_list(_root_name("list", operation.path))
            item_id = str(data.get("item_id") or uuid.uuid7())
            data["item_id"] = item_id
            container.insert(data["index"], _item_value(operation, item_id, data["value"]))
            self.doc.get_map(_authority_root("list", operation.path)).insert(item_id, operation.identity)
        elif operation.kind == "list_delete":
            container = self.doc.get_list(_root_name("list", operation.path))
            before_id, after_id = _anchors(container, data["index"], data["count"])
            values = container.get_deep_value()[data["index"] : data["index"] + data["count"]]
            decoded = [_decode_item(value) for value in values]
            if any(item is None for item in decoded):
                message = f"list {operation.path!r} contains a malformed item"
                raise TypeError(message)
            data.update(before_id=before_id, after_id=after_id, items=decoded)
            authority = self.doc.get_map(_authority_root("list", operation.path))
            for item in decoded:
                assert item is not None
                authority.insert(item[0], operation.identity)
            container.delete(data["index"], len(decoded))
        elif operation.kind == "list_delete_ids":
            container = self.doc.get_list(_root_name("list", operation.path))
            deleted: list[tuple[str, str, object]] = []
            positions = sorted(
                (found for item_id in data["item_ids"] if (found := _find_item(container, item_id)) is not None),
                reverse=True,
            )
            if positions:
                lowest = positions[-1][0]
                before_id, after_id = _anchors(container, lowest, len(positions))
                data.update(before_id=before_id, after_id=after_id)
            authority = self.doc.get_map(_authority_root("list", operation.path))
            for index, item in positions:
                deleted.append(item)
                authority.insert(item[0], operation.identity)
                container.delete(index, 1)
            data["items"] = list(reversed(deleted))
        elif operation.kind == "list_restore":
            container = self.doc.get_list(_root_name("list", operation.path))
            index = _anchor_index(container, data.get("before_id"), data.get("after_id"))
            authority = self.doc.get_map(_authority_root("list", operation.path))
            for offset, item in enumerate(data["items"]):
                item_id, _, value = item
                container.insert(index + offset, _item_value(operation, item_id, value))
                authority.insert(item_id, operation.identity)
            data["item_ids"] = [item[0] for item in data["items"]]
        elif operation.kind in {"list_replace", "list_replace_by_id"}:
            container = self.doc.get_list(_root_name("list", operation.path))
            found = (
                _find_item(container, data["item_id"])
                if operation.kind == "list_replace_by_id"
                else (data["index"], _decode_item(container.get_deep_value()[data["index"]]))
            )
            if found is None or found[1] is None:
                message = f"list item is unavailable in {operation.path!r}"
                raise ValueError(message)
            index, item = found
            item_id, previous_authority, previous_value = item
            data.update(item_id=item_id, previous_authority=previous_authority, previous_value=previous_value)
            container.delete(index, 1)
            container.insert(index, _item_value(operation, item_id, data["value"]))
            self.doc.get_map(_authority_root("list", operation.path)).insert(item_id, operation.identity)
        elif operation.kind == "movable_insert":
            container = self.doc.get_movable_list(_root_name("movable", operation.path))
            container.insert(data["index"], _item_value(operation, data["item_id"], data["value"]))
            self.doc.get_map(_authority_root("movable", operation.path)).insert(data["item_id"], operation.identity)
        elif operation.kind in {"movable_delete", "movable_delete_ids"}:
            container = self.doc.get_movable_list(_root_name("movable", operation.path))
            item_ids = [data["item_id"]] if operation.kind == "movable_delete" else data["item_ids"]
            deleted: list[tuple[str, str, object]] = []
            authority = self.doc.get_map(_authority_root("movable", operation.path))
            for item_id in item_ids:
                found = _find_item(container, item_id)
                if found is None:
                    message = f"movable item {item_id!r} is unavailable"
                    raise ValueError(message)
                index, item = found
                before_id, after_id = _anchors(container, index)
                data.setdefault("before_id", before_id)
                data.setdefault("after_id", after_id)
                deleted.append(item)
                authority.insert(item_id, operation.identity)
                container.delete(index, 1)
            data.update(item_ids=item_ids, items=deleted)
        elif operation.kind == "movable_restore":
            container = self.doc.get_movable_list(_root_name("movable", operation.path))
            index = _anchor_index(container, data.get("before_id"), data.get("after_id"))
            authority = self.doc.get_map(_authority_root("movable", operation.path))
            for offset, item in enumerate(data["items"]):
                item_id, _, value = item
                container.insert(index + offset, _item_value(operation, item_id, value))
                authority.insert(item_id, operation.identity)
            data["item_ids"] = [item[0] for item in data["items"]]
        elif operation.kind == "movable_move":
            container = self.doc.get_movable_list(_root_name("movable", operation.path))
            found = _find_item(container, data["item_id"])
            if found is None:
                message = f"movable item {data['item_id']!r} is unavailable"
                raise ValueError(message)
            old_index, _ = found
            before_id, after_id = _anchors(container, old_index)
            data.update(previous_before_id=before_id, previous_after_id=after_id)
            container.mov(old_index, data["index"])
            self.doc.get_map(_authority_root("movable", operation.path)).insert(data["item_id"], operation.identity)
        elif operation.kind == "movable_move_between":
            container = self.doc.get_movable_list(_root_name("movable", operation.path))
            found = _find_item(container, data["item_id"])
            if found is None:
                message = f"movable item {data['item_id']!r} is unavailable"
                raise ValueError(message)
            old_index, _ = found
            previous_before, previous_after = _anchors(container, old_index)
            container.mov(
                old_index,
                _anchor_index(container, data.get("before_id"), data.get("after_id")),
            )
            data.update(previous_before_id=previous_before, previous_after_id=previous_after)
            self.doc.get_map(_authority_root("movable", operation.path)).insert(data["item_id"], operation.identity)
        elif operation.kind in {"movable_replace", "movable_replace_by_id"}:
            container = self.doc.get_movable_list(_root_name("movable", operation.path))
            found = _find_item(container, data["item_id"])
            if found is None:
                message = f"movable item {data['item_id']!r} is unavailable"
                raise ValueError(message)
            index, item = found
            data.update(previous_authority=item[1], previous_value=item[2])
            container.set(index, _item_value(operation, item[0], data["value"]))
            self.doc.get_map(_authority_root("movable", operation.path)).insert(item[0], operation.identity)
        elif operation.kind in {"map_set", "map_delete", "map_restore"}:
            container = self.doc.get_map(_root_name("map", operation.path))
            key = data["key"]
            current = _decode_register(container.get_value().get(key))
            if "previous_present" not in data:
                data["previous_present"] = current is not None and current[1]
                data["previous_value"] = None if current is None else current[2]
            present = operation.kind != "map_delete" and bool(data.get("present", True))
            container.insert(key, _register_value(operation, data.get("value"), present=present))
        elif operation.kind.startswith("tree_"):
            self._apply_tree(operation, data)
        else:
            message = f"unknown Loro operation {operation.kind!r}"
            raise ValueError(message)
        return LoroOperation(operation.identity, operation.kind, operation.path, data)

    def _apply_tree(self, operation: LoroOperation, data: dict[str, Any]) -> None:
        tree = self.doc.get_tree(_root_name("tree", operation.path))
        authority = self.doc.get_map(_authority_root("tree", operation.path))
        node_id = data["node_id"]
        if operation.kind == "tree_create":
            parent = None if data["parent_id"] is None else _tree_lookup(tree, data["parent_id"])
            created = tree.create(parent) if data["index"] is None else tree.create_at(data["index"], parent)
            metadata = tree.get_meta(created)
            metadata.insert("$id", node_id)
            for key, value in data["metadata"].items():
                metadata.insert(key, _register_value(operation, value, present=True))
            authority.insert(node_id, operation.identity)
        elif operation.kind in {"tree_move", "tree_move_between"}:
            target = _tree_lookup(tree, node_id)
            if target is None:
                message = f"tree node {node_id!r} is unavailable"
                raise ValueError(message)
            current_parent = tree.parent(target)
            current_parent = None if current_parent is None else current_parent
            data.update(
                previous_parent_id=_tree_logical_id(tree, current_parent),
                previous_index=next(node.index for node in tree.get_nodes(with_deleted=False) if node.id == target),
            )
            parent = None if data.get("parent_id") is None else _tree_lookup(tree, data["parent_id"])
            if data.get("index") is None:
                tree.mov(target, parent)
            else:
                tree.mov_to(target, data["index"], parent)
            authority.insert(node_id, operation.identity)
        elif operation.kind == "tree_metadata":
            target = _tree_lookup(tree, node_id)
            if target is None:
                message = f"tree node {node_id!r} is unavailable"
                raise ValueError(message)
            metadata = tree.get_meta(target)
            current = _decode_register(metadata.get_value().get(data["key"]))
            data.update(
                previous_present=current is not None and current[1],
                previous_value=None if current is None else current[2],
            )
            metadata.insert(data["key"], _register_value(operation, data.get("value"), present=True))
        elif operation.kind == "tree_metadata_restore":
            target = _tree_lookup(tree, node_id)
            if target is None:
                message = f"tree node {node_id!r} is unavailable"
                raise ValueError(message)
            metadata = tree.get_meta(target)
            current = _decode_register(metadata.get_value().get(data["key"]))
            data.update(
                previous_present=current is not None and current[1],
                previous_value=None if current is None else current[2],
            )
            metadata.insert(
                data["key"],
                _register_value(operation, data.get("value"), present=data["present"]),
            )
        elif operation.kind == "tree_delete":
            target = _tree_lookup(tree, node_id)
            if target is None:
                message = f"tree node {node_id!r} is unavailable"
                raise ValueError(message)
            data["subtree"] = _tree_subtree(tree, target)
            tree.delete(target)
            authority.insert(node_id, operation.identity)
        elif operation.kind == "tree_restore":
            _restore_tree_subtree(tree, data["subtree"], operation)
            authority.insert(node_id, operation.identity)
        else:
            message = f"unknown Loro tree operation {operation.kind!r}"
            raise ValueError(message)

    def snapshot(self) -> ReplicatedSnapshot:
        return _snapshot(self.doc)

    def prepare(self, base: object) -> LoroDocumentPrepared:
        if base != self._before or self._engine.version() != self._before:
            message = "Loro document changed after this branch was staged"
            raise RuntimeError(message)
        self.doc.commit()
        update = self.doc.export(self._engine.module.ExportMode.Updates(self._base_vv))
        return LoroDocumentPrepared(
            self._before,
            update,
            self._before,
            self.doc.oplog_frontiers.encode(),
            tuple(self._operations),
        )

    def stage_inverse(self, inverse: object) -> None:
        if not isinstance(inverse, LoroDocumentInverse):
            message = "Loro inverse has the wrong backend type"
            raise TypeError(message)
        self.doc.apply_diff(inverse.safe_diff)
        for kind, path in inverse.safe_containers:
            self._operations.append(_operation(f"{kind}_diff", path))
        for operation in inverse.operations:
            self.apply(operation)


def _snapshot(document: Any) -> ReplicatedSnapshot:
    deep = document.get_deep_value()
    if not isinstance(deep, dict):
        message = "Loro document root is not an object"
        raise TypeError(message)
    counters: list[tuple[str, int]] = []
    sets: list[tuple[str, frozenset[str]]] = []
    texts: list[tuple[str, str]] = []
    lists: list[tuple[str, tuple[Any, ...]]] = []
    movable: list[tuple[str, tuple[ReplicatedItem, ...]]] = []
    maps: list[tuple[str, tuple[tuple[str, Any], ...]]] = []
    for root in deep:
        parsed = _parse_root(root)
        if parsed is None:
            continue
        kind, path = parsed
        if kind == "counter":
            values = document.get_map(root).get_value().values()
            counters.append((path, sum(value for value in values if isinstance(value, int))))
        elif kind == "set":
            sets.append((path, _set_snapshot(document.get_map(root).get_value())))
        elif kind == "text":
            texts.append((path, document.get_text(root).to_string()))
        elif kind == "list":
            value = document.get_list(root).get_deep_value()
            assert isinstance(value, list)
            decoded = [_decode_item(item) for item in value]
            if any(item is None for item in decoded):
                message = f"list {path!r} contains a malformed item"
                raise TypeError(message)
            lists.append((path, tuple(freeze_value(item[2]) for item in decoded if item is not None)))
        elif kind == "movable":
            value = document.get_movable_list(root).get_deep_value()
            assert isinstance(value, list)
            items: list[ReplicatedItem] = []
            for item in value:
                decoded = _decode_item(item)
                if decoded is None:
                    message = f"movable list {path!r} contains a malformed item"
                    raise TypeError(message)
                items.append(ReplicatedItem(uuid.UUID(decoded[0]), freeze_value(decoded[2])))
            movable.append((path, tuple(items)))
        elif kind == "map":
            value = document.get_map(root).get_value()
            entries: list[tuple[str, Any]] = []
            for key, encoded in value.items():
                register = _decode_register(encoded)
                if register is None:
                    message = f"map {path!r} contains a malformed register"
                    raise ValueError(message)
                if register[1]:
                    entries.append((key, freeze_value(register[2])))
            maps.append((path, tuple(sorted(entries))))
    trees: list[tuple[str, ReplicatedTreeSnapshot]] = []
    for root in deep:
        parsed = _parse_root(root)
        if parsed is None or parsed[0] != "tree":
            continue
        path = parsed[1]
        tree = document.get_tree(root)
        nodes: list[ReplicatedTreeNode] = []
        for item in tree.get_nodes(with_deleted=False):
            metadata = tree.get_meta(item.id).get_value()
            logical_id = metadata.get("$id")
            if not isinstance(logical_id, str):
                message = f"tree {path!r} contains a node without a logical identity"
                raise TypeError(message)
            entries: dict[str, Any] = {}
            for key, encoded in metadata.items():
                if key == "$id":
                    continue
                register = _decode_register(encoded)
                if register is None:
                    message = f"tree {path!r} contains malformed metadata"
                    raise TypeError(message)
                if register[1]:
                    entries[key] = freeze_value(register[2])
            children = tuple(
                uuid.UUID(child_id)
                for child in (tree.children(item.id) or [])
                if (child_id := _tree_logical_id(tree, child)) is not None
            )
            nodes.append(
                ReplicatedTreeNode(
                    uuid.UUID(logical_id),
                    None if item.parent is None else uuid.UUID(_tree_logical_id(tree, item.parent)),
                    children,
                    freeze_value(entries),
                )
            )
        roots = tuple(
            uuid.UUID(logical_id)
            for root_id in tree.roots
            if (logical_id := _tree_logical_id(tree, root_id)) is not None
        )
        trees.append((path, ReplicatedTreeSnapshot(roots, tuple(nodes))))
    return ReplicatedSnapshot(
        tuple(sorted(counters)),
        tuple(sorted(sets)),
        tuple(sorted(texts)),
        tuple(sorted(lists)),
        tuple(sorted(movable)),
        tuple(sorted(maps)),
        tuple(sorted(trees)),
    )


def _set_snapshot(entries: dict[str, object]) -> frozenset[str]:
    adds = {key[2:]: value for key, value in entries.items() if key.startswith("a:") and isinstance(value, str)}
    removals: dict[str, set[str]] = {}
    cancelled: set[str] = set()
    for key, value in entries.items():
        if key.startswith("r:") and isinstance(value, dict) and isinstance(value.get("tags"), list):
            for tag in value["tags"]:
                if isinstance(tag, str):
                    removals.setdefault(tag, set()).add(key[2:])
        elif key.startswith("u:") and isinstance(value, dict) and isinstance(value.get("removal"), str):
            cancelled.add(value["removal"])
    return frozenset(value for tag, value in adds.items() if not (removals.get(tag, set()) - cancelled))


@dataclass(frozen=True, slots=True)
class LoroBackend:
    """Create production Loro engines for one replica incarnation."""

    peer_id: int = 0
    backend_id = "loro-document-v1"

    def __post_init__(self) -> None:
        if self.peer_id == 0:
            object.__setattr__(self, "peer_id", secrets.randbits(64) or 1)

    def open_engine(self, replica_id: str, document_id: str) -> LoroEngine:
        return LoroEngine(replica_id, self.peer_id)


class LoroEngine:
    """A full-document Loro engine whose containers never escape as public values."""

    backend_id = LoroBackend.backend_id

    def __init__(self, replica_id: str, peer_id: int) -> None:
        try:
            import loro
        except ImportError as error:
            message = "install squid-replication[loro] to use LoroBackend"
            raise RuntimeError(message) from error
        self.module = loro
        self.replica_id = replica_id
        self.peer_id = peer_id
        self.doc = loro.LoroDoc()
        self.doc.peer_id = peer_id
        self.backend_version = distribution_version("loro")
        self._retentions: dict[uuid.UUID, bytes] = {}

    def operation(
        self,
        kind: str,
        path: str,
        value: str | int,
        tags: tuple[object, ...] = (),
        undoes: object | None = None,
    ) -> LoroOperation:
        data: dict[str, object] = {"value": value}
        if tags:
            data["tags"] = [str(tag) for tag in tags]
        if undoes is not None:
            data["undoes"] = str(undoes)
        return _operation(kind, path, **data)

    def make_operation(self, kind: str, path: str, **data: Any) -> LoroOperation:
        if not isinstance(path, str) or len(path.encode()) > _MAX_PATH_BYTES:
            message = "replicated paths must be strings no longer than 512 encoded bytes"
            raise ValueError(message)
        return _operation(kind, path, **data)

    def visible_tags(
        self,
        path: str,
        value: str,
        additions: tuple[LoroOperation, ...] = (),
    ) -> tuple[str, ...]:
        entries = dict(self.doc.get_map(_root_name("set", path)).get_value())
        for operation in additions:
            if operation.path != path:
                continue
            if operation.kind == "add":
                entries[f"a:{operation.identity}"] = operation.data["value"]
            elif operation.kind == "remove":
                entries[f"r:{operation.identity}"] = dict(operation.data)
            elif operation.kind == "restore":
                entries[f"u:{operation.identity}"] = {
                    "removal": operation.data["undoes"],
                    "tags": operation.data["tags"],
                    "value": operation.data["value"],
                }
        visible = _set_snapshot(entries)
        if value not in visible:
            return ()
        return tuple(key[2:] for key, item in entries.items() if key.startswith("a:") and item == value)

    def snapshot(self) -> ReplicatedSnapshot:
        return _snapshot(self.doc)

    def version(self) -> bytes:
        return self.doc.oplog_frontiers.encode()

    def branch(self) -> LoroDocumentBranch:
        return LoroDocumentBranch(self)

    def apply(self, prepared: LoroDocumentPrepared) -> LoroDocumentToken:
        try:
            self.doc.import_(prepared.update)
        except BaseException as error:
            if type(error) is BaseException:
                message = "Loro rejected an update after it passed isolated preparation"
                raise ReplicationBackendIntegrityError(message) from error
            raise
        return self._token(prepared)

    def prepare_remote(self, update: bytes) -> LoroDocumentPrepared:
        if len(update) > _MAX_UPDATE_BYTES:
            message = "Loro update exceeds the maximum encoded size"
            raise ValueError(message)
        before = self.version()
        branch = self.doc.fork()
        try:
            branch.import_(update)
        except BaseException as error:
            if type(error) is BaseException:
                message = "Loro update bytes are corrupt or incompatible"
                raise ReplicationCorruptUpdateError(message) from error
            raise
        _snapshot(branch)
        return LoroDocumentPrepared(None, update, before, branch.oplog_frontiers.encode(), ())

    def export_since(self, version: object | None = None) -> bytes:
        if version is None:
            mode = self.module.ExportMode.Snapshot()
        else:
            if not isinstance(version, bytes):
                message = "Loro version must be encoded frontiers"
                raise TypeError(message)
            frontiers = self.module.Frontiers.decode(version)
            vv = self.doc.frontiers_to_vv(frontiers)
            if vv is None:
                message = "Loro version is unavailable"
                raise ValueError(message)
            mode = self.module.ExportMode.Updates(vv)
        return self.doc.export(mode)

    def change_token(self, prepared: LoroDocumentPrepared) -> object | None:
        if not prepared.operations:
            return None
        return self._token(prepared)

    def _token(self, prepared: LoroDocumentPrepared) -> LoroDocumentToken:
        return LoroDocumentToken(
            prepared.before,
            prepared.after,
            prepared.operations,
            self.doc.shallow_since_frontiers.encode(),
            self.backend_version,
        )

    def encode_token(self, token: object) -> bytes:
        if not isinstance(token, LoroDocumentToken):
            message = "Loro token has the wrong backend type"
            raise TypeError(message)
        payload = {
            "after": base64.b64encode(token.after).decode(),
            "before": base64.b64encode(token.before).decode(),
            "operations": [
                {
                    "data": dict(operation.data),
                    "id": operation.identity,
                    "kind": operation.kind,
                    "path": operation.path,
                }
                for operation in token.operations
            ],
            "backend_version": token.backend_version,
            "schema": 1,
            "shallow_root": base64.b64encode(token.shallow_root).decode(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > _MAX_UPDATE_BYTES:
            message = "Loro history token exceeds the maximum encoded size"
            raise ValueError(message)
        return encoded

    def decode_token(self, token: bytes) -> object:
        if len(token) > _MAX_UPDATE_BYTES:
            message = "Loro history token exceeds the maximum encoded size"
            raise ValueError(message)
        try:
            payload: Any = json.loads(token)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            message = "Loro token is corrupt"
            raise ReplicationCorruptUpdateError(message) from error
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            message = "Loro token has an unsupported schema"
            raise ValueError(message)
        operations = payload.get("operations")
        if not isinstance(operations, list) or len(operations) > _MAX_OPERATIONS:
            message = "Loro token has invalid operations"
            raise TypeError(message)
        decoded: list[LoroOperation] = []
        for item in operations:
            if not isinstance(item, dict) or not isinstance(item.get("data"), dict):
                message = "Loro token has an invalid operation"
                raise TypeError(message)
            decoded.append(LoroOperation(item["id"], item["kind"], item["path"], item["data"]))
        return LoroDocumentToken(
            base64.b64decode(payload["before"], validate=True),
            base64.b64decode(payload["after"], validate=True),
            tuple(decoded),
            base64.b64decode(payload["shallow_root"], validate=True),
            payload["backend_version"],
        )

    def plan_inverse(self, token: object) -> object | ConflictDetail:
        if not isinstance(token, LoroDocumentToken):
            return ConflictDetail("replicated:loro:token", 0, 0)
        if token.backend_version != self.backend_version:
            return ConflictDetail("replicated:loro:backend-version", 0, 0)
        if self._token_is_expired(token):
            return ConflictDetail("replicated:loro:expired", _lineage_from_frontier(token.before), 0)
        inverse: list[LoroOperation] = []
        safe: set[tuple[str, str]] = set()
        for operation in reversed(token.operations):
            data = operation.data
            if operation.kind == "increment":
                amount = data.get("value")
                if isinstance(amount, bool) or not isinstance(amount, int):
                    return ConflictDetail(f"replicated:{operation.path}", 0, 0)
                inverse.append(_operation("increment", operation.path, value=-amount))
            elif operation.kind == "add":
                inverse.append(_operation("remove", operation.path, value=data["value"], tags=[operation.identity]))
            elif operation.kind == "remove":
                inverse.append(
                    _operation(
                        "restore",
                        operation.path,
                        value=data["value"],
                        tags=list(data["tags"]),
                        undoes=operation.identity,
                    )
                )
            elif operation.kind == "restore":
                inverse.append(_operation("remove", operation.path, value=data["value"], tags=list(data["tags"])))
            elif operation.kind.startswith("text_"):
                safe.add(("text", operation.path))
            elif operation.kind == "list_insert":
                conflict = self._authority_conflict("list", operation, [data["item_id"]], present=True)
                if conflict is not None:
                    return conflict
                inverse.append(_operation("list_delete_ids", operation.path, item_ids=[data["item_id"]]))
            elif operation.kind in {"list_delete", "list_delete_ids"}:
                item_ids = [item[0] for item in data["items"]]
                conflict = self._authority_conflict("list", operation, item_ids, present=False)
                if conflict is not None:
                    return conflict
                inverse.append(
                    _operation(
                        "list_restore",
                        operation.path,
                        items=data["items"],
                        before_id=data.get("before_id"),
                        after_id=data.get("after_id"),
                    )
                )
            elif operation.kind == "list_restore":
                conflict = self._authority_conflict("list", operation, data["item_ids"], present=True)
                if conflict is not None:
                    return conflict
                inverse.append(_operation("list_delete_ids", operation.path, item_ids=data["item_ids"]))
            elif operation.kind in {"list_replace", "list_replace_by_id"}:
                conflict = self._authority_conflict("list", operation, [data["item_id"]], present=True)
                if conflict is not None:
                    return conflict
                inverse.append(
                    _operation(
                        "list_replace_by_id",
                        operation.path,
                        item_id=data["item_id"],
                        value=data["previous_value"],
                    )
                )
            elif operation.kind == "movable_insert":
                conflict = self._authority_conflict("movable", operation, [data["item_id"]], present=True)
                if conflict is not None:
                    return conflict
                inverse.append(_operation("movable_delete_ids", operation.path, item_ids=[data["item_id"]]))
            elif operation.kind in {"movable_delete", "movable_delete_ids"}:
                conflict = self._authority_conflict("movable", operation, data["item_ids"], present=False)
                if conflict is not None:
                    return conflict
                inverse.append(
                    _operation(
                        "movable_restore",
                        operation.path,
                        items=data["items"],
                        before_id=data.get("before_id"),
                        after_id=data.get("after_id"),
                    )
                )
            elif operation.kind == "movable_restore":
                conflict = self._authority_conflict("movable", operation, data["item_ids"], present=True)
                if conflict is not None:
                    return conflict
                inverse.append(_operation("movable_delete_ids", operation.path, item_ids=data["item_ids"]))
            elif operation.kind in {"movable_move", "movable_move_between"}:
                conflict = self._authority_conflict("movable", operation, [data["item_id"]], present=True)
                if conflict is not None:
                    return conflict
                inverse.append(
                    _operation(
                        "movable_move_between",
                        operation.path,
                        item_id=data["item_id"],
                        before_id=data["previous_before_id"],
                        after_id=data["previous_after_id"],
                    )
                )
            elif operation.kind in {"movable_replace", "movable_replace_by_id"}:
                conflict = self._authority_conflict("movable", operation, [data["item_id"]], present=True)
                if conflict is not None:
                    return conflict
                inverse.append(
                    _operation(
                        "movable_replace_by_id",
                        operation.path,
                        item_id=data["item_id"],
                        value=data["previous_value"],
                    )
                )
            elif operation.kind in {"map_set", "map_delete", "map_restore"}:
                container = self.doc.get_map(_root_name("map", operation.path))
                current = _decode_register(container.get_value().get(data["key"]))
                actual = None if current is None else current[0]
                if actual != operation.identity:
                    return ConflictDetail(
                        f"replicated:{operation.path}:{data['key']}",
                        _lineage(operation.identity),
                        _lineage(actual),
                    )
                inverse.append(
                    _operation(
                        "map_restore",
                        operation.path,
                        key=data["key"],
                        present=data["previous_present"],
                        value=data["previous_value"],
                    )
                )
            elif operation.kind == "tree_create":
                conflict = self._tree_authority_conflict(operation, present=True)
                if conflict is not None:
                    return conflict
                inverse.append(_operation("tree_delete", operation.path, node_id=data["node_id"]))
            elif operation.kind in {"tree_move", "tree_move_between"}:
                conflict = self._tree_authority_conflict(operation, present=True)
                if conflict is not None:
                    return conflict
                inverse.append(
                    _operation(
                        "tree_move_between",
                        operation.path,
                        node_id=data["node_id"],
                        parent_id=data["previous_parent_id"],
                        index=data["previous_index"],
                    )
                )
            elif operation.kind in {"tree_metadata", "tree_metadata_restore"}:
                tree = self.doc.get_tree(_root_name("tree", operation.path))
                target = _tree_lookup(tree, data["node_id"])
                current = (
                    None if target is None else _decode_register(tree.get_meta(target).get_value().get(data["key"]))
                )
                actual = None if current is None else current[0]
                if actual != operation.identity:
                    return ConflictDetail(
                        f"replicated:{operation.path}:{data['node_id']}:{data['key']}",
                        _lineage(operation.identity),
                        _lineage(actual),
                    )
                inverse.append(
                    _operation(
                        "tree_metadata_restore",
                        operation.path,
                        node_id=data["node_id"],
                        key=data["key"],
                        present=data["previous_present"],
                        value=data["previous_value"],
                    )
                )
            elif operation.kind == "tree_delete":
                conflict = self._tree_authority_conflict(operation, present=False)
                if conflict is not None:
                    return conflict
                inverse.append(
                    _operation(
                        "tree_restore",
                        operation.path,
                        node_id=data["node_id"],
                        subtree=data["subtree"],
                    )
                )
            elif operation.kind == "tree_restore":
                conflict = self._tree_authority_conflict(operation, present=True)
                if conflict is not None:
                    return conflict
                inverse.append(_operation("tree_delete", operation.path, node_id=data["node_id"]))
            else:
                return ConflictDetail(f"replicated:{operation.path}", 0, 0)
        try:
            reverse = self.doc.diff(
                self.module.Frontiers.decode(token.after),
                self.module.Frontiers.decode(token.before),
            )
        except BaseException as error:
            if type(error) is BaseException:
                return ConflictDetail("replicated:loro:expired", _lineage_from_frontier(token.before), 0)
            raise
        filtered = self.module.DiffBatch()
        roots = {_root_name(kind, path) for kind, path in safe}
        for cid, diff in reverse.get_diff():
            if isinstance(cid, self.module.ContainerID.Root) and cid.name in roots:
                filtered.push(cid, diff)
        return LoroDocumentInverse(tuple(inverse), filtered, tuple(sorted(safe)))

    def _authority_conflict(
        self,
        kind: str,
        operation: LoroOperation,
        item_ids: list[str],
        *,
        present: bool,
    ) -> ConflictDetail | None:
        authority = self.doc.get_map(_authority_root(kind, operation.path)).get_value()
        container = (
            self.doc.get_list(_root_name(kind, operation.path))
            if kind == "list"
            else self.doc.get_movable_list(_root_name(kind, operation.path))
        )
        for item_id in item_ids:
            actual = authority.get(item_id)
            found = _find_item(container, item_id)
            if actual != operation.identity or (found is not None) != present:
                return ConflictDetail(
                    f"replicated:{operation.path}:{item_id}",
                    _lineage(operation.identity),
                    _lineage(actual if isinstance(actual, str) else None),
                )
        return None

    def _tree_authority_conflict(
        self,
        operation: LoroOperation,
        *,
        present: bool,
    ) -> ConflictDetail | None:
        node_id = operation.data["node_id"]
        actual = self.doc.get_map(_authority_root("tree", operation.path)).get_value().get(node_id)
        exists = _tree_lookup(self.doc.get_tree(_root_name("tree", operation.path)), node_id) is not None
        if actual == operation.identity and exists == present:
            return None
        return ConflictDetail(
            f"replicated:{operation.path}:{node_id}",
            _lineage(operation.identity),
            _lineage(actual if isinstance(actual, str) else None),
        )

    def encode_prepared(self, prepared: LoroDocumentPrepared) -> bytes:
        return prepared.update

    def retain_token(self, token: object) -> object:
        if not isinstance(token, LoroDocumentToken) or self._token_is_expired(token):
            message = "cannot retain an expired or foreign Loro token"
            raise ValueError(message)
        retention = uuid.uuid7()
        self._retentions[retention] = token.before
        return retention

    def release_token(self, retention: object) -> None:
        if isinstance(retention, uuid.UUID):
            self._retentions.pop(retention, None)

    def compact(self) -> None:
        target = self.doc.oplog_vv
        for encoded in self._retentions.values():
            frontiers = self.module.Frontiers.decode(encoded)
            retained = self.doc.frontiers_to_vv(frontiers)
            if retained is None:
                message = "a retained Loro token no longer has available history"
                raise ReplicationBackendIntegrityError(message)
            target = target.intersection(retained)
        frontiers = target.get_frontiers()
        try:
            snapshot = self.doc.export(self.module.ExportMode.ShallowSnapshot(frontiers))
            compacted = self.module.LoroDoc()
            compacted.peer_id = self.peer_id
            compacted.import_(snapshot)
        except BaseException as error:
            if type(error) is BaseException:
                message = "Loro failed to compact at the retained history boundary"
                raise ReplicationBackendIntegrityError(message) from error
            raise
        self.doc = compacted

    def _token_is_expired(self, token: LoroDocumentToken) -> bool:
        try:
            token_before = self.module.Frontiers.decode(token.before)
            before_vv = self.doc.frontiers_to_vv(token_before)
        except BaseException as error:
            if type(error) is BaseException:
                return True
            raise
        return before_vv is None or not before_vv.includes_vv(self.doc.shallow_since_vv)
