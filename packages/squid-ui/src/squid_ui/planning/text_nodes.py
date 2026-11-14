"""Lower resolved text structure into the HTML scene's existing safe elements."""

from squid_i18n import tree
from squid_ui import scene


def html_text(nodes: tuple[tree.Node, ...]) -> scene.HtmlNode:
    children = tuple(child for node in nodes for child in _html_nodes(node))
    if len(children) == 1:
        return children[0]
    return scene.HtmlElement(scene.HtmlTag.SPAN, children)


def _html_nodes(node: tree.Node) -> tuple[scene.HtmlNode, ...]:
    attrs = dict(node.attrs)
    children = tuple(child for item in node.children for child in _html_nodes(item))
    match node.kind:
        case "inline" | "sequence":
            return children
        case "text" | "mention" | "emoji" | "discord_markup" | "timestamp":
            return (scene.HtmlText(tree.plain(node)),)
        case "softbreak":
            return (scene.HtmlText("\n"),)
        case "hardbreak":
            return (scene.HtmlElement(scene.HtmlTag.BR, ()),)
        case "code_inline":
            return (scene.HtmlElement(scene.HtmlTag.CODE, (scene.HtmlText(node.content),)),)
        case "fence" | "code_block":
            return (
                scene.HtmlElement(
                    scene.HtmlTag.PRE, (scene.HtmlElement(scene.HtmlTag.CODE, (scene.HtmlText(node.content),)),)
                ),
            )
        case "link":
            title = attrs.get("title")
            attributes = () if title is None else (scene.HtmlAttribute(scene.HtmlAttributeName.TITLE, title),)
            return (
                scene.HtmlElement(
                    scene.HtmlTag.A, children, attributes=attributes, url=scene.HtmlUrlRef(attrs["href"])
                ),
            )
        case "image":
            return (
                scene.HtmlElement(
                    scene.HtmlTag.IMG,
                    (),
                    attributes=(scene.HtmlAttribute(scene.HtmlAttributeName.ALT, tree.plain(node)),),
                    url=scene.HtmlUrlRef(attrs["src"]),
                ),
            )
        case _:
            try:
                tag = scene.HtmlTag(node.tag)
            except ValueError:
                return children or (scene.HtmlText(node.content),)
            return (scene.HtmlElement(tag, children),)
