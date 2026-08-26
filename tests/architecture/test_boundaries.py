import ast
import sys
from pathlib import Path

from babel.messages.pofile import read_po
from pytest_archon import archrule

from squid.core.extract import deferred_msgid

# Roots for the AST scans that state repo-wide invariants. The squid-ui workspace member
# is held to the same rules as squid itself.
SCAN_ROOTS = (
    Path("squid"),
    Path("packages/squid-reactivity/src"),
    Path("packages/squid-ui/src"),
    Path("packages/squid-ui-discord/src"),
    Path("packages/squid-ui-widgets/src"),
    Path("packages/squid-replication/src"),
)

COMPILER_PASS_ROOT = Path("packages/squid-ui/src/squid_ui/planning")


def _scanned_files() -> list[Path]:
    return [path for root in SCAN_ROOTS for path in root.rglob("*.py")]


def test_compiler_pass_packages_are_not_facades() -> None:
    """Pass packages identify owners; their initializers must not aggregate those owners."""
    for package in ("layout_measurement", "semantic_adaptation"):
        path = COMPILER_PASS_ROOT / package / "__init__.py"
        body = ast.parse(path.read_text(encoding="utf-8")).body
        assert len(body) == 1
        assert isinstance(body[0], ast.Expr)
        assert isinstance(body[0].value, ast.Constant)
        assert isinstance(body[0].value.value, str)


def test_removed_compiler_pass_modules_have_no_compatibility_surface() -> None:
    """The former monolith paths stay deleted rather than becoming forwarding shims."""
    removed = {
        "squid_ui.planning.adaptation",
        "squid_ui.planning.measurement",
    }
    assert [name for name in removed if (COMPILER_PASS_ROOT / f"{name.rpartition('.')[2]}.py").exists()] == []

    violations: list[tuple[Path, int, str]] = []
    for path in _scanned_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported = (alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported = (node.module,)
            else:
                continue
            violations.extend((path, node.lineno, module) for module in imported if module in removed)

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


def test_reactive_package_has_no_hard_dependencies() -> None:
    """The extracted runtime may import only itself and Python's standard library."""
    allowed = sys.stdlib_module_names | {"squid_reactivity"}
    violations: list[tuple[Path, int, str]] = []
    for path in Path("packages/squid-reactivity/src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
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
    """Presentation is `squid.bot`'s business; no other layer may reach for a UI package."""
    for package in ("squid_ui*", "squid_ui_discord*", "squid_ui_widgets*"):
        (
            archrule(f"{package} is a Discord presentation concern")
            .match("squid*")
            .exclude("squid.bot*")
            .should_not_import(package)
            .check("squid", only_direct_imports=True)
        )


def test_layouts_package_carries_no_translation_markers() -> None:
    """Babel only extracts from squid/**, so a `_(...)` literal in the package would silently
    drop out of the catalogue. All user-facing text must enter through Chrome, pre-translated."""
    violations = [
        f"{path}:{node.lineno}"
        for path in Path("packages/squid-ui/src").rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_"
    ]

    assert violations == []


def test_deferred_messages_are_present_in_the_catalog_template() -> None:
    with Path("locales/squid.pot").open(encoding="utf-8") as fileobj:
        catalog = read_po(fileobj)
    msgids = {message.id for message in catalog if isinstance(message.id, str)}
    deferred = {
        msgid
        for path in Path("squid").rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and (msgid := deferred_msgid(node)) is not None
    }

    assert deferred - msgids == set()


def test_static_layout_rendering_stays_behind_the_host_wrapper() -> None:
    violations: list[str] = []
    for path in Path("squid").rglob("*.py"):
        if path == Path("squid/bot/ui.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
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
            # paths: squid_ui_discord.composition defines compose(), so a call resolves
            # to "squid_ui_discord.compose" regardless of which file compose() lives in.
            if resolved in {"squid_ui_discord.compose", "squid_ui_discord.render_static"}:
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
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
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
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
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
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
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
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
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


def test_voting_adapter_does_not_construct_database_service_locator() -> None:
    database_manager_calls = [
        (path, node.lineno)
        for path in Path("squid/bot/voting").glob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "DatabaseManager"
    ]

    assert database_manager_calls == []


def test_build_entity_does_not_expose_active_record_persistence_methods() -> None:
    source = Path("squid/builds/domain/models.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    build_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Build")
    method_names = {node.name for node in build_class.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}

    assert method_names.isdisjoint(
        {
            "from_id",
            "from_message_id",
            "save",
            "confirm",
            "deny",
            "ai_generate_from_message",
            "generate_embedding",
        }
    )
    assert all(not isinstance(node, ast.ClassDef) or node.name != "BuildLock" for node in tree.body)
    assert all(
        not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "DatabaseManager")
        for node in ast.walk(tree)
    )


def test_build_manager_does_not_construct_database_service_locator() -> None:
    source = Path("squid/builds/infrastructure/repository.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert all(
        not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "DatabaseManager")
        for node in ast.walk(tree)
    )


def test_build_repository_does_not_coordinate_leases() -> None:
    source = Path("squid/builds/infrastructure/repository.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    repository = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BuildRepository")
    method_names = {node.name for node in repository.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}

    assert method_names.isdisjoint({"acquire_lock", "release_lock", "locked", "clean_stale_locks"})
    assert "BuildLockRepository" not in source


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
        tree = ast.parse(path.read_text(encoding="utf-8"))
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
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
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
    blocked = {"anyio", "discord", "squid_storage", "squid_ui_discord", "squid_ui_widgets"}
    violations: list[str] = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported = {node.module.split(".", 1)[0]}
            else:
                continue
            if found := imported & blocked:
                violations.append(f"{path}:{node.lineno}: {sorted(found)}")

    assert violations == []
