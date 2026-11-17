"""Wire schema declarations owned by the Html target."""

from squid_ui.ext.html.scene import model as m
from squid_ui.scene.schema import SceneSchema

SCHEMA = SceneSchema(
    tagged=(
        (m.HtmlText, m.HtmlText.KIND, m.HtmlText.KIND),
        (m.HtmlElement, m.HtmlElement.KIND, m.HtmlElement.KIND),
        (m.HtmlBody, m.HtmlBody.KIND, m.HtmlBody.KIND),
    ),
    bodies=(m.HtmlBody,),
    plain=(),
    required={
        m.HtmlAttribute: ("name", "value"),
        m.HtmlActionRef: ("action", "mode"),
        m.HtmlRouteRef: ("route_id",),
        m.HtmlFormRef: ("key", "field_name"),
        m.HtmlUrlRef: ("url",),
        m.HtmlTimeRef: ("instant", "timezone", "style"),
        m.HtmlColourRef: ("value",),
        m.HtmlAssetRef: ("key", "name", "media_type"),
    },
    unions={"html_node": (m.HtmlText, m.HtmlElement)},
    renames={},
)
