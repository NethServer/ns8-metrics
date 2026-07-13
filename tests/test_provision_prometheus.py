#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

import contextlib
import fnmatch
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import runpy
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
        return [key for key in self.values if fnmatch.fnmatch(key, pattern)]

    def hgetall(self, key):
        return self.values.get(key, {})

    def hvals(self, key):
        return list(self.values.get(key, {}).values())

    def sismember(self, key, value):
        return False

    def exists(self, key):
        return key in self.values


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

    @mock.patch.object(provision_prometheus, "scope_promql_expression")
    def test_scopes_single_rule_to_redis_owner(self, scope_expression):
        scope_expression.return_value = 'up{module_id="postgresql1"} == 0'
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            document = provision_prometheus.normalize_alert_rule(
                SINGLE_RULE,
                "postgresql1",
                "availability",
                scoped=True,
                redis_key="module/postgresql1/metrics_alert_rules",
            )

        group = document["groups"][0]
        rule = group["rules"][0]
        self.assertEqual(group["name"], "ns8:postgresql1:availability")
        self.assertEqual(rule["expr"], 'up{module_id="postgresql1"} == 0')
        self.assertEqual(rule["labels"]["module_id"], "postgresql1")
        self.assertEqual(rule["labels"]["service"], "postgresql")
        self.assertIn("using authoritative value 'postgresql1'", stderr.getvalue())

    @mock.patch.object(provision_prometheus, "scope_promql_expression")
    def test_rewrites_full_group_names_and_labels(self, scope_expression):
        scope_expression.return_value = (
            'pg_stat_activity_count{module_id="postgresql1"} > 100'
        )
        payload = FULL_RULE.replace(
            "  rules:", "  labels:\n    module_id: another-module\n  rules:"
        )

        document = provision_prometheus.normalize_alert_rule(
            payload,
            "postgresql1",
            "connections",
            scoped=True,
            redis_key="module/postgresql1/metrics_alert_rules",
        )

        group = document["groups"][0]
        rule = group["rules"][0]
        self.assertEqual(
            group["name"],
            "ns8:postgresql1:connections:postgresql.rules",
        )
        self.assertEqual(group["labels"]["module_id"], "postgresql1")
        self.assertEqual(rule["labels"]["module_id"], "postgresql1")

    @mock.patch.object(provision_prometheus, "scope_promql_expression")
    def test_effective_group_names_do_not_depend_on_order(self, scope_expression):
        scope_expression.side_effect = lambda expression, module_id, source=None: expression
        payload = (
            "groups:\n"
            "- name: availability\n"
            "  rules:\n"
            "  - alert: Available\n"
            "    expr: up\n"
            "- name: capacity\n"
            "  rules:\n"
            "  - alert: Capacity\n"
            "    expr: disk_free_bytes\n"
        )
        reordered = payload.replace(
            "- name: availability\n"
            "  rules:\n"
            "  - alert: Available\n"
            "    expr: up\n"
            "- name: capacity\n"
            "  rules:\n"
            "  - alert: Capacity\n"
            "    expr: disk_free_bytes\n",
            "- name: capacity\n"
            "  rules:\n"
            "  - alert: Capacity\n"
            "    expr: disk_free_bytes\n"
            "- name: availability\n"
            "  rules:\n"
            "  - alert: Available\n"
            "    expr: up\n",
        )

        names = {
            group["name"]
            for group in provision_prometheus.normalize_alert_rule(
                payload, "postgresql1", "database", scoped=True
            )["groups"]
        }
        reordered_names = {
            group["name"]
            for group in provision_prometheus.normalize_alert_rule(
                reordered, "postgresql1", "database", scoped=True
            )["groups"]
        }

        self.assertEqual(names, reordered_names)
        self.assertEqual(
            names,
            {
                "ns8:postgresql1:database:availability",
                "ns8:postgresql1:database:capacity",
            },
        )

    def test_rejects_reserved_and_duplicate_group_names(self):
        payloads = (
            "groups:\n- name: ns8:reserved\n  rules: []\n",
            "groups:\n- name: repeated\n  rules: []\n"
            "- name: repeated\n  rules: []\n",
        )

        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(provision_prometheus.RuleValidationError):
                    provision_prometheus.normalize_alert_rule(
                        payload, "postgresql1", "invalid", scoped=True
                    )

    def test_scopes_promql_selectors_with_promtool(self):
        cases = (
            (
                "up == 0",
                "up == 0",
                "up == 0",
                'up{module_id="postgresql1"} == 0',
            ),
            (
                "rate(requests_total[5m]) > 1",
                "rate(requests_total[5m]) > 1",
                "rate(requests_total[5m]) > 1",
                'rate(requests_total{module_id="postgresql1"}[5m]) > 1',
            ),
            (
                "sum(up) + rate(requests_total[5m])",
                "sum(up) + rate(requests_total[5m])",
                "sum(up) + rate(requests_total[5m])",
                'sum(up{module_id="postgresql1"}) + '
                'rate(requests_total{module_id="postgresql1"}[5m])',
            ),
            (
                "absent(up)",
                "absent(up)",
                "absent(up)",
                'absent(up{module_id="postgresql1"})',
            ),
        )

        for authored, formatted, matcher_free, expected in cases:
            with self.subTest(authored=authored), mock.patch.object(
                provision_prometheus,
                "_run_promtool_command",
                side_effect=(formatted, matcher_free, expected),
            ) as promtool:
                self.assertEqual(
                    provision_prometheus.scope_promql_expression(
                        authored, "postgresql1"
                    ),
                    expected,
                )
                self.assertEqual(promtool.call_count, 3)

    def test_replaces_conflicting_promql_matcher_with_warning(self):
        source = provision_prometheus.AlertRuleSource(
            "postgresql1",
            "availability",
            "",
            "module/postgresql1/metrics_alert_rules",
        )
        stderr = io.StringIO()
        with mock.patch.object(
            provision_prometheus,
            "_run_promtool_command",
            side_effect=(
                'up{module_id=~"postgresql.*"} == 0',
                "up == 0",
                'up{module_id="postgresql1"} == 0',
            ),
        ), contextlib.redirect_stderr(stderr):
            expression = provision_prometheus.scope_promql_expression(
                'up{module_id=~"postgresql.*"} == 0',
                "postgresql1",
                source,
            )

        self.assertEqual(expression, 'up{module_id="postgresql1"} == 0')
        self.assertIn("broader or conflicting", stderr.getvalue())

    def test_rejects_promql_without_selectors(self):
        with mock.patch.object(
            provision_prometheus,
            "_run_promtool_command",
            side_effect=("vector(1)", "vector(1)", "vector(1)"),
        ), self.assertRaisesRegex(
            provision_prometheus.RuleValidationError, "no vector or range selector"
        ):
            provision_prometheus.scope_promql_expression("vector(1)", "postgresql1")

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
        self.scope_patcher = mock.patch.object(
            provision_prometheus,
            "scope_promql_expression",
            side_effect=lambda expression, module_id, source=None: (
                f"{expression} scoped-to {module_id}"
            ),
        )
        self.scope_patcher.start()

    def tearDown(self):
        self.scope_patcher.stop()
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
    def test_invalid_group_update_keeps_previous_valid_file(self, promtool):
        key = "module/postgresql1/metrics_alert_rules"
        redis = FakeRedis({key: {"availability": SINGLE_RULE}})
        provision_prometheus.provision(redis)
        path = Path("rules.d/provision_postgresql1_availability.yml")
        previous = path.read_bytes()

        redis.values[key]["availability"] = (
            "groups:\n- name: ns8:reserved\n  rules: []\n"
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            provision_prometheus.provision(redis)

        self.assertEqual(path.read_bytes(), previous)
        self.assertIn("reserved 'ns8:' prefix", stderr.getvalue())
        self.assertIn("previous valid file retained", stderr.getvalue())

    @mock.patch.object(provision_prometheus, "_run_promtool")
    def test_non_utf8_update_keeps_previous_valid_file(self, promtool):
        key = "module/postgresql1/metrics_alert_rules"
        redis = FakeRedis({key: {b"availability": SINGLE_RULE.encode("utf-8")}})
        provision_prometheus.provision(redis)
        path = Path("rules.d/provision_postgresql1_availability.yml")
        previous = path.read_bytes()

        redis.values[key] = {
            b"availability": b"\xff",
            b"connections": FULL_RULE.encode("utf-8"),
        }
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            provision_prometheus.provision(redis)

        self.assertEqual(path.read_bytes(), previous)
        self.assertTrue(Path("rules.d/provision_postgresql1_connections.yml").exists())
        self.assertIn("payload is not valid UTF-8", stderr.getvalue())
        self.assertIn("previous valid file retained", stderr.getvalue())
        self.assertEqual(promtool.call_count, 2)

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

    @mock.patch.object(provision_prometheus, "_run_promtool")
    def test_duplicate_identity_is_per_module(self, promtool):
        redis = FakeRedis({
            "module/postgresql1/metrics_alert_rules": {
                "first": SINGLE_RULE,
                "second": SINGLE_RULE,
            },
            "module/postgresql2/metrics_alert_rules": {
                "first": SINGLE_RULE,
            },
        })
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            provision_prometheus.provision(redis)

        warning = stderr.getvalue()
        self.assertIn(
            "duplicate alert identity ('PostgresqlDown', module_id 'postgresql1')",
            warning,
        )
        self.assertNotIn(
            "duplicate alert identity ('PostgresqlDown', module_id 'postgresql2')",
            warning,
        )

    @mock.patch.object(provision_prometheus, "_run_promtool")
    def test_effective_group_collisions_reject_both_custom_updates(self, promtool):
        custom_rule = (
            "groups:\n- name: custom.rules\n  rules:\n"
            "  - alert: LocalAlert\n    expr: up\n"
        )
        redis = FakeRedis({
            "module/metrics1/custom_alerts": {
                "first": custom_rule,
                "second": custom_rule,
            }
        })
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            provision_prometheus.provision(redis)

        self.assertFalse(Path("rules.d/provision_metrics1_first.yml").exists())
        self.assertFalse(Path("rules.d/provision_metrics1_second.yml").exists())
        self.assertIn("effective group name 'custom.rules'", stderr.getvalue())
        promtool.assert_not_called()


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


class ProvisionPrometheusEntrypointTest(unittest.TestCase):
    def test_uses_raw_redis_connection_for_alert_rules(self):
        redis_connect = mock.Mock(side_effect=("decoded-client", "raw-client"))
        fake_agent = types.ModuleType("agent")
        fake_agent.redis_connect = redis_connect
        check_metric_references = mock.Mock()
        fake_alert_rules = types.ModuleType("metrics_alert_rules")
        fake_alert_rules.check_metric_references = check_metric_references
        script = Path(__file__).parents[1] / "imageroot/bin/provision-prometheus"

        with mock.patch.dict(
            sys.modules,
            {"agent": fake_agent, "metrics_alert_rules": fake_alert_rules},
        ), mock.patch.object(
            sys, "argv", [str(script), "--check-metrics"]
        ), self.assertRaises(SystemExit) as raised:
            runpy.run_path(str(script), run_name="__main__")

        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(
            redis_connect.call_args_list,
            [
                mock.call(use_replica=True),
                mock.call(use_replica=True, decode_responses=False),
            ],
        )
        check_metric_references.assert_called_once_with("raw-client")

    def test_enforces_target_and_alertmanager_module_identity(self):
        redis = FakeRedis({
            "module/app1/metrics_targets": {
                "missing": "- targets: [localhost:9000]\n",
                "conflict": (
                    "- targets: [localhost:9001]\n"
                    "  labels:\n"
                    "    module_id: another-app\n"
                    "    custom: preserved\n"
                ),
                "invalid": (
                    "- targets: [localhost:9002]\n"
                    "  labels: not-a-mapping\n"
                ),
            }
        })
        fake_agent = types.ModuleType("agent")
        fake_agent.redis_connect = mock.Mock(side_effect=(redis, redis))
        fake_agent.get_smarthost_settings = mock.Mock(
            return_value={"enabled": False}
        )
        fake_agent.get_hostname = mock.Mock(return_value="node.example.org")
        fake_alert_rules = types.ModuleType("metrics_alert_rules")
        fake_alert_rules.provision = mock.Mock()
        fake_alert_rules.check_metric_references = mock.Mock()
        script = Path(__file__).parents[1] / "imageroot/bin/provision-prometheus"
        stderr = io.StringIO()

        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            sys.modules,
            {"agent": fake_agent, "metrics_alert_rules": fake_alert_rules},
        ), mock.patch.dict(
            os.environ, {"MODULE_ID": "metrics1"}
        ), mock.patch.object(
            sys, "argv", [str(script)]
        ), contextlib.chdir(
            directory
        ), contextlib.redirect_stderr(
            stderr
        ):
            runpy.run_path(str(script), run_name="__main__")

            missing = yaml.safe_load(
                Path("prometheus.d/provision_app1_missing.yml").read_text()
            )
            conflict = yaml.safe_load(
                Path("prometheus.d/provision_app1_conflict.yml").read_text()
            )
            alertmanager = yaml.safe_load(Path("alertmanager.yml").read_text())

            self.assertEqual(missing[0]["labels"]["module_id"], "app1")
            self.assertEqual(missing[0]["labels"]["target_type"], "missing")
            self.assertEqual(conflict[0]["labels"]["module_id"], "app1")
            self.assertEqual(conflict[0]["labels"]["custom"], "preserved")
            self.assertFalse(
                Path("prometheus.d/provision_app1_invalid.yml").exists()
            )
            self.assertEqual(
                alertmanager["route"]["group_by"],
                ["alertname", "node", "module_id"],
            )
            self.assertEqual(
                alertmanager["inhibit_rules"][0]["equal"],
                ["alertname", "module_id"],
            )

        self.assertIn("using authoritative value 'app1'", stderr.getvalue())
        self.assertIn("labels must be a mapping", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
