#!/usr/bin/env bash
# Install the Apollo AI runtime on the host: scripts into /etc/apollo-ai and the
# systemd unit into /etc/systemd/system. Run on Apollo as erik (sudo for root).
#
#   install.sh            install + daemon-reload + enable --now
#   install.sh --no-start install + daemon-reload + enable only
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
runtime=${RUNTIME:-/etc/apollo-ai}

if [[ ${EUID} -eq 0 ]]; then SUDO=(); else SUDO=(sudo -n); fi
"${SUDO[@]}" install -d -m 0755 "$runtime"
"${SUDO[@]}" install -m 0755 "$here/serve-vllm.sh" "$here/serve-lmcache.sh" "$here/launch.sh" "$runtime"/
"${SUDO[@]}" install -m 0644 "$here/apollo-ai.service" /etc/systemd/system/apollo-ai.service
"${SUDO[@]}" systemctl daemon-reload

if [[ ${1:-} == --no-start ]]; then
  "${SUDO[@]}" systemctl enable apollo-ai.service
  echo "installed; start with: sudo systemctl start apollo-ai.service"
else
  "${SUDO[@]}" systemctl enable --now apollo-ai.service
  echo "installed and started; status: systemctl status apollo-ai.service"
fi
