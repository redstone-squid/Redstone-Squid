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
deploy: stop _pull sync
    #!{{shebang}}
    nohup {{python}} app.py "&"

# Needed to order the tasks correctly
_pull:
    git pull

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
    uv export --locked --output-file requirements/base.txt
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
fuzz-version:
    mkdir -p .fuzz/corpus/version_parser .fuzz/artifacts/version_parser
    cp tests/fuzz/corpus/version_parser/* .fuzz/corpus/version_parser/
    uv run --group fuzz python -m tests.fuzz.fuzz_version_parser .fuzz/corpus/version_parser -max_total_time=600 -max_len=4096 -artifact_prefix=.fuzz/artifacts/version_parser/

[unix]
fuzz-cursor:
    mkdir -p .fuzz/corpus/cursor_codec .fuzz/artifacts/cursor_codec
    cp tests/fuzz/corpus/cursor_codec/* .fuzz/corpus/cursor_codec/
    uv run --group fuzz python -m tests.fuzz.fuzz_cursor_codec .fuzz/corpus/cursor_codec -max_total_time=600 -max_len=4096 -artifact_prefix=.fuzz/artifacts/cursor_codec/

[unix]
fuzz-search-parser:
    mkdir -p .fuzz/corpus/search_parser .fuzz/artifacts/search_parser
    cp tests/fuzz/corpus/search_parser/* .fuzz/corpus/search_parser/
    uv run --group fuzz python -m tests.fuzz.fuzz_search_parser .fuzz/corpus/search_parser -max_total_time=600 -max_len=4096 -artifact_prefix=.fuzz/artifacts/search_parser/

backdate start_commit:
    git backdate --no-business-hours {{start_commit}}..
