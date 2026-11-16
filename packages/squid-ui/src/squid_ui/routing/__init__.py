"""Shared route patterns and tables for navigation and persistent controls.

Patterns build and parse identifiers; tables associate them with endpoints.
Delivery adapters retain handler invocation, acknowledgement, and lifecycle policy.
"""

from squid_ui.routing.pattern import Converter as Converter
from squid_ui.routing.pattern import RoutePattern as RoutePattern
from squid_ui.routing.table import RouteTable as RouteTable
