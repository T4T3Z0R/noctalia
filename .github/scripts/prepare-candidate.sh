#!/usr/bin/env bash
# Prepare an exact source candidate without remote write credentials.
# The caller must supply a disposable repository containing all inputs.

set -euo pipefail

if [[ $# != 3 ]]; then
    echo "Expected custom, current-base and latest-base SHAs." >&2
    exit 1
fi

custom_sha="$1"
current_base_sha="$2"
latest_sha="$3"

for revision in "$custom_sha" "$current_base_sha" "$latest_sha"; do
    if [[ ! "$revision" =~ ^[0-9a-f]{40}$ ]]; then
        echo "Invalid source commit identity." >&2
        exit 1
    fi
done

# Reject credentials accidentally supplied by a caller.
for variable in \
    CUSTOM_BRANCH_TOKEN \
    CUSTOM_GIT_TOKEN \
    CUSTOM_TOKEN \
    GH_TOKEN \
    GITHUB_TOKEN
do
    if [[ -n "${!variable:-}" ]]; then
        echo "Unexpected credential in preparation environment." >&2
        exit 1
    fi
done

# Remove inherited Git configuration injection.
unset GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS
unset GIT_CONFIG_SYSTEM GIT_CONFIG_GLOBAL
unset GIT_TEMPLATE_DIR GIT_EXEC_PATH
unset GIT_EXTERNAL_DIFF GIT_SSH GIT_SSH_COMMAND
unset GIT_ASKPASS SSH_ASKPASS

export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_TERMINAL_PROMPT=0
export GIT_EDITOR=true
export GIT_SEQUENCE_EDITOR=true

safe_git() {
    command git \
        -c core.hooksPath=/dev/null \
        -c credential.helper= \
        -c commit.gpgSign=false \
        -c rebase.autoStash=false \
        -c rebase.updateRefs=false \
        -c rerere.enabled=false \
        -c submodule.recurse=false \
        -c core.fsmonitor=false \
        -c user.name="github-actions[bot]" \
        -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
        "$@"
}

test "$(safe_git rev-parse --is-inside-work-tree)" = true

test -z "$(safe_git status --porcelain --untracked-files=all)"

for revision in "$custom_sha" "$current_base_sha" "$latest_sha"; do
    test "$(safe_git cat-file -t "$revision")" = commit
done

safe_git merge-base --is-ancestor "$current_base_sha" "$custom_sha"

# All source inputs are exact commit identities.
# No candidate scripts or hooks are intentionally executed.
safe_git checkout --detach "$custom_sha" >&2

if ! safe_git rebase \
    --no-autostash \
    --no-update-refs \
    --onto "$latest_sha" \
    "$current_base_sha" >&2
then
    safe_git rebase --abort >&2 || true
    echo "Candidate rebase failed." >&2
    exit 1
fi

candidate_sha="$(safe_git rev-parse --verify HEAD^{commit})"

[[ "$candidate_sha" =~ ^[0-9a-f]{40}$ ]]

safe_git merge-base --is-ancestor "$latest_sha" "$candidate_sha"

test -z "$(safe_git status --porcelain --untracked-files=all)"

# Standard output is reserved for the candidate identity.
printf '%s\n' "$candidate_sha"
