"""Propose target realizations.

Policies and inherited constraints are shared by the primitive, content, and
interaction handlers. Navigation constructs region controls before certification;
the dispatcher chooses the handler for each semantic occurrence.
"""

from squid_ui.planning.offers.dispatch import semantic_offers as semantic_offers
from squid_ui.planning.offers.navigation import navigation_candidate as navigation_candidate
from squid_ui.planning.offers.navigation import pagination_chrome as pagination_chrome
from squid_ui.planning.offers.navigation import region_cursor_key as region_cursor_key
from squid_ui.planning.offers.policy import OfferPolicy as OfferPolicy
from squid_ui.planning.offers.policy import Offers as Offers
