"""Shared route patterns and tables for navigation and persistent controls.

Patterns build and parse identifiers; tables associate them with endpoints.
Delivery adapters retain handler invocation, acknowledgement, and lifecycle policy.
"""

from squid_ui.routing.address import AddressParts as AddressParts
from squid_ui.routing.address import AddressRoute as AddressRoute
from squid_ui.routing.address import InvalidAddress as InvalidAddress
from squid_ui.routing.controls import CONVERTERS as CONVERTERS
from squid_ui.routing.controls import MAX_ROUTE_ID_LENGTH as MAX_ROUTE_ID_LENGTH
from squid_ui.routing.controls import Route as Route
from squid_ui.routing.pattern import Converter as Converter
from squid_ui.routing.pattern import RoutePattern as RoutePattern
from squid_ui.routing.pattern import RouteTable as RouteTable
from squid_ui.routing.router import Endpoint as Endpoint
from squid_ui.routing.router import Middleware as Middleware
from squid_ui.routing.router import Proceed as Proceed
from squid_ui.routing.router import RouteMatch as RouteMatch
from squid_ui.routing.router import Router as Router
