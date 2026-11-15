"""Plan HTML scenes from compiled semantic elements, retaining only callback-free cache entries."""

from typing import Any, cast

from squid_ui import scene
from squid_ui.document import DocumentLike, as_document
from squid_ui.errors import LayoutDegradedError, LayoutInvariantError
from squid_ui.planning._html_elements import ElementCompiler, action_keys
from squid_ui.planning.cache import CachedPlan, PlanCache, PlanMemo
from squid_ui.planning.identity import stable_fingerprint
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.result import PlanMetrics, PlanReport, PlanResult, PlanReuse, PlanSeverity
from squid_ui.planning.target import Target
from squid_ui.presentation import ActivePagers, CursorUpdate
from squid_ui.target_types import HtmlTarget

type HtmlTargetT = Target[Any, scene.HtmlBody, HtmlTarget, Any]


class HtmlPlanner:
    """Compile portable semantic documents into HTML scene data.

    There is no global character budget. Authored budgets truncate text, and
    authored pagination splits between children without splitting an oversized
    child. Strict mode rejects recorded degradation. Structural cache hits still
    compile current bindings and resources before reusing the cached scene.
    """

    def plan(
        self,
        rendered: DocumentLike[HtmlTarget],
        request: PlanRequest[scene.HtmlBody, HtmlTarget, Any],
        *,
        cache: PlanCache[scene.HtmlBody] | None = None,
        memo: PlanMemo[scene.HtmlBody] | None = None,
    ) -> PlanResult[scene.HtmlBody]:
        # HTML has no global resource axes to reserve; authored region budgets
        # are handled separately during compilation.
        if request.reservation.values:
            message = "HTML has no reservable global resource axes"
            raise LayoutInvariantError(message)
        target = cast(HtmlTargetT, request.target)
        localization = request.localization
        palette = request.palette
        positions = request.positions
        strict = request.strict
        presentation = request.presentation
        key = stable_fingerprint(
            (
                rendered,
                target.fingerprint,
                localization.locale,
                palette,
                presentation,
                positions,
                strict,
            )
        )
        if memo is not None and (exact := memo.replay(rendered, key, presentation)) is not None:
            return exact
        document = as_document(rendered)
        compiler = ElementCompiler(target, request.chrome, localization, palette, presentation, positions, strict)
        for asset in document.assets:
            compiler._asset(asset)
        children = compiler.compile_children(document.children, "$")
        if strict and any(event.severity is PlanSeverity.DEGRADATION for event in compiler.events):
            raise LayoutDegradedError("; ".join(event.message for event in compiler.events))
        compiler.updates.append(
            ActivePagers(frozenset(update.key for update in compiler.updates if isinstance(update, CursorUpdate)))
        )
        planned = scene.Scene(
            protocol=scene.Codec.protocol,
            target=target.id,
            target_version=target.version,
            body=scene.HtmlBody(children, locale=localization.locale),
            assets=tuple(scene.Asset(asset.key, asset.name, asset.media_type) for asset in compiler.assets.values()),
            pagers=tuple(compiler.pagers),
        )
        logical = stable_fingerprint((document, target.fingerprint, localization.locale, palette, positions))
        report = PlanReport(
            tuple(compiler.events),
            logical_fingerprint=logical,
            scene_fingerprint=scene.Codec.fingerprint(planned),
        )
        cached = cache.get(key) if cache is not None else None
        if cached is not None:
            planned = cached.scene
            report = cached.report
        actions, forms = action_keys(planned.body.children)
        result = PlanResult(
            scene=planned,
            bindings={key: binding for key, binding in compiler.bindings.items() if key in actions},
            form_bindings={key: binding for key, binding in compiler.form_bindings.items() if key in forms},
            report=report,
            resources={f"asset:{asset.key}": asset for asset in compiler.assets.values()},
            metrics=PlanMetrics(
                states_explored=compiler.states_explored,
                cache_hit=cached is not None,
                reuse=PlanReuse.EXACT if cached else PlanReuse.MISS,
            ),
            session_updates=tuple(compiler.updates),
        )
        if cache is not None and cached is None:
            cache.put(
                key,
                CachedPlan(planned, report, tuple(compiler.updates), states_explored=compiler.states_explored),
            )
        if memo is not None:
            memo.store(rendered, key, presentation, result)
        return result


HTML_PLANNER = HtmlPlanner()


__all__ = ["HTML_PLANNER", "HtmlPlanner"]
