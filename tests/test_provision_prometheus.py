#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

import contextlib
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import yaml


_LOADER = importlib.machinery.SourceFileLoader(
    "metrics_alert_rules_under_test",
    str(Path(__file__).parents[1] / "imageroot/bin/metrics_alert_rules.py"),
)
_SPEC = importlib.util.spec_from_loader(_LOADER.name, _LOADER)
provision_prometheus = importlib.util.module_from_spec(_SPEC)
sys.modules[_LOADER.name] = provision_prometheus
_LOADER.exec_module(provision_prometheus)


COMPLETE_ANNOTATIONS = """\
annotations:
  summary_en: English summary
  summary_it: Riassunto italiano
  description_en: English description
  description_it: Descrizione italiana
"""

SINGLE_RULE = """\
alert: PostgresqlDown
expr: up{module_id="postgresql1"} == 0
for: 5m
labels:
  severity: critical
  module_id: keep-me
  service: postgresql
""" + COMPLETE_ANNOTATIONS

FULL_RULE = """\
groups:
- name: postgresql.rules
  rules:
  - alert: PostgresqlConnectionsHigh
    expr: pg_stat_activity_count > 100
    labels:
      severity: warning
    annotations:
      summary_en: English summary
      summary_it: Riassunto italiano
      description_en: English description
      description_it: Descrizione italiana
"""


class FakeRedis:
    def __init__(self, values=None):
        self.values = values or {}

    def scan_iter(self, pattern):
        if pattern == "module/*/metrics_alert_rules":
            return [
                key for key in self.values
                if key.endswith("/metrics_alert_rules")
            ]
        return []

    def hgetall(self, key):
        return self.values.get(key, {})


class AlertRuleValidationTest(unittest.TestCase):
    def test_normalizes_single_rule_without_changing_labels(self):
        document = provision_prometheus.normalize_alert_rule(
            SINGLE_RULE, "postgresql1", "availability"
        )

        group = document["groups"][0]
        self.assertEqual(group["name"], "postgresql1.availability")
        self.assertEqual(
            group["rules"][0]["labels"],
            {
                "severity": "critical",
                "module_id": "keep-me",
                "service": "postgresql",
            },
        )
        self.assertNotIn("source_module_id", group["rules"][0]["labels"])

    def test_preserves_full_rule_file(self):
        expected = yaml.safe_load(FULL_RULE)
        document = provision_prometheus.normalize_alert_rule(
            FULL_RULE, "postgresql1", "connections"
        )
        self.assertEqual(document, expected)

    def test_rejects_unsupported_rule_schemas(self):
        invalid_payloads = (
            "not-a-mapping",
            "groups: not-a-list",
            "groups:\n- name: missing-rules",
            "groups:\n- name: bad\n  rules:\n  - expr: up",
            "alert: MissingExpression",
            "record: saved_up\nexpr: up",
            "groups:\n- name: records\n  rules:\n  - record: saved_up\n    expr: up",
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(provision_prometheus.RuleValidationError):
                    provision_prometheus.normalize_alert_rule(
                        payload, "postgresql1", "invalid"
                    )

    def test_wraps_non_yaml_parser_errors(self):
        with mock.patch.object(
            provision_prometheus.yaml,
            "safe_load",
            side_effect=RecursionError("input is too deeply nested"),
        ):
            with self.assertRaisesRegex(
                provision_prometheus.RuleValidationError, "invalid YAML"
            ):
                provision_prometheus.normalize_alert_rule(
                    SINGLE_RULE, "postgresql1", "nested"
                )

    def test_rejects_unsafe_file_names(self):
        for name in ("", ".", "..", "../escape", "has/slash", "has space"):
            with self.subTest(name=name):
                with self.assertRaises(provision_prometheus.RuleValidationError):
                    provision_prometheus.generated_rule_filename("postgresql1", name)

    def test_warns_but_accepts_incomplete_metadata(self):
        source = provision_prometheus.AlertRuleSource(
            "postgresql1",
            "warning",
            "alert: PostgresqlWarning\nexpr: up\nlabels:\n  severity: info\n",
            "module/postgresql1/metrics_alert_rules",
        )
        document = provision_prometheus.normalize_alert_rule(
            source.payload, source.module_id, source.name
        )
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            provision_prometheus.warn_incomplete_rules(document, source)

        self.assertIn("unsupported severity 'info'", stderr.getvalue())
        self.assertIn("missing bilingual annotations", stderr.getvalue())


class AlertRuleProvisioningTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.previous_directory = os.getcwd()
        os.chdir(self.tempdir.name)
        Path("rules.d").mkdir()
        Path("rules.d/nodes.yml").write_text(
            "groups:\n- name: Nodes\n  rules: []\n", encoding="utf-8"
        )
        provision_prometheus.RULES_DIR = Path("rules.d")
        self.previous_module_id = os.environ.get("MODULE_ID")
        os.environ["MODULE_ID"] = "metrics1"

    def tearDown(self):
        if self.previous_module_id is None:
            os.environ.pop("MODULE_ID", None)
        else:
            os.environ["MODULE_ID"] = self.previous_module_id
        os.chdir(self.previous_directory)
        self.tempdir.cleanup()

    @mock.patch.object(provision_prometheus, "_run_promtool")
    def test_installs_each_source_and_removes_only_stale_files(self, promtool):
        redis = FakeRedis({
            "module/postgresql1/metrics_alert_rules": {
                "availability": SINGLE_RULE,
                "connections": FULL_RULE,
            },
            "module/metrics1/custom_alerts": {
                "local": SINGLE_RULE.replace("PostgresqlDown", "LocalAlert"),
            },
        })

        provision_prometheus.provision(redis)

        generated = sorted(path.name for path in Path("rules.d").glob("provision_*.yml"))
        self.assertEqual(
            generated,
            [
                "provision_metrics1_local.yml",
                "provision_postgresql1_availability.yml",
                "provision_postgresql1_connections.yml",
            ],
        )
        self.assertTrue(Path("rules.d/nodes.yml").exists())
        promtool.assert_called()

        redis.values["module/postgresql1/metrics_alert_rules"].pop("connections")
        provision_prometheus.provision(redis)
        self.assertFalse(Path("rules.d/provision_postgresql1_connections.yml").exists())
        self.assertTrue(Path("rules.d/nodes.yml").exists())

    @mock.patch.object(provision_prometheus, "_run_promtool")
    def test_invalid_update_keeps_previous_valid_file(self, promtool):
        key = "module/postgresql1/metrics_alert_rules"
        redis = FakeRedis({key: {"availability": SINGLE_RULE}})
        provision_prometheus.provision(redis)
        path = Path("rules.d/provision_postgresql1_availability.yml")
        previous = path.read_bytes()

        redis.values[key]["availability"] = "groups: ["
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            provision_prometheus.provision(redis)

        self.assertEqual(path.read_bytes(), previous)
        self.assertIn("previous valid file retained", stderr.getvalue())
        self.assertEqual(promtool.call_count, 1)

    @mock.patch.object(provision_prometheus, "_run_promtool")
    def test_invalid_implicit_value_does_not_block_other_rules(self, promtool):
        invalid_date_rule = SINGLE_RULE.replace(
            "summary_en: English summary", "summary_en: 2026-99-99"
        )
        redis = FakeRedis({
            "module/postgresql1/metrics_alert_rules": {
                "invalid-date": invalid_date_rule,
                "valid": FULL_RULE,
            }
        })
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            provision_prometheus.provision(redis)

        self.assertFalse(
            Path("rules.d/provision_postgresql1_invalid-date.yml").exists()
        )
        self.assertTrue(Path("rules.d/provision_postgresql1_valid.yml").exists())
        self.assertIn("invalid YAML", stderr.getvalue())
        self.assertIn("no rule file installed", stderr.getvalue())

    @mock.patch.object(provision_prometheus, "_run_promtool")
    def test_serialization_failure_does_not_block_other_rules(self, promtool):
        redis = FakeRedis({
            "module/postgresql1/metrics_alert_rules": {
                "invalid": SINGLE_RULE.replace("PostgresqlDown", "InvalidAlert"),
                "valid": FULL_RULE,
            }
        })
        real_safe_dump = yaml.safe_dump

        def serialize(document, *args, **kwargs):
            alert_names = {
                rule["alert"]
                for group in document["groups"]
                for rule in group["rules"]
            }
            if "InvalidAlert" in alert_names:
                raise RecursionError("input is too deeply nested")
            return real_safe_dump(document, *args, **kwargs)

        stderr = io.StringIO()
        with mock.patch.object(
            provision_prometheus.yaml, "safe_dump", side_effect=serialize
        ), contextlib.redirect_stderr(stderr):
            provision_prometheus.provision(redis)

        self.assertFalse(Path("rules.d/provision_postgresql1_invalid.yml").exists())
        self.assertTrue(Path("rules.d/provision_postgresql1_valid.yml").exists())
        self.assertIn("input is too deeply nested", stderr.getvalue())
        self.assertEqual(promtool.call_count, 1)

    @mock.patch.object(provision_prometheus, "_run_promtool")
    def test_non_scalar_severity_does_not_block_other_rules(self, promtool):
        def validate(candidate_directory, candidate_name):
            candidate = Path(candidate_directory) / candidate_name
            document = yaml.safe_load(candidate.read_text(encoding="utf-8"))
            severity = document["groups"][0]["rules"][0]["labels"]["severity"]
            if not isinstance(severity, str):
                raise provision_prometheus.RuleValidationError(
                    "label value must be a string"
                )

        promtool.side_effect = validate
        key = "module/postgresql1/metrics_alert_rules"
        redis = FakeRedis({key: {"availability": SINGLE_RULE}})
        provision_prometheus.provision(redis)
        retained = Path("rules.d/provision_postgresql1_availability.yml")
        previous = retained.read_bytes()

        redis.values[key] = {
            "availability": SINGLE_RULE.replace(
                "severity: critical", "severity: [critical]"
            ),
            "connections": FULL_RULE,
        }
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            provision_prometheus.provision(redis)

        self.assertEqual(retained.read_bytes(), previous)
        self.assertTrue(Path("rules.d/provision_postgresql1_connections.yml").exists())
        self.assertIn("unsupported severity ['critical']", stderr.getvalue())
        self.assertIn("previous valid file retained", stderr.getvalue())

    @mock.patch.object(provision_prometheus, "_run_promtool")
    def test_promtool_failure_does_not_block_another_valid_rule(self, promtool):
        def validate(candidate_directory, candidate_name):
            if candidate_name.endswith("invalid.yml"):
                raise provision_prometheus.RuleValidationError("invalid PromQL")

        promtool.side_effect = validate
        redis = FakeRedis({
            "module/postgresql1/metrics_alert_rules": {
                "valid": SINGLE_RULE,
                "invalid": SINGLE_RULE.replace("PostgresqlDown", "BadPromQL"),
            }
        })
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            provision_prometheus.provision(redis)

        self.assertTrue(Path("rules.d/provision_postgresql1_valid.yml").exists())
        self.assertFalse(Path("rules.d/provision_postgresql1_invalid.yml").exists())
        self.assertIn("invalid PromQL", stderr.getvalue())


class MetricReferenceCheckTest(unittest.TestCase):
    def test_handles_absent_dynamic_and_exact_name_selectors(self):
        ast = {
            "type": "binaryExpr",
            "lhs": {
                "type": "call",
                "func": {"name": "absent"},
                "args": [{"type": "vectorSelector", "name": "missing_on_purpose"}],
            },
            "rhs": {
                "type": "vectorSelector",
                "name": "",
                "matchers": [
                    {"name": "__name__", "type": "=~", "value": "dynamic_.+"}
                ],
            },
            "extra": {
                "type": "matrixSelector",
                "name": "known_static_metric",
                "matchers": [
                    {"name": "__name__", "type": "=", "value": "known_static_metric"}
                ],
            },
            "exact_matcher": {
                "type": "vectorSelector",
                "name": "",
                "matchers": [
                    {"name": "__name__", "type": "=", "value": "exact_static_metric"}
                ],
            },
        }

        self.assertEqual(
            provision_prometheus.collect_static_metric_names(ast),
            {"known_static_metric", "exact_static_metric"},
        )

    @mock.patch.object(provision_prometheus, "_active_generated_rules")
    @mock.patch.object(provision_prometheus, "_prometheus_api_get")
    def test_parser_failure_is_advisory(self, api_get, active_rules):
        active_rules.return_value = [
            (types.SimpleNamespace(module_id="app1", name="rule1"), "AppDown", "up == 0")
        ]
        api_get.side_effect = provision_prometheus.PrometheusAPIError("not supported")
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            provision_prometheus.check_metric_references(FakeRedis())

        self.assertIn("Deferred metric reference check", stderr.getvalue())

    @mock.patch.object(provision_prometheus, "_active_generated_rules")
    @mock.patch.object(provision_prometheus, "_prometheus_api_get")
    def test_empty_metric_set_is_advisory(self, api_get, active_rules):
        active_rules.return_value = [
            (types.SimpleNamespace(module_id="app1", name="rule1"), "AppDown", "up == 0")
        ]
        api_get.side_effect = [
            {"type": "vectorSelector", "name": "up"},
            [],
        ]
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            provision_prometheus.check_metric_references(FakeRedis())

        self.assertIn("no known metric names yet", stderr.getvalue())

    @mock.patch.object(provision_prometheus, "_active_generated_rules")
    @mock.patch.object(provision_prometheus, "_prometheus_api_get")
    def test_unknown_metric_warning_has_rule_provenance(self, api_get, active_rules):
        active_rules.return_value = [
            (types.SimpleNamespace(module_id="app1", name="database"), "AppDown", "app_up == 0")
        ]
        api_get.side_effect = [
            {"type": "vectorSelector", "name": "app_up"},
            ["up"],
        ]
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            provision_prometheus.check_metric_references(FakeRedis())

        warning = stderr.getvalue()
        self.assertIn("module app1", warning)
        self.assertIn("rule 'database'", warning)
        self.assertIn("alert 'AppDown'", warning)
        self.assertIn("unknown metric 'app_up'", warning)


if __name__ == "__main__":
    unittest.main()
