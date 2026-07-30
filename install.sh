#!/bin/bash
#
# MacCleaner installer.
#
# Run once. Asks for your password once. After this, `mc` runs completely unattended.
#
#   ./install.sh              install the root helper, sudoers rule and keychain item
#   ./install.sh --schedule   also install the weekly LaunchAgent
#   ./install.sh --uninstall  remove everything this script installed
#   ./install.sh --no-askpass install without storing a password in the Keychain
#
# What gets installed and why:
#
#   /usr/local/libexec/maccleaner/mc-root      root-owned helper, fixed verb vocabulary
#   /usr/local/libexec/maccleaner/policy.py    the allowlist mc-root enforces
#   /usr/local/libexec/maccleaner/mc-askpass   reads the sudo password from the Keychain
#   /etc/sudoers.d/maccleaner                  NOPASSWD, for mc-root only
#
# The sudoers rule grants passwordless root to exactly one root-owned, non-user-writable
# program that refuses to do anything outside its allowlist. That is meaningfully
# narrower than `NOPASSWD: ALL`, which is what most "make sudo not ask me" advice does.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/usr/local/libexec/maccleaner"
SUDOERS_FILE="/etc/sudoers.d/maccleaner"
KEYCHAIN_SERVICE="maccleaner-sudo"
LAUNCH_AGENT_LABEL="com.prabucki.maccleaner"
LAUNCH_AGENT_PLIST="$HOME/Library/LaunchAgents/${LAUNCH_AGENT_LABEL}.plist"

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; BLUE=$'\033[0;34m'; DIM=$'\033[2m'; OFF=$'\033[0m'

info()  { printf '%s==>%s %s\n' "$BLUE" "$OFF" "$1"; }
ok()    { printf '%s  ok%s %s\n' "$GREEN" "$OFF" "$1"; }
warn()  { printf '%s  !!%s %s\n' "$YELLOW" "$OFF" "$1"; }
die()   { printf '%serror%s %s\n' "$RED" "$OFF" "$1" >&2; exit 1; }

SCHEDULE=0
UNINSTALL=0
WANT_ASKPASS=1

while [ $# -gt 0 ]; do
    case "$1" in
        --schedule)   SCHEDULE=1 ;;
        --uninstall)  UNINSTALL=1 ;;
        --no-askpass) WANT_ASKPASS=0 ;;
        -h|--help)    sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)            die "unknown option: $1" ;;
    esac
    shift
done

[ "$(uname -s)" = "Darwin" ] || die "MacCleaner is macOS-only"
[ "$(id -u)" -ne 0 ] || die "run this as your normal user, not with sudo (it will ask when it needs to)"

# ---------------------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------------------

if [ "$UNINSTALL" -eq 1 ]; then
    info "Removing MacCleaner privileged components"

    if [ -f "$LAUNCH_AGENT_PLIST" ]; then
        launchctl bootout "gui/$(id -u)/${LAUNCH_AGENT_LABEL}" 2>/dev/null || true
        rm -f "$LAUNCH_AGENT_PLIST"
        ok "removed the LaunchAgent"
    fi

    if security find-generic-password -s "$KEYCHAIN_SERVICE" >/dev/null 2>&1; then
        security delete-generic-password -s "$KEYCHAIN_SERVICE" >/dev/null
        ok "removed the Keychain credential"
    fi

    if [ -e "$SUDOERS_FILE" ] || [ -d "$INSTALL_DIR" ]; then
        sudo rm -f "$SUDOERS_FILE"
        sudo rm -rf "$INSTALL_DIR"
        ok "removed the sudoers rule and $INSTALL_DIR"
    fi

    printf '\n%sQuarantine at ~/.maccleaner was left alone.%s Remove it yourself if you want it gone.\n' "$DIM" "$OFF"
    exit 0
fi

# ---------------------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------------------

info "Checking prerequisites"

for required in "$REPO_DIR/mc/privileged/mc-root" "$REPO_DIR/mc/privileged/mc-askpass" "$REPO_DIR/mc/policy.py"; do
    [ -f "$required" ] || die "missing $required - is this the repository root?"
done
ok "repository looks complete"

/usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "/usr/bin/python3 is missing or too old; install the Xcode Command Line Tools"
ok "system python is usable ($(/usr/bin/python3 -V 2>&1))"

# The helper must not run under an interpreter an unprivileged user can replace.
python_owner="$(stat -f '%Su' /usr/bin/python3)"
[ "$python_owner" = "root" ] || die "/usr/bin/python3 is owned by $python_owner, not root - refusing to install"
ok "system python is root-owned"

# ---------------------------------------------------------------------------------------
# Install the helper
# ---------------------------------------------------------------------------------------

info "Installing the root helper to $INSTALL_DIR"
printf '%s    (this is the one time you will be asked for your password)%s\n' "$DIM" "$OFF"

sudo /bin/mkdir -p "$INSTALL_DIR"
sudo /usr/sbin/chown root:wheel "$INSTALL_DIR"
sudo /bin/chmod 755 "$INSTALL_DIR"

sudo /usr/bin/install -o root -g wheel -m 0755 "$REPO_DIR/mc/privileged/mc-root"    "$INSTALL_DIR/mc-root"
sudo /usr/bin/install -o root -g wheel -m 0644 "$REPO_DIR/mc/policy.py"             "$INSTALL_DIR/policy.py"
sudo /usr/bin/install -o root -g wheel -m 0755 "$REPO_DIR/mc/privileged/mc-askpass" "$INSTALL_DIR/mc-askpass"
ok "installed mc-root, policy.py and mc-askpass (root:wheel, not user-writable)"

# A stable path for the Full Disk Access grant, so the grant survives repo moves.
sudo /bin/rm -f "$INSTALL_DIR/mc-run"
sudo /bin/ln -s "$REPO_DIR/bin/mc" "$INSTALL_DIR/mc-run" 2>/dev/null || true

# ---------------------------------------------------------------------------------------
# sudoers
# ---------------------------------------------------------------------------------------

info "Granting passwordless sudo for the helper only"

sudoers_tmp="$(mktemp -t maccleaner-sudoers)"
trap 'rm -f "$sudoers_tmp"' EXIT

cat > "$sudoers_tmp" <<EOF
# Installed by MacCleaner's install.sh. Remove with: ./install.sh --uninstall
#
# Grants $USER passwordless root for exactly one program: the MacCleaner root helper.
# mc-root is owned by root:wheel mode 0755, refuses to run if that ever stops being
# true, and only accepts a fixed set of verbs whose path arguments it re-validates
# against its own copy of the allowlist in $INSTALL_DIR/policy.py.
$USER ALL=(root) NOPASSWD: $INSTALL_DIR/mc-root
EOF

# Validate before installing. A malformed sudoers file locks you out of sudo entirely.
sudo /usr/sbin/visudo -cqf "$sudoers_tmp" || die "generated sudoers file failed validation - nothing was installed"
ok "sudoers fragment passed visudo validation"

sudo /usr/bin/install -o root -g wheel -m 0440 "$sudoers_tmp" "$SUDOERS_FILE"
ok "installed $SUDOERS_FILE"

if sudo -n "$INSTALL_DIR/mc-root" self-check >/dev/null 2>&1; then
    ok "passwordless escalation verified"
else
    die "helper installed but passwordless sudo is not working - check $SUDOERS_FILE"
fi

# ---------------------------------------------------------------------------------------
# Keychain credential
# ---------------------------------------------------------------------------------------

if [ "$WANT_ASKPASS" -eq 1 ]; then
    info "Storing a sudo credential in your login Keychain"
    cat <<EOF
${DIM}    Needed only for third-party tools that call sudo themselves and cannot be routed
    through mc-root - mainly 'brew upgrade --cask' for casks that ship a pkg installer.
    The item is readable only while your login Keychain is unlocked, and its ACL is
    restricted to mc-askpass. Skip this with --no-askpass; those steps will then be
    skipped rather than hang.${OFF}
EOF

    if security find-generic-password -s "$KEYCHAIN_SERVICE" >/dev/null 2>&1; then
        ok "credential already present (delete with: security delete-generic-password -s $KEYCHAIN_SERVICE)"
    else
        printf '    Password for %s: ' "$USER"
        stty -echo; read -r sudo_password; stty echo; printf '\n'

        if ! printf '%s\n' "$sudo_password" | sudo -S -k true 2>/dev/null; then
            unset sudo_password
            warn "that password was not accepted; skipping the Keychain item"
        else
            security add-generic-password \
                -s "$KEYCHAIN_SERVICE" \
                -a "$USER" \
                -w "$sudo_password" \
                -T "$INSTALL_DIR/mc-askpass" \
                -U \
                -j "Used by MacCleaner for tools that invoke sudo internally"
            unset sudo_password
            ok "stored, with access restricted to mc-askpass"
        fi
    fi
fi

# ---------------------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------------------

info "Setting up the Python environment"

if [ ! -d "$REPO_DIR/.venv" ]; then
    if command -v uv >/dev/null 2>&1; then
        (cd "$REPO_DIR" && uv venv --python 3.13 >/dev/null)
    else
        /usr/bin/python3 -m venv "$REPO_DIR/.venv"
    fi
fi

if command -v uv >/dev/null 2>&1; then
    (cd "$REPO_DIR" && uv pip install --quiet rich attrs inquirer toml beartype xattr)
else
    "$REPO_DIR/.venv/bin/pip" install --quiet rich attrs inquirer toml beartype xattr
fi
ok "dependencies installed into .venv"

mkdir -p "$HOME/.local/bin"
ln -sf "$REPO_DIR/bin/mc" "$HOME/.local/bin/mc"
ok "linked mc into ~/.local/bin"

case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) warn "$HOME/.local/bin is not on your PATH; add it to your shell profile to use 'mc' directly" ;;
esac

# ---------------------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------------------

if [ "$SCHEDULE" -eq 1 ]; then
    info "Installing the weekly LaunchAgent"

    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.maccleaner/logs"
    sed -e "s|@@MC@@|$REPO_DIR/bin/mc|g" -e "s|@@HOME@@|$HOME|g" \
        "$REPO_DIR/launchd/${LAUNCH_AGENT_LABEL}.plist" > "$LAUNCH_AGENT_PLIST"

    plutil -lint "$LAUNCH_AGENT_PLIST" >/dev/null || die "generated LaunchAgent plist is malformed"

    launchctl bootout "gui/$(id -u)/${LAUNCH_AGENT_LABEL}" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$LAUNCH_AGENT_PLIST"
    ok "scheduled for Sundays at 03:00 (runs only on AC power)"
    printf '%s    Run it now with: launchctl kickstart -k gui/%s/%s%s\n' "$DIM" "$(id -u)" "$LAUNCH_AGENT_LABEL" "$OFF"
fi

# ---------------------------------------------------------------------------------------
# Full Disk Access
# ---------------------------------------------------------------------------------------

printf '\n'
info "One manual step remains: Full Disk Access"

cat <<EOF
    Without it, anything under ~/Library/Containers, Mail and Safari is invisible -
    those modules will run and report zero rather than fail, which is worse than an
    error. macOS does not allow this to be granted from a script.

    System Settings > Privacy & Security > Full Disk Access, then add:

        ${INSTALL_DIR}/mc-run

    ${DIM}(Press Cmd+Shift+G in the file picker and paste that path.)
    If you run mc from a terminal, grant it to your terminal app as well.${OFF}
EOF

printf '\n%sInstalled.%s Next:\n\n' "$GREEN" "$OFF"
printf '    mc --doctor            check everything is wired up\n'
printf '    mc --dry-run           see what would be cleaned, delete nothing\n'
printf '    mc --profile standard  a conservative first real run\n'
printf '    mc                     the default aggressive run\n\n'
