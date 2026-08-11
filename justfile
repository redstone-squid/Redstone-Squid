set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# Cross platform shebang:
shebang := if os() == 'windows' {
  'powershell.exe'
} else {
  '/bin/sh'
}

python_dir := if os_family() == "windows" { "./.venv/Scripts" } else { "./.venv/bin" }
python := python_dir + if os_family() == "windows" { "/python.exe" } else { "/python3" }

default:
  just --list

run: stop
    #!{{shebang}}
    {{python}} app.py

[unix]
stop:
    #!{{shebang}}
    pkill -f app.py || true

[windows]
stop:
    #!{{shebang}}
    $process = Get-CimInstance win32_process -Filter "CommandLine like '%app.py%'"
    if ($process) { Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue }

init: sync

alias s := sync
sync:
    uv sync --locked

alias up := upgrade
upgrade *packages:
    uv sync {{ prepend("--upgrade-package ", packages) }}

compile:
    uv export --locked --no-dev --output-file requirements/base.txt
    uv export --locked --only-dev --output-file requirements/dev.txt

lint:
    {{python}} -m ruff check --extend-select I --fix --exit-zero
    {{python}} -m ruff format --target-version py312

typecheck:
    uv run --locked pyrefly check --config pyproject.toml

build:
    docker build --build-arg GIT_COMMIT_HASH=$(git rev-parse HEAD) --build-arg GIT_COMMIT_MESSAGE="$(git log -1 --pretty=%s)" -t rssquid .

docker-run: build
    docker run --env-file .env --rm -p 8000:8000 rssquid

generate-schema:
    pg_dump -h aws-0-us-west-1.pooler.supabase.com -U postgres.jnushtruzgnnmmxabsxi -d postgres -f schema_dump.sql --encoding=UTF8 --schema-only --no-owner --no-privileges

# Export a CycloneDX SBOM from the locked dependency graph.
dependency-report output="dependency-report.json":
    uv export --locked --format cyclonedx1.5 --output-file "{{output}}"

# Refresh the API contract consumed by the public catalogue's generated client.
export-openapi:
    uv run --locked python -m scripts.export_openapi

gha-analysis:
    uvx zizmor --gh-token $(gh auth token) --persona=pedantic .

visualize-dependencies output="docs/dependencies.svg":
    uv tool run pipdeptree --python {{python}} --graph-output svg > "{{output}}"

# Using https://github.com/seveibar/pgstrap, which dumps the schema per table for better readability, but this requires npm
# Does not work on Windows: https://github.com/seveibar/pgstrap/issues/8
[unix]
generate-schema-alt:
    npm run db:generate

db-upgrade:
    uv run alembic upgrade head

db-current:
    uv run alembic current

db-check:
    uv run alembic check

db-revision name:
    uv run alembic revision --autogenerate -m "{{name}}"

# Use only when adopting a database that already matches the frozen baseline.
db-stamp-baseline:
    uv run alembic stamp 20260728_baseline

test:
    uv run pytest tests/unit tests/architecture

test-integration:
    uv run pytest tests/integration

test-all:
    uv run pytest tests/unit tests/architecture tests/integration

[unix]
fuzz-target *settings:
    uv run --locked --group fuzz python -m scripts.run_fuzz_target {{settings}}

[unix]
fuzz-version seconds="20":
    uv run --locked --group fuzz python -m scripts.run_fuzz_target target=version_parser seconds={{seconds}}

[unix]
fuzz-cursor seconds="20":
    uv run --locked --group fuzz python -m scripts.run_fuzz_target target=cursor_codec seconds={{seconds}}

[unix]
fuzz-search-parser seconds="20":
    uv run --locked --group fuzz python -m scripts.run_fuzz_target target=search_parser seconds={{seconds}}

backdate start_commit:
    git backdate --no-business-hours {{start_commit}}..

i18n-extract:
    uv run pybabel extract -F babel.cfg -o locales/squid.pot --sort-output --project=redstone-squid --version=$(uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])") --msgid-bugs-address=https://github.com/redstone-squid/Redstone-Squid/issues .

i18n-update: i18n-extract
    uv run pybabel update -i locales/squid.pot -d locales -D squid --no-fuzzy-matching

i18n-init locale: i18n-extract
    uv run pybabel init -i locales/squid.pot -d locales -D squid -l {{locale}}

i18n-compile:
    uv run pybabel compile -d locales -D squid --statistics
