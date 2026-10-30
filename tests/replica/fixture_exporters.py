"""Two synthetic, guest-local discovery targets; no outbound requests."""

import http.server
import re
import sys
import threading

run_id = sys.argv[1]
if not re.fullmatch(r"[0-9a-f-]{36}", run_id):
    raise ValueError("invalid fixture run ID")


class Metrics(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        name = (
            "argocd_app_info"
            if self.server.server_port == 8082
            else "traefik_replica_metric"
        )
        data = f'{name}{{replica_run="{run_id}"}} 1\n'.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


servers = [
    http.server.ThreadingHTTPServer(("0.0.0.0", port), Metrics) for port in [9100, 8082]
]
threading.Thread(target=servers[0].serve_forever, daemon=True).start()
servers[1].serve_forever()
