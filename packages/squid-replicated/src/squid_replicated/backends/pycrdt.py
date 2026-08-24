"""Experimental pycrdt/Yrs text engine behind the replicated SPI."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PycrdtTextOperation:
    kind: str
    index: int
    value: str | int


@dataclass(frozen=True, slots=True)
class PycrdtChangeToken:
    deletions: bytes
    insertions: bytes

    def encode(self) -> bytes:
        size = len(self.deletions).to_bytes(4, "big")
        return size + self.deletions + self.insertions

    @classmethod
    def decode(cls, data: bytes) -> PycrdtChangeToken:
        size = int.from_bytes(data[:4], "big")
        return cls(data[4 : 4 + size], data[4 + size :])


@dataclass(frozen=True, slots=True)
class PycrdtPrepared:
    base: bytes | None
    update: bytes
    token: PycrdtChangeToken | None


class PycrdtTextBranch:
    def __init__(self, engine: PycrdtTextEngine) -> None:
        module = engine.module
        self._engine = engine
        self.text = module.Text()
        self.doc = module.Doc({"text": self.text}, skip_gc=True)
        self.doc.apply_update(engine.doc.get_update())
        self._base = engine.doc.get_state()
        self._undo = module.UndoManager(scopes=[self.text], capture_timeout_millis=60_000, timestamp=lambda: 0)

    def apply(self, operation: PycrdtTextOperation) -> None:
        if operation.kind == "insert":
            assert isinstance(operation.value, str)
            self.text.insert(operation.index, operation.value)
        elif operation.kind == "delete":
            assert isinstance(operation.value, int)
            del self.text[operation.index : operation.index + operation.value]
        else:
            message = f"unknown pycrdt text operation {operation.kind!r}"
            raise ValueError(message)

    def snapshot(self) -> str:
        return self.text.to_py()

    def prepare(self, base: object) -> PycrdtPrepared:
        stack = self._undo.undo_stack
        token = None
        if stack:
            item = stack[-1]
            token = PycrdtChangeToken(item.deletions.encode(), item.insertions.encode())
        return PycrdtPrepared(self._base, self.doc.get_update(self._base), token)


class PycrdtTextEngine:
    """A pycrdt text adapter used by the backend gate; public Squid values remain strings."""

    backend_id = "pycrdt-text-v1"

    def __init__(self) -> None:
        try:
            import pycrdt
        except ImportError as error:
            message = "install squid-replicated[pycrdt] to use PycrdtTextEngine"
            raise RuntimeError(message) from error
        self.module = pycrdt
        self.text = pycrdt.Text()
        self.doc = pycrdt.Doc({"text": self.text}, skip_gc=True)

    def snapshot(self) -> str:
        return self.text.to_py()

    def version(self) -> bytes:
        return self.doc.get_state()

    def branch(self) -> PycrdtTextBranch:
        return PycrdtTextBranch(self)

    def apply(self, prepared: PycrdtPrepared) -> PycrdtChangeToken | None:
        self.doc.apply_update(prepared.update)
        return prepared.token

    def prepare_remote(self, update: bytes) -> PycrdtPrepared:
        return PycrdtPrepared(None, update, None)

    def export_since(self, version: object | None = None) -> bytes:
        return self.doc.get_update(version if isinstance(version, bytes) else None)

    def plan_inverse(self, token: PycrdtChangeToken) -> PycrdtPrepared:
        branch = self.branch()
        module = self.module
        item = module.StackItem(
            branch.doc,
            module.IdSet.decode(token.deletions),
            module.IdSet.decode(token.insertions),
        )
        undo = module.UndoManager(scopes=[branch.text], capture_timeout_millis=0, undo_stack=[item])
        if not undo.undo():
            message = "pycrdt could not apply the retained action token"
            raise RuntimeError(message)
        return PycrdtPrepared(self.version(), branch.doc.get_update(self.version()), None)
