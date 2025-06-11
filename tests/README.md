## Running Tests

To run the tests, you can use `pytest`:

```bash
pytest
```

This automatically uses the configuration from `pyproject.toml` (`[tools.pytest.ini_options]`) and runs all tests in the `tests` directory.

### Integration Tests

To run integration tests, you have to set up the environment variables as described in the `README.md` file. Make sure you have the necessary credentials and configurations in place.

Also, you need to add a supabase submodule in order to get the newest docker image for the integration tests:

As a useful helper, you can run `scripts/add-tests-supabase-submodule.sh` to add the submodule.

```bash
# Linux or macOS
bash scripts/add-tests-supabase-submodule.sh
```

If you are on Windows, you can run the script in WSL or manually run the commands in the script one by one.