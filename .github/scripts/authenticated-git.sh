#!/usr/bin/env bash
# Trusted control-plane helper for authenticated GitHub pushes.
# Source only from reviewed control infrastructure.

if [[ -z "${CUSTOM_GIT_TOKEN:-}" ]]; then
    echo "::error::Required Git credential is missing." >&2
    return 1
fi

# Preserve the value as a shell variable without exporting
# it to unrelated child processes.
export -n CUSTOM_GIT_TOKEN

authenticated_git() {
    if [[ "${GITHUB_REPOSITORY:-}" != T4T3Z0R/noctalia ]]; then
        echo "::error::Unexpected repository identity." >&2
        return 1
    fi

    if [[ "${GITHUB_REF:-}" != refs/heads/main ]]; then
        echo "::error::Unexpected control execution ref." >&2
        return 1
    fi

    if [[ $# -lt 3 || "$1" != push || "$2" != origin ]]; then
        echo "::error::Unsupported authenticated Git operation." >&2
        return 1
    fi

    if [[ -z "${CUSTOM_GIT_TOKEN:-}" ]]; then
        echo "::error::Required Git credential is missing." >&2
        return 1
    fi

    # Restrict each credential consumer to one exact ref
    # and an explicitly lease-protected operation.
    #
    # The role is a defensive workflow contract, not an
    # independently authenticated identity. Repository rules
    # remain the server-side authorization boundary.

    local target refspec lease expected revision

    case "${CUSTOM_GIT_ROLE:-}" in
        marker)
            target=refs/heads/automation/upstream-tablet-detected
            ;;
        promotion)
            target=refs/heads/custom
            ;;
        retention)
            if [[ ! "$3" =~ ^([0-9a-f]{40}):refs/heads/archive/custom-([0-9a-f]{40})$ ]]; then
                echo "::error::Invalid retention refspec." >&2
                return 1
            fi

            if [[ "${BASH_REMATCH[1]}" != "${BASH_REMATCH[2]}" ]]; then
                echo "::error::Retention identity mismatch." >&2
                return 1
            fi

            target="refs/heads/archive/custom-${BASH_REMATCH[1]}"
            ;;
        *)
            echo "::error::Unknown Git mutation role." >&2
            return 1
            ;;
    esac

    if [[ $# -ne 4 ]]; then
        echo "::error::Unexpected Git mutation arguments." >&2
        return 1
    fi

    refspec="$3"
    lease="$4"

    if [[ "$lease" != "--force-with-lease=$target:"* ]]; then
        echo "::error::Exact destination lease required." >&2
        return 1
    fi

    expected="${lease#--force-with-lease="$target":}"

    if [[ -n "$expected" && ! "$expected" =~ ^[0-9a-f]{40}$ ]]; then
        echo "::error::Invalid expected revision." >&2
        return 1
    fi

    if [[ "$refspec" == ":$target" ]]; then
        echo "::error::Authenticated ref deletion is prohibited." >&2
        return 1
    fi

    if [[ "$refspec" != *":$target" ]]; then
        echo "::error::Unauthorized mutation destination." >&2
        return 1
    fi

    revision="${refspec%:"$target"}"

    if [[ ! "$revision" =~ ^[0-9a-f]{40}$ ]]; then
        echo "::error::Exact source revision required." >&2
        return 1
    fi

    if [[ "$CUSTOM_GIT_ROLE" == retention && -n "$expected" ]]; then
        echo "::error::Retention must use a create-only lease." >&2
        return 1
    fi

    if [[ "$CUSTOM_GIT_ROLE" == promotion && -z "$expected" ]]; then
        echo "::error::Promotion requires an existing revision lease." >&2
        return 1
    fi

    local destination authorization

    destination="$(command git remote get-url --push --all origin)" \
        || return 1

    case "$destination" in
        https://github.com/T4T3Z0R/noctalia|\
        https://github.com/T4T3Z0R/noctalia.git)
            ;;
        *)
            echo "::error::Unexpected Git push destination." >&2
            return 1
            ;;
    esac

    authorization="$(
        printf 'x-access-token:%s' "$CUSTOM_GIT_TOKEN" | base64 -w0
    )" || return 1

    printf '::add-mask::%s\n' "$authorization"

    GIT_CONFIG_COUNT=5 \
        GIT_CONFIG_KEY_0=http.https://github.com/.extraheader \
        GIT_CONFIG_VALUE_0='' \
        GIT_CONFIG_KEY_1=http.https://github.com/.extraheader \
        GIT_CONFIG_VALUE_1="AUTHORIZATION: basic ${authorization}" \
        GIT_CONFIG_KEY_2=credential.helper \
        GIT_CONFIG_VALUE_2='' \
        GIT_CONFIG_KEY_3=core.hooksPath \
        GIT_CONFIG_VALUE_3=/dev/null \
        GIT_CONFIG_KEY_4=http.followRedirects \
        GIT_CONFIG_VALUE_4=false \
        GIT_TERMINAL_PROMPT=0 \
        command git "$@"
}
