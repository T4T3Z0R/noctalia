#!/usr/bin/env bash
# Reconstruct an exact candidate in a fresh bare repository.
# Never execute or materialize candidate source.

set -euo pipefail
umask 077

die() {
    printf '%s\n' "$*" >&2
    exit 1
}

[[ $# == 6 ]] || die \
    "Expected destination, bundle, candidate, base, tag and checksum."

destination="$1"
bundle="$2"
candidate="$3"
base="$4"
tag="$5"
checksum="$6"

[[ "$destination" == /* ]] \
    || die "Destination must be absolute."

[[ "$bundle" == /* ]] \
    || die "Bundle path must be absolute."

[[ "$candidate" =~ ^[0-9a-f]{40}$ ]] \
    || die "Invalid candidate identity."

[[ "$base" =~ ^[0-9a-f]{40}$ ]] \
    || die "Invalid upstream identity."

[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || die "Invalid upstream release tag."

[[ "$checksum" =~ ^[0-9a-f]{64}$ ]] \
    || die "Invalid bundle checksum."

[[ ! -e "$destination" && ! -L "$destination" ]] \
    || die "Receiver destination already exists."

[[ -f "$bundle" && ! -L "$bundle" ]] \
    || die "Invalid bundle file."

for variable in \
    CUSTOM_BRANCH_TOKEN \
    CUSTOM_GIT_TOKEN \
    CUSTOM_TOKEN \
    GH_TOKEN \
    GITHUB_TOKEN \
    NOCTALIA_CANDIDATE_APP_PRIVATE_KEY
do
    [[ -z "${!variable:-}" ]] \
        || die "Unexpected receiver credential."
done

size="$(stat -c %s -- "$bundle")"

(( size > 0 && size <= 268435456 )) \
    || die "Bundle exceeds permitted size."

digest="$(sha256sum < "$bundle")"

[[ "${digest%% *}" == "$checksum" ]] \
    || die "Bundle checksum mismatch."

control="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

unset GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS
unset GIT_CONFIG_SYSTEM GIT_CONFIG_GLOBAL
unset GIT_TEMPLATE_DIR GIT_EXEC_PATH
unset GIT_EXTERNAL_DIFF GIT_SSH GIT_SSH_COMMAND
unset GIT_ASKPASS SSH_ASKPASS
unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE
unset GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES

export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_TERMINAL_PROMPT=0

safe_git() {
    command git \
        -c core.hooksPath=/dev/null \
        -c credential.helper= \
        -c core.fsmonitor=false \
        -c fetch.fsckObjects=true \
        -c transfer.fsckObjects=true \
        -c submodule.recurse=false \
        -c http.followRedirects=false \
        "$@"
}

mkdir -m 700 -- "$destination"

complete=false

cleanup() {
    if [[ "$complete" != true ]]; then
        rm -rf -- "$destination"
    fi
}

trap cleanup EXIT

mkdir -m 700 \
    "$destination/home" \
    "$destination/repository"

export HOME="$destination/home"

cd "$destination/repository"

safe_git init --bare --template= >&2

safe_git fetch --no-tags --no-recurse-submodules \
    https://github.com/noctalia-dev/noctalia.git \
    "refs/tags/$tag" >&2

actual_base="$(safe_git rev-parse --verify 'FETCH_HEAD^{commit}')"

[[ "$actual_base" == "$base" ]] \
    || die "Upstream release identity mismatch."

actual_candidate="$(
    bash "$control/candidate-bundle.sh" import \
        "$bundle" "$candidate" "$base" "$checksum"
)"

[[ "$actual_candidate" == "$candidate" ]] \
    || die "Received candidate identity mismatch."

test "$(safe_git rev-parse --is-bare-repository)" = true

safe_git merge-base --is-ancestor \
    "$base" "$actual_candidate"

complete=true

printf '%s\n' "$actual_candidate"
