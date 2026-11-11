"""Durable route identities for build workspaces.

The handlers live in controls.py, which imports the screens they open. Screens that
only need to draw a routed button import the identity from here, so drawing a button
does not create an import cycle with the handler that answers it.
"""

from squid.bot.routes._root import _feature_group

builds, _builds_created = _feature_group("builds")
build_edit = builds.define("{build_id:int}:edit", aliases=("edit:build:{build_id:int}",))
build_edit_recovery = builds.define("{build_id:int}:edit:recover")
draft_reopen = builds.define("draft:{draft_id}:reopen")
revision_reopen = builds.define("revision:{proposal_id}:reopen")
inference_reopen = builds.define("inference:{run_id}:reopen")

__all__ = ["build_edit", "build_edit_recovery", "builds", "draft_reopen", "inference_reopen", "revision_reopen"]
