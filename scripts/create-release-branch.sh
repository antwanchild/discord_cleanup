#!/usr/bin/env bash
set -euo pipefail

version="${1:-}"

if [ -z "$version" ]; then
  if [ ! -f VERSION ]; then
    echo "VERSION file not found."
    exit 1
  fi
  version="$(cat VERSION)"
fi

branch="release/${version}"

git fetch origin main --prune
git switch -c "$branch" origin/main
git push -u origin "$branch"

echo "Created and pushed ${branch}"
