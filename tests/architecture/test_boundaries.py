import ast
import re
import sys
import tomllib
from pathlib import Path

from babel.messages.pofile import read_po
from packaging.requirements import Requirement
from pytest_archon import archrule

from squid.core.extract import deferred_msgid, locale_str_msgid
from tests.support.source_tree import source_tree

# Roots for the AST scans that state repo-wide invariants. The squid-ui workspace member
# is held to the same rules as squid itself.
SCAN_ROOTS = (
    Path("squid"),
    Path("packages/squid-reactivity/src"),
    Path("packages/squid-ui/src"),
    Path("packages/squid-ui-discord/src"),
    Path("packages/squid-ui-slack/src"),
    Path("packages/squid-ui-widgets/src"),
    Path("packages/squid-replication/src"),
    Path("packages/squid-storage/src"),
)

COMPILER_PASS_ROOT = Path("packages/squid-ui/src/squid_ui/planning")

FRAMEWORK_DISTRIBUTIONS = {
    "anyio": "anyio",
    "asyncpg": "asyncpg",
    "discord": "discord-py",
    "loro": "loro",
    "packaging": "packaging",
    "pycrdt": "pycrdt",
    "slack_sdk": "slack-sdk",
    "squid_reactivity": "squid-reactivity",
    "squid_replication": "squid-replication",
    "squid_storage": "squid-storage",
    "squid_ui": "squid-ui",
    "squid_ui_discord": "squid-ui-discord",
    "squid_ui_slack": "squid-ui-slack",
    "squid_ui_widgets": "squid-ui-widgets",
}

FRAMEWORK_VERSION = "0.1.0a1"
FRAMEWORK_PACKAGE_NAMES = frozenset(
    {
        "squid-reactivity",
        "squid-replication",
        "squid-storage",
        "squid-ui",
        "squid-ui-discord",
        "squid-ui-slack",
        "squid-ui-widgets",
    }
)
FRAMEWORK_CLASSIFIERS = [
    "Development Status :: 3 - Alpha",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.14",
    "Typing :: Typed",
]
FRAMEWORK_URLS = {
    "Changelog": "https://github.com/redstone-squid/Redstone-Squid/blob/master/CHANGELOG.md",
    "Documentation": "https://redstone-squid.github.io/Redstone-Squid/squid-ui/",
    "Issues": "https://github.com/redstone-squid/Redstone-Squid/issues",
    "Repository": "https://github.com/redstone-squid/Redstone-Squid",
}


def _scanned_files(roots: tuple[Path, ...] = SCAN_ROOTS) -> list[Path]:
    return [path for root in roots for path in root.rglob("*.py")]


def _dependency_name(requirement: str) -> str:
    return re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0].strip().lower()


def test_process_entry_points_use_concrete_runtime_constructors() -> None:
    """Production startup may not inject an arbitrary service factory into bootstrap."""
    expected_calls = {
        Path("squid/bot/app.py"): ("_run_bot", "create_bot_runtime"),
        Path("squid/worker/app.py"): ("_run_worker", "create_worker_runtime"),
    }
    for path, (function_name, constructor_name) in expected_calls.items():
        function = next(
            node
            for node in source_tree(path).body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name
        )
        calls = {
            node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert constructor_name in calls

    api_factory = next(
        node
        for node in source_tree(Path("squid/api/app.py")).body
        if isinstance(node, ast.FunctionDef) and node.name == "create_api_app"
    )
    assert isinstance(api_factory.args.defaults[0], ast.Name)
    assert api_factory.args.defaults[0].id == "create_api_runtime"


def test_submission_domain_and_application_do_not_import_pydantic() -> None:
    """Transport validation stays outside submission policy and orchestration."""
    violations = [
        f"{path}:{node.lineno}"
        for root in (Path("squid/submissions/domain"), Path("squid/submissions/application"))
        for path in root.rglob("*.py")
        for node in ast.walk(source_tree(path))
        if (isinstance(node, ast.Import) and any(alias.name == "pydantic" for alias in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module == "pydantic")
    ]

    assert violations == []


def test_background_tasks_use_owned_anyio_task_groups() -> None:
    """Task lifetime stays with an anyio owner instead of escaping through asyncio."""
    banned = {"TaskGroup", "create_task", "ensure_future"}
    violations: list[str] = []
    for path in _scanned_files(SCAN_ROOTS[1:]):
        tree = source_tree(path)
        asyncio_aliases = {
            alias.asname or alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == "asyncio"
        }
        direct_aliases = {
            alias.asname or alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "asyncio"
            for alias in node.names
            if alias.name in banned
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in direct_aliases:
                violations.append(f"{path}:{node.lineno}: {node.func.id}")
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in banned
                and (
                    node.func.attr != "TaskGroup"
                    or (isinstance(node.func.value, ast.Name) and node.func.value.id in asyncio_aliases)
                )
            ):
                violations.append(f"{path}:{node.lineno}: {node.func.attr}")

    assert violations == [], f"use anyio.create_task_group() with an explicit owner: {violations}"


def test_framework_runtime_imports_are_declared_in_package_metadata() -> None:
    """A direct import needs a direct dependency or an explicitly named extra."""
    violations: list[tuple[str, str]] = []
    for package in sorted(Path("packages").iterdir()):
        metadata_path = package / "pyproject.toml"
        source = package / "src"
        if not metadata_path.exists() or not source.exists():
            continue
        project = tomllib.loads(metadata_path.read_text(encoding="utf-8"))["project"]
        declared = {_dependency_name(value) for value in project.get("dependencies", ())}
        declared.update(
            _dependency_name(value) for values in project.get("optional-dependencies", {}).values() for value in values
        )
        own_distribution = project["name"]
        for path in source.rglob("*.py"):
            for node in ast.walk(source_tree(path)):
                if isinstance(node, ast.Import):
                    modules = (alias.name.partition(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    modules = (node.module.partition(".")[0],)
                else:
                    continue
                for module in modules:
                    distribution = FRAMEWORK_DISTRIBUTIONS.get(module)
                    if distribution is not None and distribution not in {own_distribution, *declared}:
                        violations.append((str(path), distribution))

    assert violations == []


def test_framework_distributions_share_publishable_alpha_metadata() -> None:
    """Every independently built member carries the same public release contract."""
    root_license = Path("LICENSE").read_text(encoding="utf-8")
    found: set[str] = set()
    for package in sorted(Path("packages").iterdir()):
        metadata_path = package / "pyproject.toml"
        if not metadata_path.exists():
            continue
        project = tomllib.loads(metadata_path.read_text(encoding="utf-8"))["project"]
        name = project["name"]
        found.add(name)
        assert project["version"] == FRAMEWORK_VERSION
        assert project["requires-python"] == ">=3.14"
        assert project["readme"] == "README.md"
        assert project["license"] == "MIT"
        assert project["license-files"] == ["LICENSE"]
        assert project["authors"] == [{"name": "Redstone Squid contributors"}]
        assert project["classifiers"] == FRAMEWORK_CLASSIFIERS
        assert project["urls"] == FRAMEWORK_URLS
        assert (package / "README.md").is_file()
        assert (package / "LICENSE").read_text(encoding="utf-8") == root_license
        assert (package / "src" / name.replace("-", "_") / "py.typed").is_file()

        requirements = list(project.get("dependencies", ()))
        requirements.extend(
            requirement for extra in project.get("optional-dependencies", {}).values() for requirement in extra
        )
        for raw_requirement in requirements:
            requirement = Requirement(raw_requirement)
            if requirement.name in FRAMEWORK_PACKAGE_NAMES:
                assert str(requirement.specifier) == f"=={FRAMEWORK_VERSION}"

    assert found == FRAMEWORK_PACKAGE_NAMES


def test_compiler_pass_packages_are_not_facades() -> None:
    """Pass packages identify owners; their initializers must not aggregate those owners."""
    for package in ("layout_measurement", "semantic_adaptation"):
        path = COMPILER_PASS_ROOT / package / "__init__.py"
        body = source_tree(path).body
        assert len(body) == 1
        assert isinstance(body[0], ast.Expr)
        assert isinstance(body[0].value, ast.Constant)
        assert isinstance(body[0].value.value, str)


def test_generic_planning_modules_do_not_depend_on_the_discord_backend() -> None:
    """The public target seam must not acquire Discord's IR or layout solver types."""
    generic = tuple(COMPILER_PASS_ROOT / name for name in ("dialect.py", "planner.py", "target.py"))
    blocked_modules = (
        "squid_ui.planning.limits",
        "squid_ui.planning.layout_measurement",
        "squid_ui.primitives",
        "squid_ui.target_types",
    )
    violations: list[tuple[Path, int, str]] = []
    for path in generic:
        for node in ast.walk(source_tree(path)):
            if isinstance(node, ast.Import):
                imported = (alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported = (node.module,)
            else:
                continue
            violations.extend(
                (path, node.lineno, module)
                for module in imported
                if any(module == blocked or module.startswith(f"{blocked}.") for blocked in blocked_modules)
            )

    assert violations == []


def test_layouts_package_stays_standalone() -> None:
    """The UI framework package must remain publishable: no host-project or adapter imports."""
    (
        archrule("squid-ui stays independent from the host application")
        .match("squid_ui*")
        .should_not_import("squid")
        .should_not_import("squid.*")
        .should_not_import("sqlalchemy*")
        .should_not_import("fastapi*")
        .should_not_import("nucleation*")
        .should_not_import("squid_ui_discord*")
        .should_not_import("squid_ui_slack*")
        .should_not_import("squid_ui_widgets*")
        .check("squid_ui", only_direct_imports=True)
    )


def test_discord_package_stays_a_leaf() -> None:
    """The transport adapter is downstream of everything; nothing may point back at it."""
    (
        archrule("squid-ui-discord depends on the engine, not on the host")
        .match("squid_ui_discord*")
        .should_not_import("squid")
        .should_not_import("squid.*")
        .should_not_import("sqlalchemy*")
        .should_not_import("fastapi*")
        .should_not_import("nucleation*")
        .check("squid_ui_discord", only_direct_imports=True)
    )


def test_slack_package_stays_a_leaf() -> None:
    """The Slack SDK renderer depends on the engine, never a host or sibling adapter."""
    (
        archrule("squid-ui-slack depends on the engine, not on runtimes or the host")
        .match("squid_ui_slack*")
        .should_not_import("squid")
        .should_not_import("squid.*")
        .should_not_import("squid_ui_discord*")
        .should_not_import("squid_ui_widgets*")
        .should_not_import("squid_storage*")
        .should_not_import("anyio*")
        .check("squid_ui_slack", only_direct_imports=True)
    )


def test_private_names_do_not_cross_distribution_boundaries() -> None:
    """A leading-underscore name imported from a sibling distribution is an undocumented ABI.

    Inside one distribution, private imports between modules are that package's own
    business. Across distributions they couple separately versioned wheels to
    implementation details, so the only sanctioned crossing is squid_reactivity.internals,
    which exists to re-export exactly what the suite's siblings need.
    """
    framework_packages = {
        "squid_reactivity",
        "squid_replication",
        "squid_storage",
        "squid_ui",
        "squid_ui_discord",
        "squid_ui_slack",
        "squid_ui_widgets",
    }
    violations: list[tuple[str, int, str]] = []
    for root in SCAN_ROOTS:
        if root == Path("squid"):
            continue
        own = root.parent.name.replace("-", "_")
        for path in root.rglob("*.py"):
            for node in ast.walk(source_tree(path)):
                if not isinstance(node, ast.ImportFrom) or node.module is None or node.level:
                    continue
                source = node.module.partition(".")[0]
                if source == own or source not in framework_packages:
                    continue
                violations.extend(
                    (str(path), node.lineno, f"{node.module}.{alias.name}")
                    for alias in node.names
                    if alias.name.startswith("_")
                )

    assert violations == []


def test_reactive_package_has_no_hard_dependencies() -> None:
    """The extracted runtime may import only itself and Python's standard library."""
    allowed = sys.stdlib_module_names | {"squid_reactivity"}
    violations: list[tuple[Path, int, str]] = []
    for path in Path("packages/squid-reactivity/src").rglob("*.py"):
        for node in ast.walk(source_tree(path)):
            if isinstance(node, ast.Import):
                imported = ((node.lineno, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported = ((node.lineno, node.module),)
            else:
                continue
            violations.extend(
                (path, line, module) for line, module in imported if module.partition(".")[0] not in allowed
            )

    assert violations == []


def test_patterns_package_is_transport_free() -> None:
    """State machines render through the engine; they never name a runtime.

    The payoff of the extraction: `squid_ui_widgets` sits beside `squid_ui_discord`, not below it,
    so a pattern cannot quietly acquire a Discord dependency and become unusable anywhere else.
    """
    (
        archrule("squid-ui-widgets is frontend-neutral")
        .match("squid_ui_widgets*")
        .should_not_import("discord*")
        .should_not_import("anyio*")
        .should_not_import("squid_ui_discord*")
        .should_not_import("squid_storage*")
        .should_not_import("squid")
        .should_not_import("squid.*")
        .check("squid_ui_widgets", only_direct_imports=True)
    )


def test_only_the_bot_transport_uses_the_ui_packages() -> None:
    """Presentation is `squid.bot`'s business; shared deferred text is the sole exception."""
    for package in ("squid_ui_discord*", "squid_ui_slack*", "squid_ui_widgets*"):
        (
            archrule(f"{package} is a presentation concern")
            .match("squid*")
            .exclude("squid.bot*")
            .should_not_import(package)
            .check("squid", only_direct_imports=True)
        )

    violations: list[str] = []
    for path in Path("squid").rglob("*.py"):
        if path.parts[:2] == ("squid", "bot"):
            continue
        for node in ast.walk(source_tree(path)):
            if isinstance(node, ast.Import):
                imports = (alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports = (node.module,)
            else:
                continue
            violations.extend(
                f"{path}:{node.lineno}: {imported}"
                for imported in imports
                if imported.startswith("squid_ui") and imported != "squid_ui.text"
            )

    assert violations == []


def test_tr_is_the_only_direct_translation_entry_point() -> None:
    """Translation spelling stays uniform across the application and UI packages."""
    retired = {"_", "t", "translate", "ntranslate", "L"}
    violations = [
        f"{path}:{node.lineno}: {node.func.id}"
        for path in _scanned_files()
        for node in ast.walk(source_tree(path))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in retired
    ]

    assert violations == []


def test_localized_messages_are_present_in_the_catalog_template() -> None:
    with Path("locales/squid.pot").open(encoding="utf-8") as fileobj:
        catalog = read_po(fileobj)
    msgids = {
        extracted
        for message in catalog
        for extracted in (message.id if isinstance(message.id, tuple) else (message.id,))
    }
    authored = {
        extracted
        for path in Path("squid").rglob("*.py")
        for node in ast.walk(source_tree(path))
        if isinstance(node, ast.Call) and (msgid := deferred_msgid(node)) is not None
        for extracted in (msgid if isinstance(msgid, tuple) else (msgid,))
    }
    authored.update(
        msgid
        for path in Path("squid").rglob("*.py")
        for node in ast.walk(source_tree(path))
        if isinstance(node, ast.Call) and (msgid := locale_str_msgid(node)) is not None
    )

    assert authored - msgids == set()


def test_static_layout_rendering_stays_behind_the_host_wrapper() -> None:
    violations: list[str] = []
    for path in Path("squid").rglob("*.py"):
        if path == Path("squid/bot/ui.py"):
            continue
        tree = source_tree(path)
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            parts: list[str] = []
            target = node.func
            while isinstance(target, ast.Attribute):
                parts.append(target.attr)
                target = target.value
            if not isinstance(target, ast.Name):
                continue
            resolved = ".".join((aliases.get(target.id, target.id), *reversed(parts)))
            # These are dotted *call* targets (package attribute -> function), not module
            # paths: squid_ui_discord.rendering defines render_message(), so a call resolves
            # to "squid_ui_discord.render_message" regardless of which file render_message() lives in.
            if resolved in {"squid_ui_discord.render_message", "squid_ui_discord.render_static"}:
                violations.append(f"{path}:{node.lineno}")

    assert violations == []


def test_exception_model_imports_no_transport() -> None:
    (
        archrule("application exceptions stay independent from transport adapters")
        .match("squid.core.errors")
        .should_not_import("discord*")
        .should_not_import("fastapi*")
        .should_not_import("squid.bot*")
        .check("squid", only_direct_imports=True)
    )


def test_i18n_core_imports_no_transport() -> None:
    (
        archrule("translation lookup stays independent from transport adapters")
        .match("squid.core.i18n")
        .should_not_import("discord*")
        .should_not_import("fastapi*")
        .should_not_import("squid.bot*")
        .should_not_import("squid.api*")
        .check("squid", only_direct_imports=True)
    )


def test_domain_layers_are_framework_and_persistence_independent() -> None:
    (
        archrule("domain layers stay independent from frameworks and outer layers")
        .match("squid.*.domain*")
        .should_not_import("sqlalchemy*")
        .should_not_import("discord*")
        .should_not_import("fastapi*")
        .should_not_import("nucleation*")
        .should_not_import("opentelemetry*")
        .should_not_import("squid.*.application*")
        .should_not_import("squid.*.infrastructure*")
        .check("squid", only_direct_imports=True)
    )


def test_application_layers_are_framework_and_infrastructure_independent() -> None:
    (
        archrule("application layers depend on ports rather than adapters")
        .match("squid.*.application*")
        .should_not_import("sqlalchemy*")
        .should_not_import("discord*")
        .should_not_import("fastapi*")
        .should_not_import("nucleation*")
        .should_not_import("opentelemetry*")
        .should_not_import("squid.*.infrastructure*")
        .check("squid", only_direct_imports=True)
    )


def test_transports_do_not_import_persistence_adapters() -> None:
    for transport in ("squid.bot*", "squid.api*"):
        (
            archrule("transports invoke application services")
            .match(transport)
            .should_not_import("sqlalchemy*")
            .should_not_import("nucleation*")
            .should_not_import("squid.persistence*")
            .should_not_import("squid.*.infrastructure*")
            .check("squid", only_direct_imports=True)
        )


def test_native_schematic_engine_stays_in_its_adapter() -> None:
    (
        archrule("only the schematic adapter may import the native engine")
        .match("squid*")
        .exclude("squid.schematics.infrastructure*")
        .should_not_import("nucleation*")
        .check("squid", only_direct_imports=True)
    )


def test_production_modules_do_not_import_fuzz_engines() -> None:
    """Keep generators, native instrumentation, and their runtime costs test-only."""
    fuzz_packages = ("atheris", "hypothesis", "schemathesis")
    violations: list[tuple[Path, int, str]] = []
    for path in _scanned_files():
        for node in ast.walk(source_tree(path)):
            if isinstance(node, ast.Import):
                violations.extend(
                    (path, node.lineno, alias.name) for alias in node.names if alias.name.startswith(fuzz_packages)
                )
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(fuzz_packages):
                violations.append((path, node.lineno, node.module))

    assert violations == []


# The adapter modules are allowed to name the engine; everything else must not, including via
# a string handed to importlib, which an import-graph rule would not see.
ENGINE_REFERENCE_ALLOWLIST = frozenset(
    {
        Path("squid/schematics/infrastructure/capability.py"),
        Path("squid/schematics/infrastructure/nucleation_adapter.py"),
        Path("squid/schematics/infrastructure/worker_main.py"),
    }
)


def test_no_module_outside_the_adapter_names_the_native_engine() -> None:
    violations: list[tuple[Path, int, str]] = []
    for path in _scanned_files():
        if path in ENGINE_REFERENCE_ALLOWLIST:
            continue
        for node in ast.walk(source_tree(path)):
            if isinstance(node, ast.Import):
                violations.extend(
                    (path, node.lineno, alias.name)
                    for alias in node.names
                    if alias.name == "nucleation" or alias.name.startswith("nucleation.")
                )
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("nucleation"):
                violations.append((path, node.lineno, node.module))
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and (node.value == "nucleation" or node.value.startswith("nucleation."))
            ):
                violations.append((path, node.lineno, f"string {node.value!r}"))

    assert violations == []


def test_application_modules_do_not_read_process_environment_directly() -> None:
    violations: list[tuple[Path, int, str]] = []
    for path in _scanned_files():
        if path == Path("squid/config.py"):
            continue
        for node in ast.walk(source_tree(path)):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
                if node.attr in {"environ", "getenv"}:
                    violations.append((path, node.lineno, f"os.{node.attr}"))
            elif (isinstance(node, ast.Import) and any(alias.name == "dotenv" for alias in node.names)) or (
                isinstance(node, ast.ImportFrom) and node.module == "dotenv"
            ):
                violations.append((path, node.lineno, "dotenv import"))

    assert violations == []


def test_logging_calls_keep_message_templates_stable() -> None:
    """Keep log messages lazily formatted so aggregators can group by template."""
    violations: list[tuple[Path, int]] = []
    log_methods = {"debug", "info", "warning", "error", "exception", "critical", "log"}
    for path in _scanned_files():
        for node in ast.walk(source_tree(path)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in log_methods:
                continue
            message_index = 1 if node.func.attr == "log" else 0
            if len(node.args) > message_index and any(
                isinstance(item, ast.JoinedStr) for item in ast.walk(node.args[message_index])
            ):
                violations.append((path, node.lineno))

    assert violations == []


def test_development_launcher_supervises_separate_process_entry_points() -> None:
    """Production entry points stay isolated even when launched together for development."""
    path = Path("app.py")
    source = path.read_text(encoding="utf-8")

    assert "multiprocessing" not in source
    assert '"squid.api.app"' in source
    assert '"squid.bot.app"' in source
    assert '"squid.worker.app"' in source
    assert "process.terminate()" in source
    assert "process.wait" in source


def test_the_api_layer_names_a_discord_id_only_where_it_reads_one_off_an_account() -> None:
    """No module under `squid/api/` may name `discord_id`, with one exception.

    `squid/api/v1/schemas/me.py` reads it off the authenticated account's identities,
    which is the correct pattern: it is a *response* field derived from stored identity,
    not an attribute of the caller. `subject_for` hardcodes `guild_id=None`, so an HTTP
    caller can never act on a Discord fact anyway -- an identifier here would have no
    legitimate use, and the one that existed produced an
    `assert caller.discord_id is not None` on a submission path that had an account id
    in hand.

    Comments are excluded, so prose explaining the absence does not trip the ratchet.
    """
    allowed = Path("squid/api/v1/schemas/me.py")
    offenders: list[str] = []
    for path in sorted(Path("squid/api").rglob("*.py")):
        if path == allowed:
            continue
        tree = source_tree(path)
        for node in ast.walk(tree):
            match node:
                case ast.Name(id="discord_id") | ast.Attribute(attr="discord_id"):
                    offenders.append(f"{path}:{node.lineno}")
                case ast.keyword(arg="discord_id") | ast.arg(arg="discord_id"):
                    offenders.append(f"{path}:{node.lineno}")
                case _:
                    pass

    assert offenders == [], f"squid/api/ must not name discord_id outside {allowed}: {offenders}"


BUILTIN_EXCEPTIONS = frozenset(
    {
        "ArithmeticError",
        "AttributeError",
        "BufferError",
        "EOFError",
        "EnvironmentError",
        "Exception",
        "FloatingPointError",
        "IOError",
        "IndexError",
        "KeyError",
        "LookupError",
        "MemoryError",
        "NameError",
        "OSError",
        "OverflowError",
        "RecursionError",
        "ReferenceError",
        "RuntimeError",
        "StopIteration",
        "SyntaxError",
        "SystemError",
        "TimeoutError",
        "TypeError",
        "UnboundLocalError",
        "UnicodeError",
        "ValueError",
        "ZeroDivisionError",
    }
)

# `AssertionError` and `NotImplementedError` stay legal: they mark a programming error or an
# unimplemented Protocol stub, neither of which is a failure a caller is meant to catch, present,
# or translate.
BARE_RAISE_ALLOWLIST = {}


def _raises_application_layer_paths() -> list[Path]:
    """Application and domain modules, including the packages that flatten a layer into one file."""
    return sorted(
        path
        for path in Path("squid").rglob("*.py")
        if "application" in path.parts or "domain" in path.parts or path.stem in {"application", "domain", "services"}
    )


def test_application_and_domain_layers_raise_only_structured_errors() -> None:
    """An exception's base class is its user-facing contract, so builtins are never right here.

    Both transports classify a failure purely by type. `build_error_presentation`
    (`squid/bot/errors.py`) renders friendly localized text for a `DomainError` and drops everything
    else into a generic "Something went wrong" card that also logs at error level and files an error
    report; `handle_squid_error` (`squid/api/errors.py`) does the equivalent for problem details. A
    bare `ValueError` is therefore reported to operators as a crash no matter how expected it was --
    which is exactly how an admin mistyping a record category came to look like a bug (`b03322f1d85e`).

    The `SquidError` vocabulary covers every case, so no site needs a builtin: caller-contract
    violations become `InvalidStateError` and broken persisted invariants become `DataIntegrityError`.
    Neither changes what a user sees, but both keep this rule total and checkable.

    `BARE_RAISE_ALLOWLIST` is a ratchet over the violations that predate the rule. It pins a count per
    file so a new raise in an already-listed module still fails. Shrink it; never grow it.
    """
    counts: dict[str, int] = {}
    locations: dict[str, list[int]] = {}
    for path in _raises_application_layer_paths():
        for node in ast.walk(source_tree(path)):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(raised, ast.Name) and raised.id in BUILTIN_EXCEPTIONS:
                key = path.as_posix()
                counts[key] = counts.get(key, 0) + 1
                locations.setdefault(key, []).append(node.lineno)

    new_offenders = sorted(key for key in counts if key not in BARE_RAISE_ALLOWLIST)
    assert new_offenders == [], (
        "raise a squid.core.errors class instead of a builtin exception in "
        f"{ {key: locations[key] for key in new_offenders} }"
    )

    grown = {
        key: (BARE_RAISE_ALLOWLIST[key], count) for key, count in counts.items() if count > BARE_RAISE_ALLOWLIST[key]
    }
    assert grown == {}, f"these files gained bare raises (allowed, found): {grown}"

    stale = {
        key: (allowed, counts.get(key, 0))
        for key, allowed in BARE_RAISE_ALLOWLIST.items()
        if counts.get(key, 0) < allowed
    }
    assert stale == {}, f"lower or drop these BARE_RAISE_ALLOWLIST entries (allowed, found): {stale}"


def test_the_engine_needs_no_transport_install() -> None:
    """Portable authoring, planning, runtime, scenes, and HTML need no Discord install.

    This used to skip a `discord/` directory inside the same distribution. Now that the
    adapter is its own package the rule is flat, and the blocked set widens to the packages
    that sit above the engine: nothing here may import them, in a function body or under
    TYPE_CHECKING, which is where a back-edge would hide from a plain dependency check.
    """
    root = Path("packages/squid-ui/src/squid_ui")
    blocked = {
        "anyio",
        "discord",
        "slack_sdk",
        "squid_storage",
        "squid_ui_discord",
        "squid_ui_slack",
        "squid_ui_widgets",
    }
    violations: list[str] = []
    for path in root.rglob("*.py"):
        for node in ast.walk(source_tree(path)):
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported = {node.module.split(".", 1)[0]}
            else:
                continue
            if found := imported & blocked:
                violations.append(f"{path}:{node.lineno}: {sorted(found)}")

    assert violations == []


def _irrefutable(pattern: ast.pattern) -> bool:
    return isinstance(pattern, ast.MatchAs) and (pattern.pattern is None or _irrefutable(pattern.pattern))


def test_planner_traversals_keep_their_exhaustiveness_proof() -> None:
    """A traversal's terminal catch-all must be assert_never or a raise, never a quiet default.

    The planner dispatches are proven exhaustive by `assert_never` final arms (ADR 0075);
    the proof dies silently if someone reintroduces `case _: return ...`. Scoped to the
    three traversal files on purpose -- `case _:` is legitimate everywhere else.
    """
    traversals = (
        COMPILER_PASS_ROOT / "html_planner.py",
        COMPILER_PASS_ROOT / "semantic_adaptation" / "lowering.py",
        COMPILER_PASS_ROOT / "semantic_adaptation" / "decisions.py",
    )
    violations: list[tuple[Path, int]] = []
    for path in traversals:
        for node in ast.walk(source_tree(path)):
            if not isinstance(node, ast.Match):
                continue
            last = node.cases[-1]
            if not _irrefutable(last.pattern):
                continue
            statement = last.body[0] if len(last.body) == 1 else None
            proves = isinstance(statement, ast.Raise) or (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Name)
                and statement.value.func.id == "assert_never"
            )
            if not proves:
                violations.append((path, node.lineno))

    assert violations == []
