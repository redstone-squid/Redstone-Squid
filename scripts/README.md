# What each file does

`pre-push` is a git hook that runs before pushing to a remote repository. It checks if the code is formatted correctly. If not, it will prevent the push from happening and reformats the code automatically. To use it, copy the `pre-push` file to the `.git/hooks` directory. Make sure the file is executable by running `chmod +x .git/hooks/pre-push`.

`deploy.sh` as the name suggests is a script that deploys the project. It is not meant for external use as it assumes some commands and files are present in the project. A docker image may or may not be provided in the future.