"""Pure title grammar shared by catalogue consumers."""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from squid.core.errors import ValidationError
from squid.core.i18n import tr


class TitleSection(StrEnum):
    """A semantic section of a catalogue title."""

    RECORD_CLASS = "record_class"
    WIRING = "wiring"
    ANIMATED = "animated"
    SIZE = "size"
    TYPE = "type"
    ORIENTATION = "orientation"
    LENGTH = "length"
    FIXED_NOUN = "fixed_noun"
    COMPONENT = "component"
    MISCELLANEOUS = "miscellaneous"


class TitleDiagnosticCode(StrEnum):
    """A stable reason that title input needs moderator attention."""

    UNKNOWN_TERM = "unknown_term"
    DUPLICATE_TERM = "duplicate_term"
    DUPLICATE_ALIAS = "duplicate_alias"
    AMBIGUOUS_TRAPDOOR = "ambiguous_trapdoor"


class TrapdoorPlacement(StrEnum):
    """The explicit placement which replaces a door orientation."""

    FLOOR = "floor"
    CEILING = "ceiling"
    WALL = "wall"


@dataclass(frozen=True, slots=True)
class TitleToken:
    """One rendered term with enough provenance for presentation."""

    value: str
    section: TitleSection
    recognized: bool = True
    source_value: str = ""

    def __post_init__(self) -> None:
        if not self.value.strip():
            msg = tr(t"A title token cannot be blank.")
            raise ValidationError(msg)
        if not self.source_value:
            object.__setattr__(self, "source_value", self.value)


@dataclass(frozen=True, slots=True)
class TitleDiagnostic:
    """Structured moderator guidance emitted without rejecting a title."""

    code: TitleDiagnosticCode
    section: TitleSection
    terms: tuple[str, ...]
    message: str

    def as_dict(self) -> dict[str, str | list[str]]:
        """Return the stable JSON representation used by persistence."""
        return {
            "code": self.code.value,
            "section": self.section.value,
            "terms": list(self.terms),
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class FormattedTitle:
    """Rendered text plus the tokens and diagnostics which produced it."""

    title: str
    subtitle: str | None = None
    title_tokens: tuple[TitleToken, ...] = ()
    subtitle_tokens: tuple[TitleToken, ...] = ()
    diagnostics: tuple[TitleDiagnostic, ...] = ()


CategoryText = FormattedTitle


@dataclass(frozen=True, slots=True)
class DoorCategory:
    """Facts used by the piston-door title grammar."""

    wiring_restrictions: tuple[str, ...]
    animated_restrictions: tuple[str, ...]
    size: str
    types: tuple[str, ...]
    orientation: str
    component_restrictions: tuple[str, ...] = ()
    miscellaneous_restrictions: tuple[str, ...] = ()
    trapdoor_placement: TrapdoorPlacement | None = None


@dataclass(frozen=True, slots=True)
class ExtenderCategory:
    """Facts used by the piston-extender title grammar."""

    wiring_restrictions: tuple[str, ...]
    orientation: str
    length: int
    types: tuple[str, ...]
    component_restrictions: tuple[str, ...] = ()
    miscellaneous_restrictions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.length <= 0:
            msg = tr(t"Piston extender length must be positive.")
            raise ValidationError(msg)


_WIRING_ORDER: Final = (
    "super seamless",
    "full seamless",
    "semi seamless",
    "quart seamless",
    "seamless",
    "dentless",
    "minor dents only",
    "full trapdoor",
    "trapdoor",
    "full flush",
    "semi flush",
    "flush",
    "full deluxe",
    "semi deluxe",
    "deluxe",
    "full floor hipster",
    "full ceiling hipster",
    "full wall hipster",
    "semi floor hipster",
    "semi ceiling hipster",
    "semi wall hipster",
    "hipster",
    "infinitely expandable",
    "finitely expandable",
    "expandable",
    "full tileable",
    "semi tileable",
    "tileable",
    "weatherproof",
)
_ANIMATED_ORDER: Final = (
    "full symmetrical",
    "symmetrical",
    "super sync",
    "full sync",
    "full-sync",
    "semi sync",
    "clean",
    "spiral",
    "shutter",
    "scissor",
)
_DOOR_TYPE_ORDER: Final = (
    "regular",
    "iris",
    "onion",
    "stargate",
    "full stargate",
    "full lamp",
    "lamp",
    "sissy bar",
    "checkerboard",
    "full checkerboard",
    "windows",
    "redstone block center",
    "sand",
    "glass",
    "glass stripe",
    "center glass",
    "always on lamp",
    "funnel",
    "asdjke",
    "cave",
    "corner",
    "dual cave corner",
    "staircase",
    "gold play button",
    "vortex",
    "pitch",
    "bar",
    "vertical bar",
    "vertical glass stripe",
    "vertical pitch",
    "reversed pitch",
    "inverted funnel",
    "dual funnel",
    "vault",
    "circle",
    "triangle",
    "right triangle",
    "banana",
    "diamond",
    "rail",
    "dual rail",
    "carpet",
    "semi tnt",
    "full tnt",
    "hidden lamp",
    "hidden sand",
)
_EXTENDER_TYPE_ORDER: Final = ("regular", "sand", "glass")
_ORIENTATION_ORDER: Final = ("skydoor", "door", "trapdoor", "upward", "downward", "horizontal")
_ALIASES: Final = {"vault": "dual funnel"}
_COMPONENT_ORDER: Final = (
    "slimeless",
    "no slime",
    "no honey",
    "no gravity blocks",
    "no sticky pistons",
    "contained slime",
    "contained honey",
    "only wiring slime",
    "only wiring honey",
    "only wiring gravity blocks",
    "no observers",
    "observerless",
    "no note blocks",
    "no clocks",
    "no entities",
    "no flying machines",
    "contained",
    "zomba",
    "zombi",
    "torch and dust only",
    "redstone block only",
)
_MISCELLANEOUS_ORDER: Final = (
    "not locational",
    "locational with fixes",
    "directional",
    "directional with fixes",
    "up-to-date",
)


class RulesTitleFormatter:
    """Formatter for the Door Rules title grammar."""

    def format_door(self, category: DoorCategory) -> FormattedTitle:
        diagnostics: list[TitleDiagnostic] = []
        wiring = self._terms(category.wiring_restrictions, TitleSection.WIRING, _WIRING_ORDER, diagnostics)
        animated = self._terms(
            category.animated_restrictions,
            TitleSection.ANIMATED,
            _ANIMATED_ORDER,
            diagnostics,
        )
        types = self._terms(category.types, TitleSection.TYPE, _DOOR_TYPE_ORDER, diagnostics, omit={"regular"})
        orientation = self._orientation(category, wiring, diagnostics)
        if category.trapdoor_placement is not None:
            wiring = tuple(token for token in wiring if "trapdoor" not in _key(token.source_value))
        title_tokens = (
            *wiring,
            *animated,
            _fixed_token(category.size, TitleSection.SIZE),
            *types,
            orientation,
        )
        subtitle_tokens = self._subtitle(
            category.component_restrictions, category.miscellaneous_restrictions, diagnostics
        )
        return _formatted(title_tokens, subtitle_tokens, diagnostics)

    def format_extender(self, category: ExtenderCategory) -> FormattedTitle:
        diagnostics: list[TitleDiagnostic] = []
        wiring = self._terms(category.wiring_restrictions, TitleSection.WIRING, _WIRING_ORDER, diagnostics)
        types = self._terms(category.types, TitleSection.TYPE, _EXTENDER_TYPE_ORDER, diagnostics, omit={"regular"})
        orientation = self._terms((category.orientation,), TitleSection.ORIENTATION, _ORIENTATION_ORDER, diagnostics)
        title_tokens = (
            *wiring,
            *orientation,
            _fixed_token(str(category.length), TitleSection.LENGTH),
            *types,
            _fixed_token("Piston Extender", TitleSection.FIXED_NOUN),
        )
        subtitle_tokens = self._subtitle(
            category.component_restrictions, category.miscellaneous_restrictions, diagnostics
        )
        return _formatted(title_tokens, subtitle_tokens, diagnostics)

    def format_record(self, record_class: str, category: FormattedTitle) -> FormattedTitle:
        record_name = record_class.replace("_", " ").title()
        record_token = _fixed_token(record_name, TitleSection.RECORD_CLASS)
        category_tokens = category.title_tokens or (_fixed_token(category.title, TitleSection.FIXED_NOUN),)
        title_tokens = (record_token, *category_tokens)
        return FormattedTitle(
            title=_render(title_tokens),
            subtitle=category.subtitle,
            title_tokens=title_tokens,
            subtitle_tokens=category.subtitle_tokens,
            diagnostics=category.diagnostics,
        )

    def _subtitle(
        self,
        components: tuple[str, ...],
        miscellaneous: tuple[str, ...],
        diagnostics: list[TitleDiagnostic],
    ) -> tuple[TitleToken, ...]:
        component_tokens = self._terms(components, TitleSection.COMPONENT, _COMPONENT_ORDER, diagnostics)
        miscellaneous_tokens = self._terms(
            miscellaneous,
            TitleSection.MISCELLANEOUS,
            _MISCELLANEOUS_ORDER,
            diagnostics,
        )
        return (*component_tokens, *miscellaneous_tokens)

    def _orientation(
        self,
        category: DoorCategory,
        wiring: tuple[TitleToken, ...],
        diagnostics: list[TitleDiagnostic],
    ) -> TitleToken:
        trapdoor_terms = tuple(token.source_value for token in wiring if "trapdoor" in _key(token.source_value))
        if category.trapdoor_placement is not None:
            replacement = f"{category.trapdoor_placement.value.title()} Trapdoor"
            return TitleToken(
                value=replacement,
                section=TitleSection.ORIENTATION,
                source_value=category.orientation,
            )
        if trapdoor_terms:
            diagnostics.append(
                TitleDiagnostic(
                    code=TitleDiagnosticCode.AMBIGUOUS_TRAPDOOR,
                    section=TitleSection.ORIENTATION,
                    terms=trapdoor_terms,
                    message="Trapdoor placement is required to replace the door orientation.",
                )
            )
        tokens = self._terms((category.orientation,), TitleSection.ORIENTATION, _ORIENTATION_ORDER, diagnostics)
        return tokens[0]

    def _terms(
        self,
        values: tuple[str, ...],
        section: TitleSection,
        order: tuple[str, ...],
        diagnostics: list[TitleDiagnostic],
        *,
        omit: set[str] | None = None,
    ) -> tuple[TitleToken, ...]:
        omitted = omit or set()
        order_index = {_key(value): index for index, value in enumerate(order)}
        unique: dict[str, str] = {}
        for raw_value in values:
            value = raw_value.strip()
            if not value:
                continue
            key = _key(value)
            if key in omitted:
                continue
            if key in unique:
                diagnostics.append(
                    TitleDiagnostic(
                        code=TitleDiagnosticCode.DUPLICATE_TERM,
                        section=section,
                        terms=(unique[key], value),
                        message="Duplicate terms are rendered once.",
                    )
                )
                continue
            unique[key] = value

        alias_groups: dict[str, list[str]] = {}
        for key in unique:
            alias_groups.setdefault(_ALIASES.get(key, key), []).append(key)
        for canonical, keys in alias_groups.items():
            if len(keys) < 2:
                continue
            retained = canonical if canonical in keys else keys[0]
            removed = tuple(key for key in keys if key != retained)
            diagnostics.append(
                TitleDiagnostic(
                    code=TitleDiagnosticCode.DUPLICATE_ALIAS,
                    section=section,
                    terms=tuple(unique[key] for key in keys),
                    message=f"Aliases duplicate {unique[retained]!r}; the non-alias term is retained.",
                )
            )
            for key in removed:
                del unique[key]

        known = sorted(
            ((key, value) for key, value in unique.items() if key in order_index or _is_dynamic_known(key, section)),
            key=lambda pair: (order_index.get(pair[0], len(order_index)), pair[0]),
        )
        unknown = sorted(
            (
                (key, value)
                for key, value in unique.items()
                if key not in order_index and not _is_dynamic_known(key, section)
            ),
            key=lambda pair: (pair[0], pair[1]),
        )
        for _unknown_key, value in unknown:
            diagnostics.append(
                TitleDiagnostic(
                    code=TitleDiagnosticCode.UNKNOWN_TERM,
                    section=section,
                    terms=(value,),
                    message="The term is retained but is not recognized by this title ruleset.",
                )
            )
        return tuple(
            TitleToken(value=value, section=section, recognized=recognized, source_value=value)
            for recognized, pairs in ((True, known), (False, unknown))
            for _, value in pairs
        )


def _key(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").split())


def _is_dynamic_known(value: str, section: TitleSection) -> bool:
    return section is TitleSection.WIRING and re.fullmatch(r"\d+ (?:wide|high)", value) is not None


def _fixed_token(value: str, section: TitleSection) -> TitleToken:
    return TitleToken(value=value.strip(), section=section)


def _formatted(
    title_tokens: tuple[TitleToken, ...],
    subtitle_tokens: tuple[TitleToken, ...],
    diagnostics: list[TitleDiagnostic],
) -> FormattedTitle:
    subtitle = _render(subtitle_tokens)
    return FormattedTitle(
        title=_render(title_tokens),
        subtitle=subtitle or None,
        title_tokens=title_tokens,
        subtitle_tokens=subtitle_tokens,
        diagnostics=tuple(diagnostics),
    )


def _render(tokens: tuple[TitleToken, ...]) -> str:
    return " ".join(token.value for token in tokens)
