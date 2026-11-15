"""Declared display edges for planned Discord, HTML, and Slack scenes."""

from collections.abc import Iterator

from squid_ui import scene as s

# References, attributes, assets, pager records, and extension payloads remain metadata.
# Only display values participate in parent/child and sibling relationships.
_SCENE_FIELDS: dict[type, tuple[str, ...]] = {
    s.Scene: ("body",),
    s.ComponentsV2: ("children",),
    s.ClassicMessage: ("embeds", "rows"),
    s.ClassicRow: ("controls",),
    s.Embed: ("author", "fields", "thumbnail", "image", "footer"),
    s.EmbedAuthor: (),
    s.EmbedField: (),
    s.EmbedFooter: (),
    s.EmbedMedia: (),
    s.Text: (),
    s.Time: (),
    s.ZonedTime: (),
    s.File: (),
    s.Separator: (),
    s.Link: (),
    s.PremiumButton: (),
    s.Button: (),
    s.RoutedButton: (),
    s.Option: (),
    s.Select: ("options",),
    s.RoutedSelect: ("options",),
    s.EntitySelect: (),
    s.Row: ("items",),
    s.Thumbnail: (),
    s.GalleryItem: (),
    s.Gallery: ("items",),
    s.Section: ("texts", "accessory"),
    s.Panel: ("children",),
    s.Extension: (),
    s.HtmlBody: ("children",),
    s.HtmlElement: ("children",),
    s.HtmlText: (),
    s.SlackMessage: ("blocks",),
    s.SlackModalView: ("title", "blocks", "close", "submit"),
    s.SlackHomeView: ("blocks",),
    s.SlackSection: ("text", "fields", "accessory"),
    s.SlackHeader: ("text",),
    s.SlackContext: ("elements",),
    s.SlackDivider: (),
    s.SlackImage: ("title",),
    s.SlackActions: ("elements",),
    s.SlackInput: ("label", "element", "hint"),
    s.SlackTable: ("rows",),
    s.SlackCard: ("title", "description", "actions"),
    s.SlackCarousel: ("cards",),
    s.SlackAlert: ("title", "text"),
    s.SlackText: (),
    s.SlackOption: ("label", "description"),
    s.SlackButton: ("label",),
    s.SlackSelect: ("placeholder", "options"),
    s.SlackTextInput: ("placeholder",),
    s.SlackNumberInput: (),
    s.SlackDatePicker: ("placeholder",),
    s.SlackTimePicker: ("placeholder",),
    s.SlackCheckboxes: ("options",),
    s.SlackRadioButtons: ("options",),
}
_SCENE_TYPES = tuple(_SCENE_FIELDS)


def is_scene_node(node: object) -> bool:
    """Whether this value has a declared scene display shape, including scene/body roots."""
    return isinstance(node, _SCENE_TYPES)


def scene_children(node: object) -> Iterator[tuple[str, object]]:
    """Yield display children without traversing metadata or reconstructing any value.

    Text stored directly on a node remains an attribute. Structured text values,
    such as SlackText and HtmlText, are children. Tuple containers are transparent;
    Slack table cells are direct children with row and column positions in their paths.
    """
    names = _SCENE_FIELDS.get(type(node))
    if names is None:
        names = next((fields for kind, fields in _SCENE_FIELDS.items() if isinstance(node, kind)), ())
    for name in names:
        yield from _values(name, getattr(node, name))


def _values(path: str, value: object) -> Iterator[tuple[str, object]]:
    if isinstance(value, tuple):
        for index, child in enumerate(value):
            yield from _values(f"{path}.{index}", child)
    elif value is not None:
        yield path, value
