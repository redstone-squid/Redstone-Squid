"""Pins mode safety for exact Discord factories. Nothing here runs."""

import squid_ui_discord as sd

classic = sd.classic.card("classic")
v2 = sd.v2.panel("v2")

sd.classic.card(classic)
sd.v2.panel(v2)
sd.classic.card(v2)  # pyrefly: ignore[bad-argument-type]
sd.v2.panel(classic)  # pyrefly: ignore[bad-argument-type]
