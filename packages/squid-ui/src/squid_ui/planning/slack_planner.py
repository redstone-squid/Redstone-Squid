"""Slack planner backend assembled in the following implementation milestone."""

from squid_ui.errors import LayoutInvariantError


class SlackPlanner:
    """Compile portable documents into Slack scenes."""

    def plan(self, *_args: object, **_kwargs: object) -> None:
        message = "Slack planning is not available until the planner milestone is installed"
        raise LayoutInvariantError(message)


SLACK_PLANNER = SlackPlanner()


__all__ = ["SLACK_PLANNER", "SlackPlanner"]
