#!/usr/bin/env python3
"""Run the launch recipe with fake host commands; never contact Apollo."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

LAUNCH = Path(__file__).resolve().parents[1] / "apps/homelab/apollo-qwen27b/launch.sh"
CHAIN = """-N apollo-qwen-personal
-A apollo-qwen-personal -i lo -j ACCEPT
-A apollo-qwen-personal -s 100.76.140.121/32 -i tailscale0 -j ACCEPT
-A apollo-qwen-personal -j REJECT --reject-with icmp-port-unreachable"""
JUMP = "-A INPUT -d 100.77.14.27/32 -p tcp -m tcp --dport 11542 -j apollo-qwen-personal"

class Launch(unittest.TestCase):
    def test_firewall_required_before_container_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = {
                "sudo": '#!/usr/bin/env bash\nif [[ $2 == iptables ]]; then\n  [[ $FAIL != yes ]] || exit 1\n  if [[ $4 == INPUT ]]; then printf "%s\\n" "$INPUT_RULES"; else printf "%s\\n" "$CHAIN_RULES"; fi\nelse echo CONTAINER_STARTED; fi\n',
                "findmnt": '#!/usr/bin/env bash\necho /dev/md127\n',
                "nvidia-smi": '#!/usr/bin/env bash\nif [[ ${@: -1} == 1 ]]; then echo GPU-af808452-3544-e84a-b33a-fc5588ede436; else echo GPU-818843e6-ce30-5d98-a4b4-8d8580aaa049; fi\n',
                "cat": '#!/usr/bin/env bash\necho /fake-dependency\n',
            }
            for name, body in commands.items():
                path = root / name
                path.write_text(body)
                path.chmod(0o755)
            for name, chain, rules, fail, allowed in [
                ("restricted", CHAIN, "-P INPUT ACCEPT\n" + JUMP, "no", True),
                ("missing", "", "-P INPUT ACCEPT", "no", False),
                ("broad", CHAIN.replace("-i lo", ""), "-P INPUT ACCEPT\n" + JUMP, "no", False),
                ("earlier_accept", CHAIN, "-P INPUT ACCEPT\n-A INPUT -j ACCEPT\n" + JUMP, "no", False),
                ("read_failure", CHAIN, "-P INPUT ACCEPT\n" + JUMP, "yes", False),
            ]:
                with self.subTest(name=name):
                    env = dict(os.environ, PATH=str(root) + ":" + os.environ["PATH"], CHAIN_RULES=chain, INPUT_RULES=rules, FAIL=fail)
                    result = subprocess.run(["bash", str(LAUNCH)], env=env, capture_output=True, text=True)
                    self.assertEqual("CONTAINER_STARTED" in result.stdout, allowed)
                    self.assertEqual(result.returncode == 0, allowed)

if __name__ == "__main__":
    unittest.main()
