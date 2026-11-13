#!/usr/bin/env bash
set -euo pipefail

# Other generators can push while formatting runs, without starting another workflow.
for attempt in 1 2 3; do
  git fetch origin "refs/heads/${BRANCH}"
  git rebase FETCH_HEAD
  if git push origin "HEAD:refs/heads/${BRANCH}"; then
    exit 0
  fi
  echo "Automatic fixes could not be pushed (attempt ${attempt}/3)."
done

echo "Unable to push automatic fixes after three attempts." >&2
exit 1
