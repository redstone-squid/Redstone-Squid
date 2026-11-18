"""Resolve pure contextual content against one captured, bounded layout attempt."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from squid_ui import scene
from squid_ui.authoring.document import Document, DocumentLike, as_document
from squid_ui.authoring.semantic import Contextual, Group, LayoutNode, PreparedSlot
from squid_ui.authoring.traversal import _namespace, map_layout_children
from squid_ui.boundary import Boundary
from squid_ui.content.text import NEUTRAL, Localization
from squid_ui.errors import LayoutError
from squid_ui.layout import (
    DocumentScope,
    ElementRef,
    FactUnavailableError,
    LocationQuery,
    Locations,
    RegionRef,
)
from squid_ui.planning.composition import OccurrenceId, WorkBudget
from squid_ui.planning.context_evaluation import evaluate_contextual
from squid_ui.planning.identity import stable_fingerprint
from squid_ui.planning.joint_search import JointInfeasibleError
from squid_ui.planning.layout_snapshot import SemanticCapture, _ScopeReader
from squid_ui.planning.result import CertificateScope, PlanEvent, PlanResult
from squid_ui.planning.semantic_index import semantic_graph
from squid_ui.presentation_state import RetainedCompositionUpdate
from squid_ui.selectors import Match


class ContextResolutionError(LayoutError):
    """A contextual attempt failed without publishing any candidate output."""

    def __init__(self, reason: str, keys: tuple[str, ...], rounds: int) -> None:
        self.reason = reason
        self.keys = keys
        self.rounds = rounds
        super().__init__(f"contextual layout {reason} after {rounds} rounds: {', '.join(keys)}")


@dataclass(frozen=True, slots=True)
class _AuthoredLocations:
    authored: SemanticCapture
    reader: LocationQuery
    owner: str | None = None
    namespace: str | None = None

    def __call__[T](self, target: ElementRef[T] | Match[T], within: RegionRef | None) -> Locations:
        if isinstance(target, Match) and target._token is self.authored.index._token:
            reference = self.authored.references[self.authored.index.index(target)]
            return self.reader(ElementRef(type(target.node), reference.keys), within)
        if isinstance(target, ElementRef) and self.owner is not None and within is None:
            candidates = tuple(
                match
                for match in self.authored.index.matches
                if isinstance(match.node, target.kind)
                and match.owner == self.owner
                and (
                    self.authored.references[match._index].keys[-len(target.keys) :] == target.keys
                    or (len(target.keys) == 1 and match.key == target.keys[0])
                )
            )
            if len(candidates) > 1:
                message = f"ambiguous element reference in placement {self.owner}: {target.keys!r}"
                raise LayoutError(message)
            if candidates:
                return self.reader(ElementRef(target.kind, self.authored.references[candidates[0]._index].keys), within)
            if self.namespace is not None:
                scoped = (*target.keys[:-1], f"{self.namespace}.{target.keys[-1]}")
                return self.reader(ElementRef(target.kind, scoped), within)
        return self.reader(target, within)


class ContextualResolution[R, BodyT: scene.Body]:
    """Keep working content and query evidence private to one planning attempt."""

    def __init__(
        self, document: Document[R], *, work_limit: int, max_rounds: int = 16, localization: Localization = NEUTRAL
    ) -> None:
        self.work = WorkBudget(work_limit)
        self.max_rounds = max_rounds
        self.blocks: dict[str, Contextual[R, Any]] = {}
        self.working: dict[str, tuple[LayoutNode[R, Any], ...]] = {}
        self.assets = document.assets
        self.generated_assets: dict[str, tuple[Any, ...]] = {}
        self.partial = False
        self.view_dependent = False
        self.excluded: list[tuple[tuple[OccurrenceId, str], ...]] = []
        self.last_choices: tuple[tuple[OccurrenceId, str], ...] = ()
        self.rejected: list[ContextResolutionError] = []

        def declare(node: LayoutNode[R, Any], path: str) -> tuple[LayoutNode[R, Any], ...]:
            self.work.consume()
            if isinstance(node, Contextual):
                node = replace(node, _identity=path)
                node = map_layout_children(node, path, declare)
                assert isinstance(node, Contextual)
                self.blocks[path] = node
                self.working[path] = tuple(_namespace(list(node.children), node.key or path))
                return (node,)
            return (map_layout_children(node, path, declare),)

        self.document = replace(
            document,
            children=tuple(child for i, node in enumerate(document.children) for child in declare(node, f"root.{i}")),
        )

        def authored(node: LayoutNode[R, Any], path: str) -> tuple[LayoutNode[R, Any], ...]:
            if isinstance(node, Contextual):
                node = replace(node, children=(*node.children, *node._prepared), _prepared=())
            return (map_layout_children(node, path, authored),)

        authored_nodes = tuple(
            child for i, node in enumerate(self.document.children) for child in authored(node, str(i))
        )
        graph = semantic_graph(Group(authored_nodes, key=document.key or "@document"), consume=self.work.consume)
        capture = SemanticCapture(graph, self.work.consume, localization)
        self.authored_capture = capture
        self.authored = DocumentScope(_ScopeReader(capture, frozenset(capture.references)), len(capture.references))

    def _document(self, *, logical: bool = False) -> Document[R]:
        def expand(node: LayoutNode[R, Any], path: str) -> tuple[LayoutNode[R, Any], ...]:
            self.work.consume()
            if isinstance(node, Contextual):
                return (
                    replace(
                        node,
                        children=tuple(
                            child
                            for i, value in enumerate(self.working[node._identity])
                            for child in expand(value, f"{path}.{i}")
                        ),
                        components=self.blocks[node._identity].components if logical else (),
                        _prepared=self.blocks[node._identity]._prepared if logical else (),
                    ),
                )
            return (map_layout_children(node, path, expand),)

        return replace(
            self.document,
            children=tuple(
                child for i, node in enumerate(self.document.children) for child in expand(node, f"root.{i}")
            ),
            assets=(*self.assets, *(asset for values in self.generated_assets.values() for asset in values)),
        )

    def _output(self, block: Contextual[R, Any], value: DocumentLike[R, Any]) -> tuple[LayoutNode[R, Any], ...]:
        document = as_document(value)
        if document.key is not None:
            message = f"context {block.key}: generated content cannot replace the document identity"
            raise LayoutError(message)
        self.generated_assets[block._identity] = document.assets
        slots = {
            (id(slot.component), slot.key): prepared
            for slot, prepared in zip(block.components, block._prepared, strict=True)
        }

        def substitute(node: LayoutNode[R, Any], path: str) -> tuple[LayoutNode[R, Any], ...]:
            self.work.consume()
            if isinstance(node, Boundary):
                prepared = slots.get((id(node.component), node.key))
                if prepared is None:
                    message = f"context {block.key}: component {node.key!r} was not declared in components"
                    raise LayoutError(message)
                placement = node.key if block._scope == "$" else f"{block._scope}.{node.key}"
                return (PreparedSlot(prepared.children, key=node.key, placement=placement),)
            if isinstance(node, Contextual):
                message = f"context {block.key}: declare nested contextual content before planning"
                raise LayoutError(message)
            return (map_layout_children(node, path, substitute),)

        nodes = tuple(child for i, node in enumerate(document.children) for child in substitute(node, str(i)))
        return tuple(_namespace(list(nodes), block.key or block._identity))

    def solve(
        self,
        plan: Callable[[Document[R], Mapping[OccurrenceId, int], WorkBudget, bool], PlanResult[BodyT]],
        *,
        pages: Mapping[OccurrenceId, int],
        replan: Callable[[Mapping[OccurrenceId, int]], PlanResult[BodyT]],
    ) -> PlanResult[BodyT]:
        initial = dict(self.working)
        while True:
            try:
                return self._solve(plan, pages=pages, replan=replan)
            except ContextResolutionError as error:
                if not self.partial and error.reason in ("oscillates", "did not converge"):
                    self.partial = True
                    continue
                if not self.last_choices or self.last_choices in self.excluded:
                    raise
                self.rejected.append(error)
                self.excluded.append(self.last_choices)
                self.working = dict(initial)
                self.generated_assets.clear()
                self.partial = False
                self.view_dependent = False
            except JointInfeasibleError as error:
                if not self.rejected:
                    raise
                reason = "has no convergent declared alternative"
                raise ContextResolutionError(reason, self.rejected[-1].keys, self.rejected[-1].rounds) from error

    def _solve(
        self,
        plan: Callable[[Document[R], Mapping[OccurrenceId, int], WorkBudget, bool], PlanResult[BodyT]],
        *,
        pages: Mapping[OccurrenceId, int],
        replan: Callable[[Mapping[OccurrenceId, int]], PlanResult[BodyT]],
    ) -> PlanResult[BodyT]:
        seen: set[str] = set()
        previous: str | None = None
        observations: list[tuple[object, object]] = []
        for round_number in range(1, self.max_rounds + 1):
            self.work.consume()
            query_document = self._document(logical=True)
            try:
                with self.work.allowance(max(1, self.work.limit // 4)):
                    result = plan(self._document(), pages, self.work, self.partial)
            except JointInfeasibleError:
                if self.partial:
                    raise
                # A closed-family failure does not disprove the demanded view.
                self.partial = True
                with self.work.allowance(max(1, self.work.limit // 4)):
                    result = plan(self._document(), pages, self.work, self.partial)
            self.last_choices = result._choices
            layout = result.layout
            if layout is None:
                message = "contextual content needs an automatic semantic composition target"
                raise LayoutError(message)
            pending: list[str] = []
            unresolved_offscreen = False
            changed = False
            observations = []
            for identity, containing, required in layout._invocations:
                self.work.consume()
                block = self.blocks[identity]
                ctx = layout._evaluating(
                    self.work.consume,
                    owner=block._scope,
                    containing=containing,
                    partial=self.partial,
                    authored=self.authored,
                )
                ctx._locate = _AuthoredLocations(self.authored_capture, ctx._locate, block._scope, block.key)
                try:
                    output = evaluate_contextual(block, ctx)
                    old_assets = self.generated_assets.get(identity, ())
                    nodes = self._output(block, output)
                    if stable_fingerprint(old_assets, consume=self.work.consume) != stable_fingerprint(
                        self.generated_assets.get(identity, ()), consume=self.work.consume
                    ):
                        changed = True
                except FactUnavailableError:
                    if required:
                        pending.append(block.key or identity)
                    else:
                        unresolved_offscreen = True
                    continue
                self.view_dependent |= ctx.view_dependent
                observations.extend(ctx.observations)
                before = stable_fingerprint(self.working[identity], consume=self.work.consume)
                after = stable_fingerprint(nodes, consume=self.work.consume)
                self.working[identity] = nodes
                if after != before:
                    changed = True
            signature = stable_fingerprint(
                (self.working, self.partial, observations, result.report.regions),
                consume=self.work.consume,
            )
            if not changed:
                if unresolved_offscreen and not self.partial:
                    self.partial = True
                    seen.clear()
                    continue
                if pending:
                    reason = "has unresolved required facts"
                    raise ContextResolutionError(reason, tuple(pending), round_number)
            if not changed and signature == previous:
                # The input handlers were generated from the same observed facts.
                # Re-running search here could choose different cuts under the
                # remaining budget and invalidate those captured handler values.
                assert result.layout is not None
                final_layout = result.layout._evaluating(None, partial=self.partial, authored=self.authored)
                final_layout._locate = _AuthoredLocations(self.authored_capture, final_layout._locate)
                return replace(
                    result,
                    layout=final_layout,
                    query_document=query_document,
                    report=replace(
                        result.report,
                        events=(
                            *result.report.events,
                            *(
                                PlanEvent("context.rejected", ", ".join(failure.keys), failure.reason)
                                for failure in self.rejected
                            ),
                        ),
                    ),
                    certificate=CertificateScope.VIEW
                    if self.partial or self.view_dependent
                    else CertificateScope.FAMILY,
                    allocation=None,
                    page_selector=replan,
                    session_updates=tuple(
                        update for update in result.session_updates if not isinstance(update, RetainedCompositionUpdate)
                    ),
                    metrics=replace(result.metrics, work_used=self.work.spent, context_rounds=round_number),
                )
            if signature in seen and signature != previous:
                reason = "oscillates"
                raise ContextResolutionError(
                    reason, tuple(block.key or identity for identity, block in self.blocks.items()), round_number
                )
            seen.add(signature)
            previous = signature
        reason = "did not converge"
        raise ContextResolutionError(
            reason, tuple(block.key or identity for identity, block in self.blocks.items()), self.max_rounds
        )
