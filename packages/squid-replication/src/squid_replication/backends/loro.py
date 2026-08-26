"""Experimental Loro text engine behind the replicated SPI."""

from dataclasses import dataclass


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
