"""Fit one selected primitive layout into Discord's available space.

The planner chooses a representation before calling `solver.measure`. The builder
creates mutable text slots; allocation gives each text unit a character allowance
and applies its overflow policy. The measured result contains filled slots,
resource costs, and diagnostics for the planner to compare with other candidates.
"""
