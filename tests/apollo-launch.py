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
-A apollo-qwen-personal -s 100.103.52.100/32 -i tailscale0 -j ACCEPT
-A apollo-qwen-personal -j REJECT --reject-with icmp-port-unreachable"""
JUMP = "-A INPUT -d 100.77.14.27/32 -p tcp -m tcp --dport 11542 -j apollo-qwen-personal"

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
        """Fake host commands plus the paths preflight must see."""
        root = Path(directory)
        (root / "sudo").write_text(SUDO)
        (root / "sudo").chmod(0o755)
        (root / "findmnt").write_text("#!/usr/bin/env bash\necho /dev/sdc1\n")
        (root / "findmnt").chmod(0o755)
        (root / "nvidia-smi").write_text(
            "#!/usr/bin/env bash\n"
            "if [[ ${@: -1} == 1 ]]; then echo GPU-af808452-3544-e84a-b33a-fc5588ede436; "
            "else echo GPU-818843e6-ce30-5d98-a4b4-8d8580aaa049; fi\n"
        )
        (root / "nvidia-smi").chmod(0o755)
        runtime = root / "runtime"
        runtime.mkdir(exist_ok=True)
        (runtime / "serve-vllm.sh").write_text("")
        (runtime / "serve-lmcache.sh").write_text("")
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
                ("restricted", CHAIN, "-P INPUT ACCEPT\n" + JUMP, "no", True),
                ("missing", "", "-P INPUT ACCEPT", "no", False),
                ("broad", CHAIN.replace("-i lo", ""), "-P INPUT ACCEPT\n" + JUMP, "no", False),
                ("earlier_accept", CHAIN, "-P INPUT ACCEPT\n-A INPUT -j ACCEPT\n" + JUMP, "no", False),
                ("read_failure", CHAIN, "-P INPUT ACCEPT\n" + JUMP, "yes", False),
            ]:
                with self.subTest(name=name):
                    env = self._env(directory, CHAIN_RULES=chain, INPUT_RULES=rules, FAIL=fail)
                    result = subprocess.run(["bash", str(LAUNCH)], env=env, capture_output=True, text=True)
                    self.assertEqual("CONTAINER_STARTED" in result.stdout, allowed)
                    self.assertEqual(result.returncode == 0, allowed)

    def test_run_supervisor_fails_when_a_child_ends(self):
        """`run` is the systemd mode: it must not report success after a child exits."""
        with tempfile.TemporaryDirectory() as directory:
            env = self._env(directory, CHAIN_RULES=CHAIN, INPUT_RULES="-P INPUT ACCEPT\n" + JUMP, FAIL="no")
            result = subprocess.run(["bash", str(LAUNCH), "run"], env=env, capture_output=True, text=True)
            self.assertIn("CONTAINER_STARTED", result.stdout)
            self.assertNotEqual(result.returncode, 0)

    def test_run_respects_the_firewall_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self._env(directory, CHAIN_RULES="", INPUT_RULES="-P INPUT ACCEPT", FAIL="no")
            result = subprocess.run(["bash", str(LAUNCH), "run"], env=env, capture_output=True, text=True)
            self.assertNotIn("CONTAINER_STARTED", result.stdout)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
