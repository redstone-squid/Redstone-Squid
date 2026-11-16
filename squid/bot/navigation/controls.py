"""Controls referencing UI endpoints directly."""

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.routes import AppRequest
from squid.bot.ui import text_node
from squid.core.i18n import tr
from squid_i18n import TextLike
from squid_ui.routing import Endpoint


def invalid_address_response() -> sd.Response:
    return sd.Response(
        text_node(
            tr(t"navigation-invalid-address = That screen address is invalid. Try /builds, /drafts, or /account.")
        ),
        audience="personal",
    )


def destination_button[**P](
    label: TextLike, endpoint: Endpoint[AppRequest, P], /, *args: P.args, **kwargs: P.kwargs
) -> sl.primitives.RoutedButton:
    return sl.primitives.RoutedButton(label, endpoint.custom_id(*args, **kwargs))
