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
    {{python_dir}}/pip-compile --output-file=requirements.txt requirements.in
    {{python_dir}}/pip-compile --constraint=requirements.txt --output-file=test-requirements.txt test-requirements.in

sync:
    {{python_dir}}/pip-sync --python-executable {{python}} requirements.txt test-requirements.txt

lint:
    {{python}} -m ruff check --fix --exit-zero
    {{python}} -m ruff format --target-version py312
