#!/usr/bin/env bash
# Transfer candidate Git objects without transferring a working tree.
# Import requires a fresh, trusted bare repository with upstream history.

set -euo pipefail

die() {
    printf '%s\n' "$*" >&2
    exit 1
}

[[ $# == 5 ]] || die \
    "Expected mode, bundle, candidate SHA, base SHA and checksum."

mode="$1"
bundle="$2"
candidate="$3"
base="$4"
checksum="$5"

[[ "$candidate" =~ ^[0-9a-f]{40}$ ]] \
    || die "Invalid candidate identity."

[[ "$base" =~ ^[0-9a-f]{40}$ ]] \
    || die "Invalid base identity."

[[ "$bundle" == /* ]] \
    || die "Bundle path must be absolute."

for variable in \
    CUSTOM_BRANCH_TOKEN \
    CUSTOM_GIT_TOKEN \
    CUSTOM_TOKEN \
    GH_TOKEN \
    GITHUB_TOKEN
do
    [[ -z "${!variable:-}" ]] \
        || die "Unexpected transfer credential."
done

unset GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS
unset GIT_CONFIG_SYSTEM GIT_CONFIG_GLOBAL
unset GIT_TEMPLATE_DIR GIT_EXEC_PATH
unset GIT_EXTERNAL_DIFF GIT_SSH GIT_SSH_COMMAND
unset GIT_ASKPASS SSH_ASKPASS

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
        "$@"
}

transfer_ref="refs/heads/remediation-transfer"

# Initial conservative limit. Revisit using real source measurements.
maximum_size=268435456

validate_bundle_file() {
    [[ -f "$bundle" && ! -L "$bundle" ]] \
        || die "Invalid bundle file."

    local size
    size="$(stat -c %s -- "$bundle")"

    (( size > 0 && size <= maximum_size )) \
        || die "Bundle exceeds permitted size."
}

verify_reference() {
    local heads
    heads="$(safe_git bundle list-heads "$bundle")"

    [[ "$heads" == "$candidate $transfer_ref" ]] \
        || die "Unexpected bundle reference or candidate identity."
}

case "$mode" in
    export)
        [[ "$checksum" == "-" ]] \
            || die "Export does not accept an input checksum."

        [[ ! -e "$bundle" && ! -L "$bundle" ]] \
            || die "Bundle destination already exists."

        test "$(safe_git cat-file -t "$candidate")" = commit
        test "$(safe_git cat-file -t "$base")" = commit

        safe_git merge-base --is-ancestor "$base" "$candidate"

        if safe_git show-ref --verify --quiet "$transfer_ref"
        then
            die "Transfer reference already exists."
        fi

        umask 077

        safe_git update-ref "$transfer_ref" \
            "$candidate" \
            0000000000000000000000000000000000000000

        trap 'safe_git update-ref -d "$transfer_ref" "$candidate"' EXIT

        # Full bundle if candidate equals base; otherwise
        # include candidate history not already in the base.
        if [[ "$candidate" == "$base" ]]
        then
            safe_git bundle create "$bundle" "$transfer_ref" >&2
        else
            safe_git bundle create "$bundle" \
                "$transfer_ref" "^$base" >&2
        fi

        validate_bundle_file
        verify_reference

        safe_git bundle verify "$bundle" >&2

        safe_git update-ref -d "$transfer_ref" "$candidate"
        trap - EXIT

        digest="$(sha256sum < "$bundle")"

        printf '%s\n' "${digest%% *}"
        ;;

    import)
        [[ "$checksum" =~ ^[0-9a-f]{64}$ ]] \
            || die "Invalid bundle checksum."

        test "$(safe_git rev-parse --is-bare-repository)" = true

        validate_bundle_file

        digest="$(sha256sum < "$bundle")"

        [[ "${digest%% *}" == "$checksum" ]] \
            || die "Bundle checksum mismatch."

        test "$(safe_git cat-file -t "$base")" = commit

        if safe_git show-ref --verify --quiet "$transfer_ref"
        then
            die "Transfer reference already exists."
        fi

        verify_reference

        safe_git bundle verify "$bundle" >&2

        safe_git fetch --no-tags --no-recurse-submodules \
            "$bundle" "$transfer_ref:$transfer_ref" >&2

        trap 'safe_git update-ref -d "$transfer_ref"' EXIT

        actual="$(safe_git rev-parse --verify "$transfer_ref^{commit}")"

        [[ "$actual" == "$candidate" ]] \
            || die "Imported candidate identity mismatch."

        test "$(safe_git cat-file -t "$actual")" = commit

        safe_git merge-base --is-ancestor "$base" "$actual"

        # Confirm the retained artifact did not change
        # during import.
        digest="$(sha256sum < "$bundle")"

        [[ "${digest%% *}" == "$checksum" ]] \
            || die "Bundle changed during import."

        trap - EXIT

        printf '%s\n' "$actual"
        ;;

    *)
        die "Unsupported candidate transfer operation."
        ;;
esac
