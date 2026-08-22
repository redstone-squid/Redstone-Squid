"""Durable route identities owned by generic poll cards."""

from squid.bot.routes._root import routes

polls = routes.group("polls")

poll_close = polls.define("close", aliases=("poll:close",))
poll_refresh = polls.define("refresh", aliases=("poll:refresh",))
