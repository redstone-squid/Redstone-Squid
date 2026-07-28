from pytest_archon import archrule


def test_application_services_do_not_import_discord_or_bot_layer() -> None:
    (
        archrule("application services stay independent from Discord adapters")
        .match("squid.services*")
        .should_not_import("discord*")
        .should_not_import("squid.bot*")
        .check("squid", only_direct_imports=True)
    )
