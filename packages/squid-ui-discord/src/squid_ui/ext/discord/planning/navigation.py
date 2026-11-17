"""Build region navigation and stable identities for composed controls."""

from dataclasses import replace

from squid_i18n import tree
from squid_ui.content.emoji import normalize_emoji
from squid_ui.content.text import TextLike, resolve_text
from squid_ui.ext.discord.planning.realizations import PremiumRealization
from squid_ui.ext.discord.primitives import nodes as primitive
from squid_ui.interactions import ActionBinding
from squid_ui.interactions.forms import FormBinding
from squid_ui.planning import controls
from squid_ui.planning.composition import (
    CandidateComposition,
    OccurrenceId,
    PlanningContext,
    RealizationOffer,
    SemanticOccurrence,
)
from squid_ui.planning.realizations import (
    ControlRealization,
    EntityRealization,
    Realization,
    RowRealization,
    SelectRealization,
)
from squid_ui.planning.semantic_index import UnsupportedSemanticError


def navigation_candidate[RenderTargetT, ContextT](
    node: primitive.Node[ContextT],
    occurrence: SemanticOccurrence[RenderTargetT, ContextT],
    context: PlanningContext,
) -> CandidateComposition[RenderTargetT, ContextT, Realization]:
    """Expose navigation structure and bindings before its resource certification."""
    context.work.consume()

    def label(value: TextLike | None) -> str | None:
        return (
            None if value is None else "".join(tree.plain(n) for n in resolve_text(value, context.localization).nodes)
        )

    binding = None
    form: FormBinding[ContextT] | None = None
    children: tuple[CandidateComposition[RenderTargetT, ContextT, Realization], ...] = ()
    payload: Realization
    match node:
        case primitive.Row(items=items):
            payload = RowRealization()
            children = tuple(
                navigation_candidate(
                    child, replace(occurrence, identity=OccurrenceId((*occurrence.identity.path, str(index)))), context
                )
                for index, child in enumerate(items)
            )
        case primitive.Button():
            payload = ControlRealization(
                label(node.label) or "",
                action=node.key,
                style=node.style,
                disabled=node.disabled,
                mode=node.mode,
                emoji=normalize_emoji(node.emoji),
            )
            binding = ActionBinding(
                node.key,
                node.on_click,
                node.mode,
                guard=node.guard,
                busy=node.busy,
                label=node.label or "",
                record=node.record,
            )
            if isinstance(node, primitive.FormButton):
                form = node.form
        case primitive.RoutedButton():
            payload = ControlRealization(
                label(node.label) or "",
                route=node.route_id,
                style=node.style,
                disabled=node.disabled,
                emoji=normalize_emoji(node.emoji),
            )
        case primitive.PremiumButton():
            payload = PremiumRealization(node.sku_id)
        case primitive.LinkButton():
            payload = ControlRealization(
                label(node.label) or "", url=node.url, disabled=node.disabled, emoji=normalize_emoji(node.emoji)
            )
        case primitive.SelectMenu() | primitive.RoutedSelect():
            options = tuple(
                controls.Option(
                    label(option.label) or "",
                    option.value,
                    label(option.description),
                    option.default,
                    normalize_emoji(option.emoji),
                )
                for option in node.options
            )
            if isinstance(node, primitive.SelectMenu):
                payload = SelectRealization(
                    controls.Select(
                        options,
                        node.key,
                        label(node.placeholder),
                        node.min_values,
                        node.max_values,
                        node.disabled,
                        node.mode,
                    )
                )
                binding = ActionBinding(node.key, node.on_select, node.mode, routes=node.routes)
            else:
                payload = SelectRealization(
                    controls.RoutedSelect(
                        options, node.route_id, label(node.placeholder), node.min_values, node.max_values, node.disabled
                    )
                )
        case primitive.EntitySelect():
            payload = EntityRealization(
                controls.EntitySelect(
                    node.entity_type,
                    node.key,
                    label(node.placeholder),
                    node.default_values,
                    node.conversation_types,
                    node.min_values,
                    node.max_values,
                    node.disabled,
                    node.mode,
                )
            )
            binding = ActionBinding(node.key, node.on_select, node.mode)
        case _:
            msg = f"navigation {type(node).__name__}"
            raise UnsupportedSemanticError(msg)
    return CandidateComposition(
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "navigation",
            payload,
            tuple(child.offer.occurrence for child in children),
            binding=binding,
            form=form,
        ),
        children,
    )
