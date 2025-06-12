# What each file does

`pre-push` is a git hook that runs before pushing to a remote repository. It checks if the code is formatted correctly. If not, it will prevent the push from happening and reformats the code automatically. To use it, copy the `pre-push` file to the `.git/hooks` directory. Make sure the file is executable by running `chmod +x .git/hooks/pre-push`.

`deploy.sh` as the name suggests is a script that deploys the project. It is not meant for external use as it assumes some commands and files are present in the project. A docker image may or may not be provided in the future.

`add-tests-supabase-submodule.sh` is a script that adds [Supabase](https://github.com/supabase/supabase) as submodule to  `tests/`. We use sparse checkout to get the docker compose files, which is used to spin up a Supabase instance in containers to run integration tests against.

The scripts in `migrations/` are used to provide additional information accompanying the migrations. For example, to backfill data when a new column is added to a table. These scripts are meant to be run manually before or after the migration is applied (whether before or after depends on the migration itself, see the top docstring of the script). These scripts are only guaranteed to work at the commit they are created in. They may not work in future commits.