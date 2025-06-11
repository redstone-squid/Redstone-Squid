#!/usr/bin/env bash
# Run this script on the root of the repository to add the tests submodule for Supabase.
# Based on https://stackoverflow.com/questions/52780680/git-submodule-prepare-for-sparse-checkout/53233492#53233492

set -e
path="tests/supabase"
supabase_repo="https://github.com/supabase/supabase"
git clone --filter=blob:none --no-checkout "$supabase_repo" "$path"
git submodule add "$supabase_repo" "$path"
git submodule absorbgitdirs
git -C "$path" config core.sparseCheckout true
echo 'docker/*' >> .git/modules/$path/info/sparse-checkout
git submodule update --force --checkout "$path"