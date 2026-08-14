#!/bin/bash
# WireGuard setup script for GPCG multi-worker architecture.
#
# This script sets up a WireGuard tunnel between the VPS and a worker (VM or PC).
# It generates keys, creates config files, and starts the tunnel.
#
# Usage:
#   On VPS (server):   sudo ./setup-wireguard.sh server
#   On Worker (client): sudo ./setup-wireguard.sh client <vps-public-ip>
#
# After setup, the VPS is reachable at 10.10.0.1 and workers get IPs
# in the 10.10.0.x range. The GPCG worker connects to the VPS via
# this tunnel, avoiding exposing the API to the public internet.
#
# Prerequisites:
#   - wireguard-tools installed (apt install wireguard-tools)
#   - Root access
#   - UDP port 51820 open on the VPS firewall

set -euo pipefail

WG_INTERFACE="wg0"
WG_PORT="51820"
WG_SERVER_IP="10.10.0.1/24"
WG_CLIENT_IP="10.10.0.2/24"
WG_DIR="/etc/wireguard"

# ─── Helper functions ───────────────────────────────────────────────────

check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo "❌ Please run as root (use sudo)"
        exit 1
    fi
}

check_wireguard_installed() {
    if ! command -v wg &> /dev/null; then
        echo "❌ wireguard-tools not installed. Install with:"
        echo "   apt install wireguard-tools"
        exit 1
    fi
}

generate_keys() {
    local key_dir="$1"
    mkdir -p "$key_dir"

    if [ ! -f "$key_dir/privatekey" ]; then
        echo "🔑 Generating WireGuard keys in $key_dir..."
        wg genkey > "$key_dir/privatekey"
        wg pubkey < "$key_dir/privatekey" > "$key_dir/publickey"
        chmod 600 "$key_dir/privatekey"
        chmod 644 "$key_dir/publickey"
    else
        echo "✅ Keys already exist in $key_dir"
    fi
}

# ─── Server setup ───────────────────────────────────────────────────────

setup_server() {
    check_root
    check_wireguard_installed

    echo "🖥️  Setting up WireGuard SERVER on VPS..."

    generate_keys "$WG_DIR"
    local PRIVATE_KEY
    PRIVATE_KEY=$(cat "$WG_DIR/privatekey")
    local PUBLIC_KEY
    PUBLIC_KEY=$(cat "$WG_DIR/publickey")

    # Create server config
    cat > "$WG_DIR/$WG_INTERFACE.conf" << EOF
[Interface]
PrivateKey = $PRIVATE_KEY
Address = $WG_SERVER_IP
ListenPort = $WG_PORT
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

# Worker peers will be added here by setup-worker.sh
# Example:
# [Peer]
# PublicKey = <worker-public-key>
# AllowedIPs = 10.10.0.2/32
EOF

    chmod 600 "$WG_DIR/$WG_INTERFACE.conf"

    # Enable IP forwarding
    echo "net.ipv4.ip_forward = 1" > /etc/sysctl.d/99-wireguard.conf
    sysctl -p /etc/sysctl.d/99-wireguard.conf

    # Start and enable
    systemctl enable wg-quick@$WG_INTERFACE
    systemctl start wg-quick@$WG_INTERFACE

    echo ""
    echo "✅ WireGuard server is running!"
    echo ""
    echo "📋 Server public key (share with workers):"
    echo "   $PUBLIC_KEY"
    echo ""
    echo "📋 Server WireGuard IP: 10.10.0.1"
    echo "📋 Workers should connect to: http://10.10.0.1:8787/gpcg"
    echo ""
    echo "Next: Run setup-worker.sh on each worker machine"
}

# ─── Client (worker) setup ──────────────────────────────────────────────

setup_client() {
    check_root
    check_wireguard_installed

    local VPS_IP="${1:-}"
    if [ -z "$VPS_IP" ]; then
        echo "❌ Usage: $0 client <vps-public-ip>"
        exit 1
    fi

    echo "💻 Setting up WireGuard CLIENT (worker)..."

    generate_keys "$WG_DIR"
    local PRIVATE_KEY
    PRIVATE_KEY=$(cat "$WG_DIR/privatekey")
    local PUBLIC_KEY
    PUBLIC_KEY=$(cat "$WG_DIR/publickey")

    # Create client config
    cat > "$WG_DIR/$WG_INTERFACE.conf" << EOF
[Interface]
PrivateKey = $PRIVATE_KEY
Address = $WG_CLIENT_IP

[Peer]
PublicKey = <VPS_PUBLIC_KEY>
Endpoint = $VPS_IP:$WG_PORT
AllowedIPs = 10.10.0.0/24
PersistentKeepalive = 25
EOF

    chmod 600 "$WG_DIR/$WG_INTERFACE.conf"

    echo ""
    echo "✅ WireGuard client config created!"
    echo ""
    echo "📋 Worker public key (add to VPS server config):"
    echo "   $PUBLIC_KEY"
    echo ""
    echo "📋 Next steps:"
    echo "   1. On the VPS, add this worker as a peer:"
    echo "      wg set $WG_INTERFACE peer $PUBLIC_KEY allowed-ips 10.10.0.2/32"
    echo ""
    echo "   2. Replace <VPS_PUBLIC_KEY> in $WG_DIR/$WG_INTERFACE.conf"
    echo "      with the VPS server's public key"
    echo ""
    echo "   3. Start the tunnel:"
    echo "      systemctl enable wg-quick@$WG_INTERFACE"
    echo "      systemctl start wg-quick@$WG_INTERFACE"
    echo ""
    echo "   4. Test connectivity:"
    echo "      ping 10.10.0.1"
    echo ""
    echo "   5. Set GPCG_VPS_URL to the WireGuard IP:"
    echo "      export GPCG_VPS_URL=http://10.10.0.1:8787/gpcg"
}

# ─── Main ───────────────────────────────────────────────────────────────

case "${1:-}" in
    server)
        setup_server
        ;;
    client)
        setup_client "${2:-}"
        ;;
    *)
        echo "Usage: $0 {server|client <vps-public-ip>}"
        echo ""
        echo "  server              Set up WireGuard server on VPS"
        echo "  client <vps-ip>     Set up WireGuard client on worker"
        exit 1
        ;;
esac
