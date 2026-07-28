set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# Cross platform shebang:
shebang := if os() == 'windows' {
  'powershell.exe'
} else {
  '/bin/sh'
}

python_dir := if os_family() == "windows" { "./.venv/Scripts" } else { "./.venv/bin" }
python := python_dir + if os_family() == "windows" { "/python.exe" } else { "/python3" }
system_python := if os_family() == "windows" { "py.exe -3.12" } else { "python3.12" }

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

init: && sync
    if test ! -e .venv; then {{system_python}} -m venv .venv; fi
    {{python}} -m pip install --upgrade pip pip-tools

compile:
    {{python_dir}}/pip-compile --output-file=requirements/base.txt pyproject.toml
    {{python_dir}}/pip-compile --constraint=requirements/base.txt --output-file=requirements/dev.txt requirements/dev.in

sync:
    {{python_dir}}/pip-sync --python-executable {{python}} requirements/base.txt requirements/dev.txt

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

backdate start_commit:
    git backdate --no-business-hours {{start_commit}}..
