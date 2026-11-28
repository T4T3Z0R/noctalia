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
        GIT_CONFIG_VALUE_0= \
        GIT_CONFIG_KEY_1=http.https://github.com/.extraheader \
        GIT_CONFIG_VALUE_1="AUTHORIZATION: basic ${authorization}" \
        GIT_CONFIG_KEY_2=credential.helper \
        GIT_CONFIG_VALUE_2= \
        GIT_CONFIG_KEY_3=core.hooksPath \
        GIT_CONFIG_VALUE_3=/dev/null \
        GIT_CONFIG_KEY_4=http.followRedirects \
        GIT_CONFIG_VALUE_4=false \
        GIT_TERMINAL_PROMPT=0 \
        command git "$@"
}
