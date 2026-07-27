import ast
from pathlib import Path


def test_application_services_do_not_import_discord_or_bot_layer() -> None:
    services_dir = Path("squid/services")
    violations: list[str] = []

    for path in services_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported_modules: list[str]
            if isinstance(node, ast.Import):
                imported_modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported_modules = [node.module or ""]
            else:
                continue

            violations.extend(
                f"{path}:{node.lineno} imports {module}"
                for module in imported_modules
                if module == "discord" or module.startswith(("discord.", "squid.bot"))
            )

    assert violations == []
