#!/usr/bin/env sh
set -eu

VERSION="0.4.0-alpha.1-dev"
TARGET="codex"
CLEAN_LEGACY=0
CHECK_ONLY=0
BUNDLE_PATH=""
PROFILE_ROOT="${HOME}/.paperspine5/profiles/default"
CODEX_SKILLS_ROOT="${CODEX_SKILLS_ROOT:-${HOME}/.codex/skills}"
CLAUDE_SKILLS_ROOT="${CLAUDE_SKILLS_ROOT:-${HOME}/.claude/skills}"

usage() {
  cat <<'EOF'
Usage: ./install.sh [--target codex|claude-code|both] [--clean-legacy]
                    [--check-only] [--bundle PATH] [--profile-root PATH]

Downloads and verifies the self-contained PaperSpine5 suite for this POSIX host,
archives an existing canonical paper-spine Skill, activates the profile, and
runs first-start health. Unknown folders and paper task data are never deleted.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --target) TARGET=${2:?missing --target value}; shift 2 ;;
    --clean-legacy) CLEAN_LEGACY=1; shift ;;
    --check-only) CHECK_ONLY=1; shift ;;
    --bundle) BUNDLE_PATH=${2:?missing --bundle value}; shift 2 ;;
    --profile-root) PROFILE_ROOT=${2:?missing --profile-root value}; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$TARGET" in codex|claude-code|both) ;; *) echo "Invalid target: $TARGET" >&2; exit 2 ;; esac

machine=$(uname -m)
os=$(uname -s)
case "$os/$machine" in
  Linux/x86_64|Linux/amd64)
    PLATFORM="linux-x86_64"
    FILE="paperspine5-suite-${VERSION}-linux-x86_64.zip"
    BYTES="56169896"
    SHA256="d348bc8fe541edcfc95e105519770ba3e56e7243bc3f705771462bc11a23fb95"
    BUILD_ID="w7p-linux-x86_64-b3bcb4dcf094"
    ;;
  Darwin/arm64|Darwin/aarch64)
    PLATFORM="macos-arm64"
    FILE="paperspine5-suite-${VERSION}-macos-arm64.zip"
    BYTES="40524093"
    SHA256="9aedfd5d95b7210e4cb174f69773a5e9d3c3d55528189c6b67cbdece064c660c"
    BUILD_ID="w7p-macos-arm64-5512484f90b8"
    ;;
  Darwin/x86_64|Darwin/amd64)
    PLATFORM="macos-x86_64"
    FILE="paperspine5-suite-${VERSION}-macos-x86_64.zip"
    BYTES="40498601"
    SHA256="a03fdd843aa7761728c6aa525e61a0379644d5335add5261723ee1f0f9c6dff7"
    BUILD_ID="w7p-macos-x86_64-e6712a779f9c"
    ;;
  *) echo "Unsupported PaperSpine5 suite platform: $os/$machine" >&2; exit 2 ;;
esac

state="$PROFILE_ROOT/.paperspine5-lifecycle/profile-state.json"
current=""
if [ -f "$state" ]; then
  current=$(sed -n 's/.*"active_build_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$state" | head -n 1)
fi
if [ "$CHECK_ONLY" -eq 1 ]; then
  if [ -z "$current" ]; then status="not_installed"; elif [ "$current" = "$BUILD_ID" ]; then status="up_to_date"; else status="update_available"; fi
  printf '{"status":"%s","platform":"%s","current_build_id":"%s","available_build_id":"%s","version":"%s"}\n' "$status" "$PLATFORM" "$current" "$BUILD_ID" "$VERSION"
  exit 0
fi

for command in curl unzip; do
  command -v "$command" >/dev/null 2>&1 || { echo "Required command is missing: $command" >&2; exit 2; }
done
work=$(mktemp -d "${TMPDIR:-/tmp}/paperspine5-v5.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM
zip="$work/$FILE"
if [ -n "$BUNDLE_PATH" ]; then cp "$BUNDLE_PATH" "$zip"; else curl --fail --location --retry 3 "https://github.com/WUBING2023/PaperSpine/releases/download/v${VERSION}/${FILE}" --output "$zip"; fi
actual_bytes=$(wc -c < "$zip" | tr -d '[:space:]')
if [ "$actual_bytes" != "$BYTES" ]; then echo "Byte-size mismatch for $FILE" >&2; exit 1; fi
if command -v sha256sum >/dev/null 2>&1; then actual_sha=$(sha256sum "$zip" | awk '{print $1}'); else actual_sha=$(shasum -a 256 "$zip" | awk '{print $1}'); fi
if [ "$actual_sha" != "$SHA256" ]; then echo "SHA-256 mismatch for $FILE" >&2; exit 1; fi
extract="$work/suite"
mkdir -p "$extract"
unzip -q "$zip" -d "$extract"
chmod +x "$extract/paperspine" "$extract/release/stable-update" "$extract/runtime_vendor/python/bin/python3"
"$extract/paperspine" verify-bundle --bundle "$zip" >/dev/null

stamp=$(date -u +%Y%m%d-%H%M%S)
operation="v5-${PLATFORM}-${stamp}"
backup_root="$HOME/.paperspine5/backups/v5-install/$stamp"
archive_root="$HOME/.paperspine5/legacy-migrations"
if [ "$TARGET" = "both" ]; then targets="codex claude-code"; else targets="$TARGET"; fi
if [ "$CLEAN_LEGACY" -eq 1 ]; then
  set -- "$extract/runtime_vendor/python/bin/python3" -I -B "$extract/standalone/paper-spine/scripts/skill_discovery_migration.py" migrate --archive-root "$archive_root" --operation-id "$operation"
  for target in $targets; do
    if [ "$target" = "codex" ]; then root="$CODEX_SKILLS_ROOT"; else root="$CLAUDE_SKILLS_ROOT"; fi
    set -- "$@" --skills-root "$target=$root"
  done
  "$@" >/dev/null
fi
for target in $targets; do
  if [ "$target" = "codex" ]; then root="$CODEX_SKILLS_ROOT"; else root="$CLAUDE_SKILLS_ROOT"; fi
  destination="$root/paper-spine"
  mkdir -p "$root"
  if [ -e "$destination" ]; then
    mkdir -p "$backup_root/$target"
    mv "$destination" "$backup_root/$target/paper-spine"
  fi
  cp -R "$extract/standalone/paper-spine" "$destination"
  printf 'Installed V5 paper-spine Skill for %s: %s\n' "$target" "$destination"
done
mkdir -p "$PROFILE_ROOT"
if [ -f "$state" ]; then
  "$extract/paperspine" update --profile-root "$PROFILE_ROOT" --bundle "$zip" --operation-id "$operation" --confirm
else
  "$extract/paperspine" install --profile-root "$PROFILE_ROOT" --bundle "$zip" --operation-id "$operation"
fi
"$extract/paperspine" first-start --profile-root "$PROFILE_ROOT"
printf 'PaperSpine5 %s installed for %s. Restart the host before invoking paper-spine.\n' "$VERSION" "$PLATFORM"
printf 'Profile: %s\nBackup root: %s\n' "$PROFILE_ROOT" "$backup_root"
