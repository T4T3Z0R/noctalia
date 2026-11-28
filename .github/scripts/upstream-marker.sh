#!/usr/bin/env bash
# Represent an upstream detection event without publishing
# upstream source files or workflow definitions.

set -euo pipefail

die() {
    printf '%s\n' "$*" >&2
    exit 1
}

[[ $# -ge 2 ]] || die "Missing marker arguments."

mode="$1"
upstream="$2"

[[ "$upstream" =~ ^[0-9a-f]{40}$ ]] \
    || die "Invalid upstream revision."

safe_git() {
    command git \
        -c core.hooksPath=/dev/null \
        -c credential.helper= \
        -c commit.gpgSign=false \
        -c core.fsmonitor=false \
        "$@"
}

message="noctalia-upstream-tablet-v2:v1:$upstream"

case "$mode" in
    create)
        [[ $# == 2 ]] || die "Unexpected marker arguments."

        tree="$(safe_git mktree < /dev/null)"

        marker="$(
            printf '%s\n' "$message" |
                safe_git \
                    -c user.name='github-actions[bot]' \
                    -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
                    commit-tree "$tree"
        )"

        [[ "$marker" =~ ^[0-9a-f]{40}$ ]]

        printf '%s\n' "$marker"
        ;;

    matches)
        [[ $# == 3 ]] || die "Missing marker revision."

        marker="$3"

        [[ "$marker" =~ ^[0-9a-f]{40}$ ]] \
            || die "Invalid marker revision."

        test "$(safe_git cat-file -t "$marker")" = commit

        # Recognize the previous source-commit marker format.
        if [[ "$marker" == "$upstream" ]]; then
            printf 'true\n'
            exit 0
        fi

        entries="$(safe_git ls-tree -r "$marker")"
        ancestry="$(safe_git rev-list --parents -n 1 "$marker")"
        observed="$(safe_git show -s --format=%B "$marker")"

        if [[ -z "$entries" &&
              "$ancestry" == "$marker" &&
              "$observed" == "$message" ]]
        then
            printf 'true\n'
        else
            printf 'false\n'
        fi
        ;;

    *)
        die "Unsupported marker operation."
        ;;
esac
