"""Durable route identities owned by build-log consent banners."""

from squid.bot.routes._root import routes

build_log_consents = routes.group("build-log-consents")

build_log_consent = build_log_consents.define("new", aliases=("build_log:consent",))
