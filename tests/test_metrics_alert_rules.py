#!/usr/bin/env python3

#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

import dataclasses
import fnmatch
import importlib.machinery
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


def load_metrics_alert_rules():
    script_path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "imageroot"
        / "bin"
        / "metrics_alert_rules.py"
    )
    loader = importlib.machinery.SourceFileLoader(
        "metrics_alert_rules_under_test", str(script_path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


metrics_alert_rules = load_metrics_alert_rules()


COMPLETE_ANNOTATIONS = """\
annotations:
  summary_en: English summary
  summary_it: Riassunto italiano
  description_en: English description
  description_it: Descrizione italiana
"""

SINGLE_RULE = """\
alert: PostgresqlDown
expr: up == 0
labels:
  severity: critical
  service: postgresql
""" + COMPLETE_ANNOTATIONS


def module_source(payload=SINGLE_RULE, publisher_id="postgresql1", name="availability"):
    return metrics_alert_rules.AlertRuleSource(
        publisher_id=publisher_id,
        rule_set_name=name,
        payload=payload,
        redis_key=f"module/{publisher_id}/metrics_alert_rules",
    )


def ignore_warning(message):
    pass


class ScriptedPromtool:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, arguments):
        self.calls.append(arguments)
        if not self.responses:
            raise AssertionError(f"unexpected promtool call: {arguments!r}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def scripted_rewrite(formatted, matcher_free, scoped, placeholder=None):
    if placeholder is None:
        placeholder = metrics_alert_rules.PLACEHOLDER_LABEL_BASE
    marker = f" [[{placeholder}]]"
    runner = ScriptedPromtool(
        formatted,
        formatted,
        formatted + marker,
        matcher_free + marker,
        scoped + marker,
        scoped,
    )
    runner.placeholder = placeholder
    runner.with_placeholder = formatted + marker
    runner.matcher_free_with_placeholder = matcher_free + marker
    runner.scoped_with_placeholder = scoped + marker
    return runner


class PlaceholderPromtool:
    """Provide deterministic parser responses for document-level tests."""

    def __init__(self):
        self.calls = []

    def __call__(self, arguments):
        self.calls.append(arguments)
        if arguments[2] == "format":
            return arguments[3].strip()
        operation = arguments[3]
        expression = arguments[4]
        label_name = arguments[5]
        if operation == "delete":
            if label_name == "module_id":
                return expression
            marker = f" [[{label_name}={metrics_alert_rules.PLACEHOLDER_VALUE}]]"
            return expression.replace(marker, "")
        if operation == "set":
            value = arguments[6]
            if label_name == "module_id":
                return f"{expression} [module_id={value}]"
            return f"{expression} [[{label_name}={value}]]"
        raise AssertionError(f"unexpected promtool arguments: {arguments!r}")


class ProvisioningPromtool(PlaceholderPromtool):
    def __init__(self, check_error=None):
        super().__init__()
        self.check_error = check_error
        self.checked_candidates = []

    def check_rules(self, candidate_path):
        with open(candidate_path, encoding="utf-8") as stream:
            self.checked_candidates.append(stream.read())
        if self.check_error is not None:
            raise self.check_error
        return "SUCCESS"


class RuleRedis:
    def __init__(
        self,
        hashes=None,
        scan_order=None,
        scan_error=None,
        hgetall_error=None,
    ):
        self.hashes = hashes or {}
        self.scan_order = scan_order
        self.scan_error = scan_error
        self.hgetall_error = hgetall_error
        self.scan_patterns = []
        self.read_keys = []

    def scan_iter(self, pattern):
        self.scan_patterns.append(pattern)
        if self.scan_error is not None:
            raise self.scan_error
        keys = self.scan_order
        if keys is None:
            keys = [
                key
                for key in self.hashes
                if fnmatch.fnmatch(
                    key.decode("utf-8", errors="surrogateescape")
                    if isinstance(key, bytes)
                    else key,
                    pattern,
                )
            ]
        return iter(keys)

    def hgetall(self, key):
        self.read_keys.append(key)
        if self.hgetall_error is not None:
            raise self.hgetall_error
        return self.hashes.get(key, {})


class SourceAndSchemaTests(unittest.TestCase):
    def test_source_record_is_immutable(self):
        source = module_source()

        with self.assertRaises(dataclasses.FrozenInstanceError):
            source.publisher_id = "other1"

    def test_source_exposes_only_provider_rule_identity(self):
        self.assertEqual(
            [
                field.name
                for field in dataclasses.fields(
                    metrics_alert_rules.AlertRuleSource
                )
            ],
            ["publisher_id", "rule_set_name", "payload", "redis_key"],
        )

    def test_generated_filename_validates_identifiers_and_length(self):
        self.assertEqual(
            metrics_alert_rules.generated_rule_filename(
                "postgresql1", "availability.v1"
            ),
            "provision_postgresql1_availability.v1.yml",
        )

        invalid_identifiers = (
            ("", "rule"),
            (".", "rule"),
            ("..", "rule"),
            ("module/escape", "rule"),
            ("module with spaces", "rule"),
            ("postgresql1", ""),
            ("postgresql1", "../rule"),
            ("postgresql1", "règle"),
        )
        for publisher_id, name in invalid_identifiers:
            with self.subTest(publisher_id=publisher_id, name=name):
                with self.assertRaises(metrics_alert_rules.RuleValidationError):
                    metrics_alert_rules.generated_rule_filename(publisher_id, name)

        with self.assertRaisesRegex(
            metrics_alert_rules.RuleValidationError, "exceeds 255 bytes"
        ):
            metrics_alert_rules.generated_rule_filename("p", "x" * 240)

    def test_rejects_invalid_utf8(self):
        source = module_source(payload=b"\xff")

        with self.assertRaisesRegex(
            metrics_alert_rules.RuleValidationError, "not valid UTF-8"
        ):
            metrics_alert_rules.transform_alert_rule(
                source, PlaceholderPromtool(), ignore_warning
            )

    def test_accepts_utf8_bytes(self):
        source = module_source(payload=SINGLE_RULE.encode("utf-8"))

        document = metrics_alert_rules.transform_alert_rule(
            source, PlaceholderPromtool(), ignore_warning
        )

        self.assertEqual(document["groups"][0]["rules"][0]["alert"], "PostgresqlDown")

    def test_rejects_unsupported_document_and_rule_schemas(self):
        invalid_payloads = (
            "groups: [",
            "- not-a-mapping",
            "{}",
            "groups: invalid",
            "groups:\n- invalid",
            "groups:\n- rules: []",
            "groups:\n- name: empty-rules",
            "groups:\n- name: invalid\n  rules:\n  - invalid",
            "groups:\n- name: missing-alert\n  rules:\n  - expr: up",
            "groups:\n- name: missing-expression\n  rules:\n  - alert: MissingExpr",
            "groups:\n- name: record\n  rules:\n  - record: saved_up\n    expr: up",
            "record: saved_up\nexpr: up",
            "groups:\n- name: labels\n  labels: invalid\n  rules: []",
            "groups:\n- name: labels\n  rules:\n  - alert: InvalidLabels\n    expr: up\n    labels: invalid",
            "groups:\n- name: annotations\n  rules:\n  - alert: InvalidAnnotations\n    expr: up\n    annotations: invalid",
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(metrics_alert_rules.RuleValidationError):
                    metrics_alert_rules.transform_alert_rule(
                        module_source(payload=payload),
                        PlaceholderPromtool(),
                        ignore_warning,
                    )

    def test_wraps_non_yaml_parser_failures_as_validation_errors(self):
        with patch.object(
            metrics_alert_rules.yaml,
            "safe_load",
            side_effect=RecursionError("too deeply nested"),
        ):
            with self.assertRaisesRegex(
                metrics_alert_rules.RuleValidationError, "invalid YAML"
            ):
                metrics_alert_rules.transform_alert_rule(
                    module_source(), PlaceholderPromtool(), ignore_warning
                )


class DocumentTransformationTests(unittest.TestCase):
    def test_normalizes_single_module_rule_and_enforces_static_identity(self):
        runner = PlaceholderPromtool()
        warnings = []

        document = metrics_alert_rules.transform_alert_rule(
            module_source(), runner, warnings.append
        )

        group = document["groups"][0]
        rule = group["rules"][0]
        self.assertEqual(group["name"], "ns8:postgresql1:availability")
        self.assertEqual(rule["expr"], "up == 0 [module_id=postgresql1]")
        self.assertEqual(rule["labels"], {
            "severity": "critical",
            "service": "postgresql",
            "module_id": "postgresql1",
        })
        self.assertEqual(warnings, [])

    def test_rewrites_full_group_names_stably_when_reordered(self):
        groups = {
            "availability": "Available",
            "capacity": "CapacityHigh",
        }

        def payload(order):
            lines = ["groups:"]
            for group_name in order:
                lines.extend([
                    f"- name: {group_name}",
                    "  rules:",
                    f"  - alert: {groups[group_name]}",
                    "    expr: up",
                    "    labels:",
                    "      severity: warning",
                    "    annotations:",
                    "      summary_en: English",
                    "      summary_it: Italiano",
                    "      description_en: English",
                    "      description_it: Italiano",
                ])
            return "\n".join(lines) + "\n"

        names = {
            group["name"]
            for group in metrics_alert_rules.transform_alert_rule(
                module_source(payload=payload(("availability", "capacity")), name="all"),
                PlaceholderPromtool(),
                ignore_warning,
            )["groups"]
        }
        reordered_names = {
            group["name"]
            for group in metrics_alert_rules.transform_alert_rule(
                module_source(payload=payload(("capacity", "availability")), name="all"),
                PlaceholderPromtool(),
                ignore_warning,
            )["groups"]
        }

        expected = {
            "ns8:postgresql1:all:availability",
            "ns8:postgresql1:all:capacity",
        }
        self.assertEqual(names, expected)
        self.assertEqual(reordered_names, expected)

    def test_rejects_reserved_duplicate_and_blank_local_group_names(self):
        invalid_payloads = (
            "groups:\n- name: ns8:reserved\n  rules: []\n",
            "groups:\n- name: repeated\n  rules: []\n"
            "- name: ' repeated '\n  rules: []\n",
            "groups:\n- name: '   '\n  rules: []\n",
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(metrics_alert_rules.RuleValidationError):
                    metrics_alert_rules.transform_alert_rule(
                        module_source(payload=payload),
                        PlaceholderPromtool(),
                        ignore_warning,
                    )

    def test_overwrites_group_and_rule_identity_with_location_warnings(self):
        payload = """\
groups:
- name: database
  labels:
    module_id: another1
    environment: production
  rules:
  - alert: DatabaseDown
    expr: up
    labels:
      severity: critical
      module_id: another2
      service: database
""" + "\n".join(f"    {line}" for line in COMPLETE_ANNOTATIONS.splitlines())
        warnings = []

        document = metrics_alert_rules.transform_alert_rule(
            module_source(payload=payload), PlaceholderPromtool(), warnings.append
        )

        group = document["groups"][0]
        rule = group["rules"][0]
        self.assertEqual(group["labels"], {
            "module_id": "postgresql1",
            "environment": "production",
        })
        self.assertEqual(rule["labels"], {
            "severity": "critical",
            "module_id": "postgresql1",
            "service": "database",
        })
        warning_text = "\n".join(warnings)
        self.assertIn("group 'ns8:postgresql1:availability:database'", warning_text)
        self.assertIn("alert 'DatabaseDown'", warning_text)
        self.assertIn("another1", warning_text)
        self.assertIn("another2", warning_text)

    def test_matching_group_and_rule_identity_does_not_warn(self):
        payload = """\
groups:
- name: database
  labels:
    module_id: postgresql1
  rules:
  - alert: DatabaseDown
    expr: up
    labels:
      severity: critical
      module_id: postgresql1
""" + "\n".join(f"    {line}" for line in COMPLETE_ANNOTATIONS.splitlines())
        warnings = []

        document = metrics_alert_rules.transform_alert_rule(
            module_source(payload=payload), PlaceholderPromtool(), warnings.append
        )

        group = document["groups"][0]
        self.assertEqual(group["labels"]["module_id"], "postgresql1")
        self.assertEqual(
            group["rules"][0]["labels"]["module_id"], "postgresql1"
        )
        self.assertEqual(warnings, [])

    def test_warns_without_rejecting_incomplete_metadata(self):
        payload = """\
alert: MetadataWarning
expr: up
labels:
  severity: info
annotations:
  summary_en: English
"""
        warnings = []

        document = metrics_alert_rules.transform_alert_rule(
            module_source(payload=payload), PlaceholderPromtool(), warnings.append
        )

        self.assertEqual(
            document["groups"][0]["rules"][0]["labels"]["severity"],
            "info",
        )
        warning_text = "\n".join(warnings)
        self.assertIn("unsupported severity 'info'", warning_text)
        self.assertIn("description_en", warning_text)
        self.assertIn("description_it", warning_text)
        self.assertIn("summary_it", warning_text)

    def test_warns_when_severity_and_annotations_are_missing(self):
        warnings = []

        metrics_alert_rules.transform_alert_rule(
            module_source(payload="alert: Minimal\nexpr: up\n"),
            PlaceholderPromtool(),
            warnings.append,
        )

        warning_text = "\n".join(warnings)
        self.assertIn("has no severity label", warning_text)
        self.assertIn("summary_en", warning_text)

    def test_same_payload_gets_distinct_publisher_identity(self):
        first = metrics_alert_rules.transform_alert_rule(
            module_source(publisher_id="postgresql1"),
            PlaceholderPromtool(),
            ignore_warning,
        )
        second = metrics_alert_rules.transform_alert_rule(
            module_source(publisher_id="postgresql2"),
            PlaceholderPromtool(),
            ignore_warning,
        )

        self.assertNotEqual(
            first["groups"][0]["name"], second["groups"][0]["name"]
        )
        self.assertEqual(
            first["groups"][0]["rules"][0]["labels"]["module_id"],
            "postgresql1",
        )
        self.assertEqual(
            second["groups"][0]["rules"][0]["labels"]["module_id"],
            "postgresql2",
        )

class PromqlRewriteTests(unittest.TestCase):
    def test_scopes_every_supported_selector_shape(self):
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
                "max(sum(rate(requests_total[5m]))) > 1",
                "max(sum(rate(requests_total[5m]))) > 1",
                "max(sum(rate(requests_total[5m]))) > 1",
                'max(sum(rate(requests_total{module_id="postgresql1"}[5m]))) > 1',
            ),
            (
                "errors_total / requests_total",
                "errors_total / requests_total",
                "errors_total / requests_total",
                'errors_total{module_id="postgresql1"} / '
                'requests_total{module_id="postgresql1"}',
            ),
            (
                "sum by (node) (up)",
                "sum by (node) (up)",
                "sum by (node) (up)",
                'sum by (node) (up{module_id="postgresql1"})',
            ),
            (
                "absent(up)",
                "absent(up)",
                "absent(up)",
                'absent(up{module_id="postgresql1"})',
            ),
            (
                "absent_over_time(up[5m])",
                "absent_over_time(up[5m])",
                "absent_over_time(up[5m])",
                'absent_over_time(up{module_id="postgresql1"}[5m])',
            ),
        )

        for authored, formatted, matcher_free, expected in cases:
            with self.subTest(authored=authored):
                runner = scripted_rewrite(formatted, matcher_free, expected)
                warnings = []

                result = metrics_alert_rules.rewrite_promql_expression(
                    authored, module_source(), runner, warnings.append
                )

                self.assertEqual(result, expected)
                self.assertEqual(warnings, [])
                self.assertEqual(
                    runner.calls,
                    [
                        ["--experimental", "promql", "format", authored],
                        [
                            "--experimental", "promql", "label-matchers",
                            "delete", formatted, runner.placeholder,
                        ],
                        [
                            "--experimental", "promql", "label-matchers",
                            "set", formatted, runner.placeholder,
                            metrics_alert_rules.PLACEHOLDER_VALUE,
                        ],
                        [
                            "--experimental", "promql", "label-matchers",
                            "delete", runner.with_placeholder, "module_id",
                        ],
                        [
                            "--experimental", "promql", "label-matchers",
                            "set", runner.matcher_free_with_placeholder,
                            "module_id", "postgresql1",
                        ],
                        [
                            "--experimental", "promql", "label-matchers",
                            "delete", runner.scoped_with_placeholder,
                            runner.placeholder,
                        ],
                    ],
                )

    def test_exact_matcher_is_reapplied_without_warning(self):
        exact = 'up{module_id="postgresql1"} == 0'
        runner = scripted_rewrite(exact, "up == 0", exact)
        warnings = []

        result = metrics_alert_rules.rewrite_promql_expression(
            exact, module_source(), runner, warnings.append
        )

        self.assertEqual(result, exact)
        self.assertEqual(warnings, [])

    def test_selector_with_only_exact_module_matcher_remains_valid(self):
        exact = 'count({module_id="postgresql1"})'
        runner = scripted_rewrite(exact, "count({})", exact)
        warnings = []

        result = metrics_alert_rules.rewrite_promql_expression(
            exact, module_source(), runner, warnings.append
        )

        self.assertEqual(result, exact)
        self.assertEqual(warnings, [])

    def test_selects_a_placeholder_proven_absent(self):
        formatted = 'up{__ns8_rule_scope="authored"}'
        scoped = (
            'up{__ns8_rule_scope="authored", module_id="postgresql1"}'
        )
        second_placeholder = metrics_alert_rules.PLACEHOLDER_LABEL_BASE + "_1"
        marker = f" [[{second_placeholder}]]"
        runner = ScriptedPromtool(
            formatted,
            "up",
            formatted,
            formatted + marker,
            formatted + marker,
            scoped + marker,
            scoped,
        )

        result = metrics_alert_rules.rewrite_promql_expression(
            formatted, module_source(), runner, ignore_warning
        )

        self.assertEqual(result, scoped)
        self.assertEqual(
            runner.calls[2],
            [
                "--experimental", "promql", "label-matchers", "delete",
                formatted, second_placeholder,
            ],
        )

    def test_rejects_promtool_failure_for_duplicate_authored_matchers(self):
        authored = '{module_id="x", module_id!="y"}'
        placeholder = metrics_alert_rules.PLACEHOLDER_LABEL_BASE
        runner = ScriptedPromtool(
            authored,
            authored,
            f"{authored} [[{placeholder}]]",
            metrics_alert_rules.RuleValidationError("promtool matcher panic"),
        )

        with self.assertRaisesRegex(
            metrics_alert_rules.RuleValidationError, "promtool matcher panic"
        ):
            metrics_alert_rules.rewrite_promql_expression(
                authored, module_source(), runner, ignore_warning
            )

    def test_broader_conflicting_negative_and_mixed_matchers_warn(self):
        cases = (
            'up{module_id="postgresql2"}',
            'up{module_id=~"postgresql.*"}',
            'up{module_id!="postgresql2"}',
            'up{module_id!~"postgresql.*"}',
            'up{module_id="postgresql1"} + errors_total',
            'up{module_id="postgresql1"} + errors_total{module_id="other1"}',
            'count({module_id=~".+"})',
        )

        for authored in cases:
            with self.subTest(authored=authored):
                matcher_free = "up" if not authored.startswith("count") else "count({})"
                scoped = (
                    'up{module_id="postgresql1"}'
                    if not authored.startswith("count")
                    else 'count({module_id="postgresql1"})'
                )
                runner = scripted_rewrite(authored, matcher_free, scoped)
                warnings = []

                result = metrics_alert_rules.rewrite_promql_expression(
                    authored, module_source(), runner, warnings.append
                )

                self.assertEqual(result, scoped)
                self.assertEqual(len(warnings), 1)
                self.assertIn("using publisher ID 'postgresql1'", warnings[0])
                self.assertIn("module/postgresql1/metrics_alert_rules", warnings[0])

    def test_rejects_selector_free_expression(self):
        runner = ScriptedPromtool("vector(1)", "vector(1)", "vector(1)")

        with self.assertRaisesRegex(
            metrics_alert_rules.RuleValidationError,
            "no vector or range selector",
        ):
            metrics_alert_rules.rewrite_promql_expression(
                "vector(1)", module_source(), runner, ignore_warning
            )

    def test_rejects_syntactically_invalid_expression(self):
        runner = ScriptedPromtool(
            metrics_alert_rules.RuleValidationError("parse error")
        )

        with self.assertRaisesRegex(
            metrics_alert_rules.RuleValidationError, "parse error"
        ):
            metrics_alert_rules.rewrite_promql_expression(
                "up{", module_source(), runner, ignore_warning
            )

    def test_propagates_promtool_infrastructure_failures(self):
        runner = ScriptedPromtool(
            metrics_alert_rules.RuleInfrastructureError("podman unavailable")
        )

        with self.assertRaisesRegex(
            metrics_alert_rules.RuleInfrastructureError, "podman unavailable"
        ):
            metrics_alert_rules.rewrite_promql_expression(
                "up", module_source(), runner, ignore_warning
            )


class ModuleRuleProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.rules_directory = pathlib.Path(self.temp_directory.name) / "rules.d"
        self.rules_directory.mkdir()

    def provision(
        self,
        redis_client,
        runner=None,
        transformer=metrics_alert_rules.transform_alert_rule,
    ):
        if runner is None:
            runner = ProvisioningPromtool()
        warnings = []
        metrics_alert_rules.provision_module_alert_rules(
            redis_client,
            rules_directory=self.rules_directory,
            promtool_runner=runner,
            warning=warnings.append,
            transformer=transformer,
        )
        return runner, warnings

    def generated_path(self, publisher_id, rule_set_name):
        filename = metrics_alert_rules.generated_rule_filename(
            publisher_id, rule_set_name
        )
        return self.rules_directory / filename

    def payload(self, alert_name):
        return SINGLE_RULE.replace("PostgresqlDown", alert_name)

    def test_discovers_only_provider_sources_in_deterministic_order(self):
        hashes = {
            "module/zeta1/metrics_alert_rules": {
                "second": self.payload("ZetaSecond"),
                "first": self.payload("ZetaFirst"),
            },
            "module/alpha1/metrics_alert_rules": {
                b"availability": self.payload("AlphaAvailable").encode(),
            },
            "module/metrics1/custom_alerts": {
                "local": self.payload("LocalAlert"),
            },
        }
        redis_client = RuleRedis(
            hashes=hashes,
            scan_order=[
                "module/zeta1/metrics_alert_rules",
                "module/metrics1/custom_alerts",
                "module/alpha1/metrics_alert_rules",
            ],
        )
        warnings = []

        sources = metrics_alert_rules.discover_alert_rule_sources(
            redis_client, warnings.append
        )

        self.assertEqual(
            [
                (source.publisher_id, source.rule_set_name)
                for source in sources
            ],
            [
                ("alpha1", "availability"),
                ("zeta1", "first"),
                ("zeta1", "second"),
            ],
        )
        self.assertEqual(
            redis_client.scan_patterns,
            ["module/*/metrics_alert_rules"],
        )
        self.assertNotIn(
            "module/metrics1/custom_alerts", redis_client.read_keys
        )
        self.assertIn("invalid Redis key shape", "\n".join(warnings))

    def test_isolates_invalid_utf8_and_identifiers(self):
        long_name = "x" * 240
        redis_client = RuleRedis(hashes={
            "module/good1/metrics_alert_rules": {
                "valid": self.payload("ValidAlert"),
                "invalid-payload": b"\xff",
                b"\xff": self.payload("InvalidField"),
                "bad name": self.payload("InvalidName"),
                long_name: self.payload("TooLong"),
            },
            "module/bad publisher/metrics_alert_rules": {
                "valid": self.payload("InvalidPublisher"),
            },
        })

        runner, warnings = self.provision(redis_client)

        self.assertEqual(
            sorted(path.name for path in self.rules_directory.glob("*.yml")),
            ["provision_good1_valid.yml"],
        )
        warning_text = "\n".join(warnings)
        self.assertIn("not valid UTF-8", warning_text)
        self.assertIn("invalid publisher ID", warning_text)
        self.assertIn("invalid rule-set name", warning_text)
        self.assertIn("exceeds 255 bytes", warning_text)
        self.assertEqual(len(runner.checked_candidates), 1)

    def test_rejects_all_sources_in_a_filename_collision(self):
        redis_client = RuleRedis(hashes={
            "module/a_b/metrics_alert_rules": {
                "c": self.payload("FirstAlert"),
            },
            "module/a/metrics_alert_rules": {
                "b_c": self.payload("SecondAlert"),
            },
        })

        runner, warnings = self.provision(redis_client)

        self.assertFalse(self.generated_path("a_b", "c").exists())
        self.assertEqual(runner.calls, [])
        collision_warnings = [
            message for message in warnings if "filename collision" in message
        ]
        self.assertEqual(len(collision_warnings), 2)
        self.assertTrue(
            all("module/a_b/" in message and "module/a/" in message
                for message in collision_warnings)
        )

    def test_rejects_and_retains_effective_group_name_collisions(self):
        redis_client = RuleRedis(hashes={
            "module/first1/metrics_alert_rules": {
                "availability": self.payload("FirstAlert"),
            },
            "module/second1/metrics_alert_rules": {
                "availability": self.payload("SecondAlert"),
            },
        })
        self.provision(redis_client)
        first_path = self.generated_path("first1", "availability")
        second_path = self.generated_path("second1", "availability")
        previous = {
            first_path: first_path.read_bytes(),
            second_path: second_path.read_bytes(),
        }

        def colliding_transformer(source, runner, warning):
            document = metrics_alert_rules.transform_alert_rule(
                source, runner, warning
            )
            document["groups"][0]["name"] = "ns8:forced:collision"
            return document

        _runner, warnings = self.provision(
            redis_client, transformer=colliding_transformer
        )

        self.assertEqual(first_path.read_bytes(), previous[first_path])
        self.assertEqual(second_path.read_bytes(), previous[second_path])
        collision_warnings = [
            message for message in warnings
            if "group-name collision" in message
        ]
        self.assertEqual(len(collision_warnings), 2)

    def test_retains_only_an_exact_source_previous_valid_file(self):
        first_redis = RuleRedis(hashes={
            "module/a_b/metrics_alert_rules": {
                "c": self.payload("InitialAlert"),
            },
        })
        self.provision(first_redis)
        output_path = self.generated_path("a_b", "c")
        previous = output_path.read_bytes()
        self.assertEqual(
            metrics_alert_rules._read_rule_owner(output_path),
            ("module/a_b/metrics_alert_rules", "c"),
        )
        self.assertTrue(previous.startswith(
            b'# ns8-metrics-source: {"field":"c",'
        ))

        invalid_redis = RuleRedis(hashes={
            "module/a_b/metrics_alert_rules": {"c": "groups: [\n"},
        })
        self.provision(invalid_redis)
        self.assertEqual(output_path.read_bytes(), previous)

        rejected_runner = ProvisioningPromtool(
            metrics_alert_rules.RuleValidationError("invalid rule file")
        )
        updated_redis = RuleRedis(hashes={
            "module/a_b/metrics_alert_rules": {
                "c": self.payload("RejectedUpdate"),
            },
        })
        self.provision(updated_redis, runner=rejected_runner)
        self.assertEqual(output_path.read_bytes(), previous)

        colliding_identity = RuleRedis(hashes={
            "module/a/metrics_alert_rules": {"b_c": "groups: [\n"},
        })
        self.provision(colliding_identity)
        self.assertFalse(output_path.exists())

    def test_replaces_a_valid_candidate_atomically(self):
        redis_client = RuleRedis(hashes={
            "module/app1/metrics_alert_rules": {
                "availability": self.payload("InitialAlert"),
            },
        })
        self.provision(redis_client)
        output_path = self.generated_path("app1", "availability")
        previous = output_path.read_bytes()
        updated_redis = RuleRedis(hashes={
            "module/app1/metrics_alert_rules": {
                "availability": self.payload("UpdatedAlert"),
            },
        })
        real_replace = os.replace
        observations = []

        def observe_replace(source, destination):
            observations.append({
                "destination_before": pathlib.Path(destination).read_bytes(),
                "candidate": pathlib.Path(source).read_bytes(),
            })
            real_replace(source, destination)

        with patch.object(
            metrics_alert_rules.os, "replace", side_effect=observe_replace
        ):
            self.provision(updated_redis)

        self.assertEqual(observations[0]["destination_before"], previous)
        self.assertIn(b"UpdatedAlert", observations[0]["candidate"])
        self.assertIn(b"UpdatedAlert", output_path.read_bytes())
        self.assertNotEqual(output_path.read_bytes(), previous)

    def test_valid_and_invalid_provider_updates_are_independent(self):
        initial_redis = RuleRedis(hashes={
            "module/app1/metrics_alert_rules": {
                "first": self.payload("FirstAlert"),
                "second": self.payload("SecondAlert"),
            },
        })
        self.provision(initial_redis)
        first_path = self.generated_path("app1", "first")
        second_path = self.generated_path("app1", "second")
        first_previous = first_path.read_bytes()

        updated_redis = RuleRedis(hashes={
            "module/app1/metrics_alert_rules": {
                "first": "groups: [\n",
                "second": self.payload("SecondAlertUpdated"),
                "third": b"\xff",
            },
        })
        self.provision(updated_redis)

        self.assertEqual(first_path.read_bytes(), first_previous)
        self.assertIn(b"SecondAlertUpdated", second_path.read_bytes())
        self.assertFalse(self.generated_path("app1", "third").exists())

    def test_cleanup_removes_only_stale_generated_module_rules(self):
        built_in = self.rules_directory / "nodes.yml"
        custom = self.rules_directory / "custom.yml"
        built_in.write_bytes(b"built-in\n")
        custom.write_bytes(b"legacy-custom\n")
        redis_client = RuleRedis(hashes={
            "module/app1/metrics_alert_rules": {
                "availability": self.payload("Available"),
            },
        })
        self.provision(redis_client)
        generated = self.generated_path("app1", "availability")
        self.assertTrue(generated.exists())
        orphan = self.rules_directory / "provision_orphan_rule.yml"
        orphan.write_bytes(b"unowned\n")

        self.provision(RuleRedis())

        self.assertFalse(generated.exists())
        self.assertFalse(orphan.exists())
        self.assertEqual(built_in.read_bytes(), b"built-in\n")
        self.assertEqual(custom.read_bytes(), b"legacy-custom\n")

    def test_propagates_redis_image_filesystem_and_container_failures(self):
        with self.assertRaisesRegex(
            metrics_alert_rules.RuleInfrastructureError,
            "cannot discover.*Redis",
        ):
            self.provision(RuleRedis(scan_error=OSError("redis offline")))

        unreadable_redis = RuleRedis(
            hashes={
                "module/app1/metrics_alert_rules": {
                    "availability": self.payload("Available"),
                },
            },
            hgetall_error=OSError("redis read failed"),
        )
        with self.assertRaisesRegex(
            metrics_alert_rules.RuleInfrastructureError,
            "cannot read module alert-rule hash",
        ):
            self.provision(unreadable_redis)

        redis_client = RuleRedis(hashes={
            "module/app1/metrics_alert_rules": {
                "availability": self.payload("Available"),
            },
        })
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                metrics_alert_rules.RuleInfrastructureError,
                "PROMETHEUS_IMAGE is not set",
            ):
                metrics_alert_rules.provision_module_alert_rules(
                    redis_client,
                    rules_directory=self.rules_directory,
                    warning=ignore_warning,
                )

        with patch.object(
            metrics_alert_rules.tempfile,
            "mkstemp",
            side_effect=OSError("read-only filesystem"),
        ):
            with self.assertRaisesRegex(
                metrics_alert_rules.RuleInfrastructureError,
                "read-only filesystem",
            ):
                self.provision(redis_client)

        failing_runner = ProvisioningPromtool(
            metrics_alert_rules.RuleInfrastructureError(
                "container unavailable"
            )
        )
        with self.assertRaisesRegex(
            metrics_alert_rules.RuleInfrastructureError,
            "container unavailable",
        ):
            self.provision(redis_client, runner=failing_runner)

    def test_warns_for_duplicate_installed_module_identities_only(self):
        redis_client = RuleRedis(hashes={
            "module/app1/metrics_alert_rules": {
                "first": self.payload("SharedAlert"),
                "second": self.payload("SharedAlert"),
            },
            "module/app2/metrics_alert_rules": {
                "first": self.payload("SharedAlert"),
            },
        })

        _runner, warnings = self.provision(redis_client)

        duplicate_warnings = [
            message for message in warnings
            if "duplicate installed module alert identity" in message
        ]
        self.assertEqual(len(duplicate_warnings), 1)
        self.assertIn("alertname='SharedAlert'", duplicate_warnings[0])
        self.assertIn("module_id='app1'", duplicate_warnings[0])
        self.assertNotIn("module_id='app2'", duplicate_warnings[0])


class PromtoolRunnerTests(unittest.TestCase):
    def test_runs_pinned_image_networkless_without_a_shell(self):
        calls = []

        def executor(command, **kwargs):
            calls.append((command, kwargs))
            return types.SimpleNamespace(returncode=0, stdout="formatted\n")

        runner = metrics_alert_rules.PromtoolRunner(
            image="quay.io/prometheus/prometheus:v3.5.3",
            executor=executor,
        )
        expression = 'up{job="$(not-a-shell)"}'

        output = runner(["--experimental", "promql", "format", expression])

        self.assertEqual(output, "formatted")
        command, kwargs = calls[0]
        self.assertEqual(command, [
            "/usr/bin/podman",
            "run",
            "--rm",
            "--network=none",
            "--entrypoint=/bin/promtool",
            "quay.io/prometheus/prometheus:v3.5.3",
            "--experimental",
            "promql",
            "format",
            expression,
        ])
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["check"], False)
        self.assertEqual(kwargs["stderr"], subprocess.STDOUT)

    def test_requires_declared_prometheus_image(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                metrics_alert_rules.RuleInfrastructureError,
                "PROMETHEUS_IMAGE is not set",
            ):
                metrics_alert_rules.PromtoolRunner()

    def test_checks_candidate_through_a_shared_read_only_networkless_mount(self):
        calls = []

        def executor(command, **kwargs):
            calls.append(command)
            return types.SimpleNamespace(returncode=0, stdout="SUCCESS\n")

        runner = metrics_alert_rules.PromtoolRunner(
            image="quay.io/prometheus/prometheus:v3.5.3",
            executor=executor,
        )
        with tempfile.NamedTemporaryFile() as candidate:
            output = runner.check_rules(candidate.name)
            candidate_path = os.path.abspath(candidate.name)

        self.assertEqual(output, "SUCCESS")
        self.assertEqual(calls[0], [
            "/usr/bin/podman",
            "run",
            "--rm",
            "--network=none",
            "--volume",
            f"{candidate_path}:/tmp/ns8-module-rules.yml:ro,z",
            "--entrypoint=/bin/promtool",
            "quay.io/prometheus/prometheus:v3.5.3",
            "check",
            "rules",
            "/tmp/ns8-module-rules.yml",
        ])

    def test_classifies_promtool_rejection_as_validation_failure(self):
        def executor(command, **kwargs):
            return types.SimpleNamespace(returncode=1, stdout="parse error")

        runner = metrics_alert_rules.PromtoolRunner("prometheus:v3.5.3", executor)

        with self.assertRaisesRegex(
            metrics_alert_rules.RuleValidationError, "parse error"
        ):
            runner(["--experimental", "promql", "format", "up{"])

    def test_classifies_podman_status_one_as_infrastructure_failure(self):
        def executor(command, **kwargs):
            return types.SimpleNamespace(
                returncode=1,
                stdout="Failed to obtain podman configuration: denied",
            )

        runner = metrics_alert_rules.PromtoolRunner(
            "prometheus:v3.5.3", executor
        )

        with self.assertRaisesRegex(
            metrics_alert_rules.RuleInfrastructureError,
            "podman configuration",
        ):
            runner(["--version"])

    def test_classifies_candidate_read_failure_as_infrastructure_failure(self):
        def executor(command, **kwargs):
            return types.SimpleNamespace(
                returncode=1,
                stdout=(
                    "FAILED: open /tmp/ns8-module-rules.yml: "
                    "permission denied"
                ),
            )

        runner = metrics_alert_rules.PromtoolRunner(
            "prometheus:v3.5.3", executor
        )

        with self.assertRaisesRegex(
            metrics_alert_rules.RuleInfrastructureError,
            "permission denied",
        ):
            runner.check_rules("candidate.yml")

    def test_classifies_runtime_exit_as_infrastructure_failure(self):
        for returncode, output in (
            (125, "image missing"),
            (137, "container killed"),
        ):
            with self.subTest(returncode=returncode):
                def executor(command, **kwargs):
                    return types.SimpleNamespace(
                        returncode=returncode, stdout=output
                    )

                runner = metrics_alert_rules.PromtoolRunner(
                    "prometheus:v3.5.3", executor
                )

                with self.assertRaisesRegex(
                    metrics_alert_rules.RuleInfrastructureError, output
                ):
                    runner(["--version"])

    def test_classifies_process_start_failure_as_infrastructure_failure(self):
        def executor(command, **kwargs):
            raise OSError("podman missing")

        runner = metrics_alert_rules.PromtoolRunner("prometheus:v3.5.3", executor)

        with self.assertRaisesRegex(
            metrics_alert_rules.RuleInfrastructureError, "podman missing"
        ):
            runner(["--version"])


if __name__ == "__main__":
    unittest.main()
