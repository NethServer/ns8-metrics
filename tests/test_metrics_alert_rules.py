#!/usr/bin/env python3

#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

import dataclasses
import importlib.machinery
import importlib.util
import os
import pathlib
import subprocess
import sys
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

    def test_classifies_promtool_rejection_as_validation_failure(self):
        def executor(command, **kwargs):
            return types.SimpleNamespace(returncode=1, stdout="parse error")

        runner = metrics_alert_rules.PromtoolRunner("prometheus:v3.5.3", executor)

        with self.assertRaisesRegex(
            metrics_alert_rules.RuleValidationError, "parse error"
        ):
            runner(["--experimental", "promql", "format", "up{"])

    def test_classifies_runtime_exit_as_infrastructure_failure(self):
        def executor(command, **kwargs):
            return types.SimpleNamespace(returncode=125, stdout="image missing")

        runner = metrics_alert_rules.PromtoolRunner("prometheus:v3.5.3", executor)

        with self.assertRaisesRegex(
            metrics_alert_rules.RuleInfrastructureError, "image missing"
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
