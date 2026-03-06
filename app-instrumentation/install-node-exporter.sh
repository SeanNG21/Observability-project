#!/bin/bash
# ============================================================
# install-node-exporter.sh
# Install Node Exporter as systemd service on Ubuntu/Debian
# Run on EACH server you want to monitor
# ============================================================

set -e

NODE_EXPORTER_VERSION="${NODE_EXPORTER_VERSION:-1.7.0}"
NODE_EXPORTER_USER="node_exporter"
INSTALL_DIR="/usr/local/bin"
SYSTEMD_DIR="/etc/systemd/system"

echo "======================================================"
echo "Installing Node Exporter v${NODE_EXPORTER_VERSION}"
echo "======================================================"

# Create user
if ! id -u $NODE_EXPORTER_USER &>/dev/null; then
    useradd --no-create-home --shell /bin/false $NODE_EXPORTER_USER
    echo "✓ Created user: $NODE_EXPORTER_USER"
fi

# Download
cd /tmp
ARCH=$(uname -m)
case $ARCH in
    x86_64)  ARCH="amd64" ;;
    aarch64) ARCH="arm64" ;;
    armv7l)  ARCH="armv7" ;;
esac

FILENAME="node_exporter-${NODE_EXPORTER_VERSION}.linux-${ARCH}"
URL="https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/${FILENAME}.tar.gz"

echo "Downloading from: $URL"
wget -q "$URL" -O "${FILENAME}.tar.gz"
tar xzf "${FILENAME}.tar.gz"
cp "${FILENAME}/node_exporter" "$INSTALL_DIR/"
chmod 755 "$INSTALL_DIR/node_exporter"
chown $NODE_EXPORTER_USER:$NODE_EXPORTER_USER "$INSTALL_DIR/node_exporter"
rm -rf "/tmp/${FILENAME}"*
echo "✓ Binary installed to $INSTALL_DIR/node_exporter"

# Create systemd service
cat > "${SYSTEMD_DIR}/node_exporter.service" << 'EOF'
[Unit]
Description=Node Exporter - Prometheus OS Metrics
Documentation=https://github.com/prometheus/node_exporter
Wants=network-online.target
After=network-online.target

[Service]
User=node_exporter
Group=node_exporter
Type=simple
Restart=on-failure
RestartSec=5s

ExecStart=/usr/local/bin/node_exporter \
    --web.listen-address=0.0.0.0:9100 \
    --web.telemetry-path=/metrics \
    --collector.filesystem.mount-points-exclude='^/(sys|proc|dev|host|etc)($$|/)' \
    --collector.netclass.ignored-devices='^(veth|docker|br-|lo).*' \
    --collector.cpu \
    --collector.meminfo \
    --collector.diskstats \
    --collector.filesystem \
    --collector.netstat \
    --collector.netdev \
    --collector.loadavg \
    --collector.vmstat \
    --collector.uname \
    --collector.time \
    --collector.processes \
    --collector.systemd \
    --collector.textfile.directory=/var/lib/node_exporter/textfile_collector

[Install]
WantedBy=multi-user.target
EOF

# Textfile collector directory
mkdir -p /var/lib/node_exporter/textfile_collector
chown -R $NODE_EXPORTER_USER:$NODE_EXPORTER_USER /var/lib/node_exporter

# Enable and start
systemctl daemon-reload
systemctl enable node_exporter
systemctl start node_exporter

sleep 2

if systemctl is-active --quiet node_exporter; then
    echo "✓ Node Exporter is running!"
    echo "  Metrics URL: http://$(hostname -I | awk '{print $1}'):9100/metrics"
else
    echo "✗ Node Exporter failed to start!"
    systemctl status node_exporter
    exit 1
fi

# Firewall: allow Prometheus server to scrape (adjust IP)
# ufw allow from PROMETHEUS_SERVER_IP to any port 9100 proto tcp

echo ""
echo "======================================================"
echo "✅ Node Exporter installed successfully!"
echo ""
echo "Add to prometheus.yml:"
echo "  - job_name: 'node-exporter'"
echo "    static_configs:"
echo "      - targets: ['$(hostname -I | awk '{print $1}'):9100']"
echo "======================================================"
