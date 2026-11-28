#!/usr/bin/env bash
# Execute candidate source in an isolated disposable container.
# The caller must supply an independently verified bare repository.

set -euo pipefail
umask 077

die() {
    printf '%s\n' "$*" >&2
    exit 1
}

[[ $# == 2 ]] || die \
    "Expected receiver repository and candidate SHA."

repository="$1"
candidate="$2"

[[ "$repository" == /* && -d "$repository" ]] \
    || die "Invalid receiver repository."

[[ "$candidate" =~ ^[0-9a-f]{40}$ ]] \
    || die "Invalid candidate identity."

for variable in \
    CUSTOM_BRANCH_TOKEN \
    CUSTOM_GIT_TOKEN \
    CUSTOM_TOKEN \
    GH_TOKEN \
    GITHUB_TOKEN \
    NOCTALIA_CANDIDATE_APP_PRIVATE_KEY
do
    [[ -z "${!variable:-}" ]] \
        || die "Unexpected validation credential."
done

test "$(git -C "$repository" \
    rev-parse --is-bare-repository)" = true

test "$(git -C "$repository" \
    rev-parse --verify 'refs/heads/remediation-transfer^{commit}')" \
    = "$candidate"

control="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

scratch="$(mktemp -d)"
suffix="${scratch##*/}"

image="noctalia-validation:${suffix,,}"
container="noctalia-validation-${suffix}"
volume="noctalia-validation-${suffix}"

cleanup() {
    local original_status=$?
    local cleanup_failed=0
    local inventory

    trap - EXIT

    if inventory="$(docker container ls -aq \
        --filter "name=^/${container}$")"
    then
        if [[ -n "$inventory" ]]; then
            docker rm -f "$container" >/dev/null \
                || cleanup_failed=1
        fi
    else
        cleanup_failed=1
    fi

    if inventory="$(docker volume ls -q \
        --filter "name=^${volume}$")"
    then
        if [[ -n "$inventory" ]]; then
            docker volume rm "$volume" >/dev/null \
                || cleanup_failed=1
        fi
    else
        cleanup_failed=1
    fi

    if inventory="$(docker image ls -q \
        --filter "reference=$image")"
    then
        if [[ -n "$inventory" ]]; then
            docker image rm "$image" >/dev/null \
                || cleanup_failed=1
        fi
    else
        cleanup_failed=1
    fi

    rm -rf -- "$scratch" || cleanup_failed=1

    if (( cleanup_failed != 0 )); then
        echo "Validation resource cleanup failed." >&2

        if (( original_status == 0 )); then
            exit 1
        fi
    fi

    exit "$original_status"
}

trap cleanup EXIT

mkdir -m 700 "$scratch/context"

cp -- "$control/candidate-validation.Dockerfile" \
    "$scratch/context/Dockerfile"

# Only trusted image construction instructions enter
# the build context. No candidate source is present.
docker build \
    --pull \
    --tag "$image" \
    "$scratch/context"

# Export the verified Git objects without checking out
# candidate files on the Actions runner.
git -C "$repository" \
    -c core.hooksPath=/dev/null \
    -c credential.helper= \
    bundle create "$scratch/source.bundle" \
    refs/heads/remediation-transfer

chmod 0444 "$scratch/source.bundle"

test "$(stat -c %a "$scratch")" = 700
test "$(stat -c %a "$scratch/source.bundle")" = 444

docker volume create "$volume" >/dev/null

# Initialize the disposable volume before executing
# any candidate-controlled source.
docker run --rm \
    --name "$container" \
    --network none \
    --user 0:0 \
    --security-opt no-new-privileges=true \
    --mount "type=volume,source=$volume,target=/source" \
    "$image" -c '
        set -euo pipefail
        chown 20000:20000 /source
        chmod 700 /source
    '

# Import and execute as an unprivileged user.
# The only host file mounted is a read-only Git bundle.
# No runner directories or Docker socket are mounted.
status=0

docker run \
    --name "$container" \
    --network none \
    --cap-drop ALL \
    --security-opt no-new-privileges=true \
    --user 20000:20000 \
    --pids-limit 1024 \
    --memory 6g \
    --memory-swap 6g \
    --cpus 2 \
    --log-driver none \
    --init \
    --mount "type=volume,source=$volume,target=/source" \
    --mount "type=bind,source=$scratch/source.bundle,target=/input.bundle,readonly" \
    "$image" -c '
        set -euo pipefail

        candidate="$1"

        [[ "$candidate" =~ ^[0-9a-f]{40}$ ]]

        test "$(id -u)" = 20000
        test "$(id -g)" = 20000

        test ! -S /var/run/docker.sock
        test -r /input.bundle
        test ! -w /input.bundle

        for variable in \
            GITHUB_TOKEN \
            GH_TOKEN \
            GITHUB_ENV \
            GITHUB_OUTPUT \
            GITHUB_PATH \
            GITHUB_STEP_SUMMARY \
            ACTIONS_RUNTIME_TOKEN \
            CUSTOM_GIT_TOKEN
        do
            test -z "${!variable:-}"
        done

        export GIT_CONFIG_NOSYSTEM=1
        export GIT_CONFIG_GLOBAL=/dev/null
        export GIT_TERMINAL_PROMPT=0

        git init --template= --initial-branch=workspace

        git -c core.hooksPath=/dev/null \
            -c fetch.fsckObjects=true \
            fetch --no-tags /input.bundle \
            refs/heads/remediation-transfer

        test "$(git rev-parse --verify "FETCH_HEAD^{commit}")" \
            = "$candidate"

        git -c core.hooksPath=/dev/null \
            -c credential.helper= \
            -c submodule.recurse=false \
            checkout --detach "$candidate"

        test "$(git rev-parse HEAD)" = "$candidate"

        list="$(mktemp)"
        trap "rm -f -- \"$list\"" EXIT

        find src tests \
            \( -name "*.cpp" -o -name "*.h" \) \
            -type f -print0 > "$list"

        mapfile -d "" files < "$list"

        (( ${#files[@]} > 0 ))

        /usr/lib/llvm22/bin/clang-format \
            --dry-run -Werror "${files[@]}"

        just build
        just test debug --print-errorlogs
        just lint
    ' validation "$candidate" \
    2>&1 | python3 -I -B -c '
import sys

limit = 16 * 1024 * 1024
retained = 0
discarded = 0

while True:
    chunk = sys.stdin.buffer.read(65536)

    if not chunk:
        break

    remaining = max(0, limit - retained)
    accepted = chunk[:remaining]

    sys.stdout.buffer.write(accepted)

    retained += len(accepted)
    discarded += len(chunk) - len(accepted)

if discarded:
    sys.stdout.buffer.write(
        ("\\n[Validation log truncated; "
         f"{discarded} bytes discarded.]\\n").encode()
    )
' > "$scratch/validation.log" || status=$?

# Candidate output is untrusted. Disable Actions command
# interpretation while replaying the captured log.
resume="$(cat /proc/sys/kernel/random/uuid)"

printf '::stop-commands::%s\n' "$resume"

cat -- "$scratch/validation.log"

printf '\n::%s::\n' "$resume"

if (( status != 0 )); then
    printf 'Candidate validation failed: status %s\n' \
        "$status" >&2

    exit "$status"
fi

printf 'PASS: Isolated validation completed for %s\n' \
    "$candidate"
