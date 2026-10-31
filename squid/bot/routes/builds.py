"""Durable route identities owned by build cards."""

from squid.bot.routes._root import routes

builds = routes.group("builds")

build_edit = builds.define("{build_id:int}:edit", aliases=("edit:build:{build_id:int}",))
