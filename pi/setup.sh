#!/usr/bin/env bash
#
# setup.sh — provision one Raspberry Pi for the forest-fire network.
#
# Run once per Pi. Six Pis, six invocations:
#
#     sudo ./setup.sh gateway
#     sudo ./setup.sh node 1
#     sudo ./setup.sh node 2      ... through node 5
#
# What it does:
#   - installs the Python packages that role needs
#   - downloads Waveshare's sx126x.py driver and puts it where the code expects
#   - enables the serial port (and SPI, on sensor nodes)
#   - writes the systemd units and enables them
#
# It is safe to re-run: every step checks before acting.
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PI_DIR="$REPO_DIR/pi"
DRIVER_URL="https://files.waveshare.com/upload/1/18/SX126X_LoRa_HAT_CODE.zip"

# The user the services run as — the invoking user, not root, since we expect
# sudo. Falls back to 'pi' when SUDO_USER is unset (e.g. a root login shell).
RUN_USER="${SUDO_USER:-pi}"

ROLE="${1:-}"
NODE_ID="${2:-}"

# ------------------------------------------------------------------ helpers --

c_info()  { printf '\033[0;36m[setup]\033[0m %s\n' "$*"; }
c_ok()    { printf '\033[0;32m[ ok  ]\033[0m %s\n' "$*"; }
c_warn()  { printf '\033[0;33m[warn ]\033[0m %s\n' "$*"; }
c_die()   { printf '\033[0;31m[fail ]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage:
    sudo ./setup.sh gateway
    sudo ./setup.sh node <NODE_ID>      # NODE_ID is 1..254

Examples:
    sudo ./setup.sh gateway
    sudo ./setup.sh node 3
EOF
    exit 1
}

# ------------------------------------------------------------ validate args --

[[ -n "$ROLE" ]] || usage

case "$ROLE" in
    gateway)
        [[ -z "$NODE_ID" ]] || c_die "gateway takes no node id (it is always LoRa addr 0)"
        ;;
    node)
        [[ -n "$NODE_ID" ]] || c_die "node role needs an id: sudo ./setup.sh node 3"
        [[ "$NODE_ID" =~ ^[0-9]+$ ]] || c_die "node id must be a number, got '$NODE_ID'"
        # 0 is the gateway; the protocol reserves 255.
        (( NODE_ID >= 1 && NODE_ID <= 254 )) || c_die "node id must be 1..254, got $NODE_ID"
        ;;
    *)
        usage
        ;;
esac

[[ $EUID -eq 0 ]] || c_die "run with sudo — this edits /boot config and /etc/systemd"

c_info "role=$ROLE${NODE_ID:+ node_id=$NODE_ID} user=$RUN_USER"
c_info "repo=$REPO_DIR"

# ------------------------------------------------------------- apt packages --

c_info "installing system packages"
apt-get update -qq
apt-get install -y -qq python3-pip unzip wget >/dev/null
if [[ "$ROLE" == "node" ]]; then
    # libgpiod2 is what adafruit-circuitpython-dht links against.
    apt-get install -y -qq libgpiod2 >/dev/null || \
        c_warn "libgpiod2 unavailable — DHT22 may not import; DHT_MODE=simulate still works"
fi
c_ok "system packages"

# ---------------------------------------------------------- python packages --

# --break-system-packages is required on Bookworm and later (PEP 668).
PIP_FLAGS="--break-system-packages --quiet"
# Older pip does not know the flag; drop it rather than fail.
if ! pip3 install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
    PIP_FLAGS="--quiet"
fi

c_info "installing python packages"
if [[ "$ROLE" == "gateway" ]]; then
    # shellcheck disable=SC2086
    pip3 install $PIP_FLAGS -r "$PI_DIR/gateway/requirements.txt"
else
    # shellcheck disable=SC2086
    pip3 install $PIP_FLAGS -r "$PI_DIR/node/requirements.txt"
fi
c_ok "python packages"

# --------------------------------------------------------- waveshare driver --

# Not vendored into this repo on purpose: it is Waveshare's code, and pulling
# from source means picking up any fixes they publish.
install_driver() {
    local dest="$1"
    if [[ -f "$dest/sx126x.py" ]]; then
        c_ok "driver already present in $(basename "$dest")/"
        return
    fi

    local tmp
    tmp="$(mktemp -d)"
    c_info "downloading Waveshare SX126x driver"
    if ! wget -q -O "$tmp/hat.zip" "$DRIVER_URL"; then
        rm -rf "$tmp"
        c_die "driver download failed — check connectivity, or fetch $DRIVER_URL by hand"
    fi

    unzip -q -o "$tmp/hat.zip" -d "$tmp"

    local found
    found="$(find "$tmp" -name 'sx126x.py' -print -quit)"
    [[ -n "$found" ]] || { rm -rf "$tmp"; c_die "sx126x.py not found inside the archive"; }

    install -o "$RUN_USER" -g "$RUN_USER" -m 644 "$found" "$dest/sx126x.py"
    rm -rf "$tmp"
    c_ok "driver installed to $(basename "$dest")/sx126x.py"
}

if [[ "$ROLE" == "gateway" ]]; then
    install_driver "$PI_DIR/gateway"
else
    install_driver "$PI_DIR/node"
fi

# -------------------------------------------------------------- interfaces --

# Bookworm moved config.txt; support both locations.
BOOT_CONFIG=/boot/firmware/config.txt
[[ -f "$BOOT_CONFIG" ]] || BOOT_CONFIG=/boot/config.txt

enable_flag() {
    local flag="$1" label="$2"
    if grep -qE "^${flag}$" "$BOOT_CONFIG"; then
        c_ok "$label already enabled"
    else
        echo "$flag" >> "$BOOT_CONFIG"
        c_ok "$label enabled (reboot required)"
        REBOOT_NEEDED=1
    fi
}

REBOOT_NEEDED=0

c_info "configuring interfaces in $BOOT_CONFIG"
enable_flag "enable_uart=1" "UART"

# The serial console fights the LoRa HAT for /dev/ttyS0 — it must go.
if systemctl is-enabled --quiet serial-getty@ttyS0.service 2>/dev/null; then
    systemctl disable --now serial-getty@ttyS0.service >/dev/null 2>&1 || true
    c_ok "serial login console disabled"
else
    c_ok "serial login console already off"
fi

# Remove console=serial0 from the kernel cmdline, same reason.
CMDLINE=/boot/firmware/cmdline.txt
[[ -f "$CMDLINE" ]] || CMDLINE=/boot/cmdline.txt
if [[ -f "$CMDLINE" ]] && grep -q "console=serial0" "$CMDLINE"; then
    sed -i 's/console=serial0,[0-9]* //' "$CMDLINE"
    c_ok "removed serial console from kernel cmdline"
    REBOOT_NEEDED=1
fi

if [[ "$ROLE" == "node" ]]; then
    enable_flag "dtparam=spi=on" "SPI (for the MCP3008)"
fi

# Serial access without sudo.
if id -nG "$RUN_USER" | grep -qw dialout; then
    c_ok "$RUN_USER already in dialout"
else
    usermod -aG dialout "$RUN_USER"
    c_ok "$RUN_USER added to dialout (needs logout/login)"
fi

# --------------------------------------------------------------- systemd ----

install_unit() {
    local name="$1"
    local src="$PI_DIR/systemd/$name"
    [[ -f "$src" ]] || c_die "missing unit file $src"

    # Templated at install time so the units carry no hardcoded paths or user.
    sed -e "s|@REPO_DIR@|$REPO_DIR|g" \
        -e "s|@RUN_USER@|$RUN_USER|g" \
        -e "s|@NODE_ID@|${NODE_ID:-1}|g" \
        "$src" > "/etc/systemd/system/$name"
    c_ok "installed $name"
}

c_info "installing systemd units"
if [[ "$ROLE" == "gateway" ]]; then
    install_unit fire-gateway.service
    install_unit fire-api.service
    systemctl daemon-reload
    systemctl enable --now fire-gateway.service fire-api.service
    c_ok "fire-gateway and fire-api enabled"
else
    install_unit fire-node.service
    systemctl daemon-reload
    systemctl enable --now fire-node.service
    c_ok "fire-node enabled with NODE_ID=$NODE_ID"
fi

# ----------------------------------------------------------------- summary --

echo
c_ok "provisioning complete for role '$ROLE'"
echo
if [[ "$ROLE" == "gateway" ]]; then
    cat <<EOF
Check it:
    systemctl status fire-gateway fire-api
    journalctl -u fire-gateway -f
    curl http://localhost:5000/api/health

Dashboard:
    http://\$(hostname -I | awk '{print \$1}'):5000
EOF
else
    cat <<EOF
Check it:
    systemctl status fire-node
    journalctl -u fire-node -f

This node starts in simulate mode. Once sensors are wired, edit
    /etc/systemd/system/fire-node.service
and set SMOKE_MODE=adc and DHT_MODE=dht22, then:
    sudo systemctl daemon-reload && sudo systemctl restart fire-node
EOF
fi

if (( REBOOT_NEEDED )); then
    echo
    c_warn "reboot required for the serial/SPI changes to take effect: sudo reboot"
fi
