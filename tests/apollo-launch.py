#!/usr/bin/env python3
"""Run the launch recipe with fake host commands; never contact Apollo."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

LAUNCH = Path(__file__).resolve().parents[1] / "apps/homelab/apollo-qwen27b/launch.sh"
CHAIN = """-N apollo-qwen-litellm
-A apollo-qwen-litellm -i lo -j ACCEPT
-A apollo-qwen-litellm -s 100.103.52.100/32 -i tailscale0 -p tcp -m multiport --dports 11542,11560 -j ACCEPT
-A apollo-qwen-litellm -j REJECT --reject-with icmp-port-unreachable"""
JUMPS = """-A INPUT -d 100.77.14.27/32 -p tcp -m tcp --dport 11542 -j apollo-qwen-litellm
-A INPUT -d 100.77.14.27/32 -p tcp -m tcp --dport 11560 -j apollo-qwen-litellm"""
INPUT = "-P INPUT ACCEPT\n-A INPUT -j ts-input\n" + JUMPS + "\n-A INPUT -j nixos-fw"

SUDO = """#!/usr/bin/env bash
if [[ " $* " == *" iptables "* ]]; then
  [[ $FAIL != yes ]] || exit 1
  if [[ " $* " == *" INPUT "* ]]; then printf "%s\\n" "$INPUT_RULES"; else printf "%s\\n" "$CHAIN_RULES"; fi
  exit 0
fi
echo CONTAINER_STARTED
"""


class Launch(unittest.TestCase):
    def _env(self, directory, **overrides):
        root = Path(directory)
        (root / "sudo").write_text(SUDO)
        (root / "sudo").chmod(0o755)
        (root / "findmnt").write_text("#!/usr/bin/env bash\necho /dev/sdc1\n")
        (root / "findmnt").chmod(0o755)
        (root / "nvidia-smi").write_text(
            "#!/usr/bin/env bash\n"
            "printf 'GPU-af808452-3544-e84a-b33a-fc5588ede436\\nGPU-818843e6-ce30-5d98-a4b4-8d8580aaa049\\n'\n"
        )
        (root / "nvidia-smi").chmod(0o755)
        runtime = root / "runtime"
        runtime.mkdir(exist_ok=True)
        for script in ("serve-vllm.sh", "serve-lmcache.sh", "supervise.sh"):
            (runtime / script).write_text("")
        model = root / "model"
        model.mkdir(exist_ok=True)
        (model / "config.json").write_text("{}")
        return dict(
            os.environ,
            PATH=str(root) + ":" + os.environ["PATH"],
            RUNTIME=str(runtime),
            MODEL_DIR=str(model),
            **overrides,
        )

    def test_firewall_required_before_container_start(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, chain, rules, fail, allowed in [
                ("restricted", CHAIN, INPUT, "no", True),
                ("missing", "", "-P INPUT ACCEPT", "no", False),
                ("broad", CHAIN.replace("-i lo", ""), INPUT, "no", False),
                ("wrong_chain", CHAIN.replace("apollo-qwen-litellm", "other", 1), INPUT, "no", False),
                ("read_failure", CHAIN, INPUT, "yes", False),
            ]:
                with self.subTest(name=name):
                    env = self._env(directory, CHAIN_RULES=chain, INPUT_RULES=rules, FAIL=fail)
                    result = subprocess.run(["bash", str(LAUNCH)], env=env, capture_output=True, text=True)
                    self.assertEqual("CONTAINER_STARTED" in result.stdout, allowed)
                    self.assertEqual(result.returncode == 0, allowed)

    def test_run_mode_also_gates_on_the_firewall(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self._env(directory, CHAIN_RULES="", INPUT_RULES="-P INPUT ACCEPT", FAIL="no")
            result = subprocess.run(["bash", str(LAUNCH), "run"], env=env, capture_output=True, text=True)
            self.assertNotIn("CONTAINER_STARTED", result.stdout)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
