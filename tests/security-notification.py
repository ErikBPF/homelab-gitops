#!/usr/bin/env python3
"""Offline contract for the security workflow's simple notification predicate."""

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def notification_runs(condition, event, outcome, prior_success):
    # This workflow uses only conjunctions and literal comparisons. Fail closed
    # on other syntax instead of pretending to implement GitHub expressions.
    terms = [term.strip() for term in condition.split("&&")]
    values = []
    for term in terms:
        if term in ("always()", "success()"):
            values.append(term == "always()" or prior_success)
            continue
        match = re.fullmatch(
            r"(github\.event_name|steps\.gitleaks\.outcome) == '([a-z_]+)'", term
        )
        if match is None:
            raise ValueError(f"unsupported notification condition: {term}")
        actual = event if match[1] == "github.event_name" else outcome
        values.append(actual.casefold() == match[2].casefold())
    # GitHub adds success() when no status function is present.
    status = "always()" in terms or "success()" in terms or prior_success
    return status and all(values)


class SecurityNotification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / ".github/workflows/security.yml").read_text()
        trigger = re.search(r"(?m)^on:\n((?:[ \t].*\n|\n)+)", cls.source)
        if trigger is None:
            raise AssertionError("missing explicit workflow trigger mapping")
        cls.events = re.findall(r"(?m)^  ([a-z_]+):\s*$", trigger[1])
        conditions = re.findall(
            r"(?m)^      - name: Notify security channel\n        if: (.+)$",
            cls.source,
        )
        if len(conditions) != 1:
            raise AssertionError("expected one security notification condition")
        cls.condition = conditions[0]

    def test_manual_trigger_and_scanner_identity(self):
        self.assertEqual(self.events, ["workflow_dispatch"])
        self.assertEqual(self.source.count("      - id: gitleaks\n"), 1)

    def test_notification_for_declared_events(self):
        for event in self.events:
            for outcome in ("failure", "success", "skipped", "cancelled"):
                for prior_success in (False, True):
                    with self.subTest(event=event, outcome=outcome, prior_success=prior_success):
                        self.assertEqual(
                            notification_runs(self.condition, event, outcome, prior_success),
                            outcome == "failure",
                        )

    def test_known_failure_controls(self):
        failure = "steps.gitleaks.outcome == 'failure'"
        self.assertFalse(notification_runs(failure, "workflow_dispatch", "failure", False))
        self.assertFalse(notification_runs(
            "always() && github.event_name == 'push' && " + failure,
            "workflow_dispatch", "failure", False,
        ))
        self.assertTrue(notification_runs("always() && " + failure, "workflow_dispatch", "failure", False))
        with self.assertRaises(ValueError):
            notification_runs("unknown()", "workflow_dispatch", "failure", False)


if __name__ == "__main__":
    unittest.main()
