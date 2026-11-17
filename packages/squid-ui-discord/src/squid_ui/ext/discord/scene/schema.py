"""Wire schema declarations owned by the Discord target."""

from squid_ui.content.emoji import Emoji
from squid_ui.content.entity import EntityRef
from squid_ui.ext.discord.scene import model as m
from squid_ui.scene.schema import SceneSchema

SCHEMA = SceneSchema(
    tagged=(
        (m.Text, m.Text.KIND, m.Text.KIND),
        (m.Time, m.Time.KIND, m.Time.KIND),
        (m.ZonedTime, m.ZonedTime.KIND, m.ZonedTime.KIND),
        (m.File, m.File.KIND, m.File.KIND),
        (m.Separator, m.Separator.KIND, m.Separator.KIND),
        (m.Link, m.Link.KIND, m.Link.KIND),
        (m.Button, m.Button.KIND, m.Button.KIND),
        (m.Row, m.Row.KIND, m.Row.KIND),
        (m.Select, m.Select.KIND, m.Select.KIND),
        (m.RoutedSelect, m.RoutedSelect.KIND, m.RoutedSelect.KIND),
        (m.EntitySelect, m.EntitySelect.KIND, m.EntitySelect.KIND),
        (m.RoutedButton, m.RoutedButton.KIND, m.RoutedButton.KIND),
        (m.PremiumButton, m.PremiumButton.KIND, m.PremiumButton.KIND),
        (m.Thumbnail, m.Thumbnail.KIND, m.Thumbnail.KIND),
        (m.Gallery, m.Gallery.KIND, m.Gallery.KIND),
        (m.Section, m.Section.KIND, m.Section.KIND),
        (m.Panel, m.Panel.KIND, m.Panel.KIND),
        (m.Extension, m.Extension.KIND, m.Extension.KIND),
        (m.ComponentsV2, m.ComponentsV2.KIND, m.ComponentsV2.KIND),
        (m.ClassicMessage, m.ClassicMessage.KIND, m.ClassicMessage.KIND),
    ),
    bodies=(m.ComponentsV2, m.ClassicMessage),
    plain=(("embed", m.Embed), ("classic_row", m.ClassicRow)),
    required={
        m.ClassicRow: ("controls",),
        m.Option: ("label", "value", "description", "default"),
        m.GalleryItem: ("url", "description"),
        Emoji: ("name", "id", "animated"),
        EntityRef: ("kind", "id"),
    },
    unions={
        "node": (
            m.Text,
            m.Time,
            m.ZonedTime,
            m.File,
            m.Separator,
            m.Link,
            m.Button,
            m.Row,
            m.Select,
            m.RoutedSelect,
            m.EntitySelect,
            m.RoutedButton,
            m.PremiumButton,
            m.Thumbnail,
            m.Gallery,
            m.Section,
            m.Panel,
            m.Extension,
        )
    },
    renames={m.Extension: {"kind": "extension"}},
)
