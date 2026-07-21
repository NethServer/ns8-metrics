#!/usr/bin/env python3

#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

import importlib.machinery
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import yaml


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN_DIRECTORY = REPOSITORY_ROOT / "imageroot" / "bin"


def load_reload_prometheus_rules():
    script_path = BIN_DIRECTORY / "reload-prometheus-rules"
    loader = importlib.machinery.SourceFileLoader(
        "reload_prometheus_rules_under_test", str(script_path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    sys.path.insert(0, str(BIN_DIRECTORY))
    try:
        loader.exec_module(module)
    finally:
        sys.path.remove(str(BIN_DIRECTORY))
    return module


reload_rules = load_reload_prometheus_rules()


def command_result(returncode=0, stdout=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout)


class ScriptedExecutor:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if not self.results:
            raise AssertionError(f"unexpected command: {command!r}")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeClock:
    def __init__(self):
        self.value = 0
        self.sleeps = []

    def monotonic(self):
        return self.value

    def sleep(self, duration):
        self.sleeps.append(duration)
        self.value += duration


class ScriptedClient:
    def __init__(self, *states):
        self.states = list(states)
        self.reload_calls = 0

    def reload_state(self):
        self.reload_calls += 1
        if not self.states:
            raise AssertionError("unexpected reload-state request")
        state = self.states.pop(0)
        if isinstance(state, Exception):
            raise state
        return state


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.payload


class ScriptedOpener:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.payloads:
            raise AssertionError(f"unexpected HTTP request: {request.full_url}")
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(payload)


class PrometheusClientTests(unittest.TestCase):
    def test_builds_normalized_local_base_urls(self):
        self.assertEqual(
            reload_rules.prometheus_base_url(""),
            "http://127.0.0.1:9091",
        )
        self.assertEqual(
            reload_rules.prometheus_base_url("/prometheus/nested/"),
            "http://127.0.0.1:9091/prometheus/nested",
        )
        self.assertEqual(
            reload_rules.prometheus_base_url("metrics path"),
            "http://127.0.0.1:9091/metrics%20path",
        )
        with patch.dict(os.environ, {"PROMETHEUS_PATH": "from-env"}):
            self.assertEqual(
                reload_rules.prometheus_base_url(),
                "http://127.0.0.1:9091/from-env",
            )

    def test_reads_metrics_and_prefixed_api_endpoints(self):
        metrics = b"""\
# HELP prometheus_config_last_reload_successful Whether the last reload worked.
prometheus_config_last_reload_successful 1
prometheus_config_last_reload_success_timestamp_seconds 42
"""
        ast = {
            "status": "success",
            "data": {"type": "vectorSelector", "name": "up"},
        }
        names = {"status": "success", "data": ["up", "node_load1"]}
        opener = ScriptedOpener(
            metrics,
            json.dumps(ast).encode(),
            json.dumps(names).encode(),
        )
        client = reload_rules.PrometheusClient(
            base_url="http://127.0.0.1:9091/prometheus",
            opener=opener,
            timeout=2,
        )

        state = client.reload_state()
        parsed = client.parse_query("up == 0")
        known_names = client.metric_names()

        self.assertEqual(
            state,
            reload_rules.ReloadState(successful=1, timestamp=42),
        )
        self.assertEqual(parsed["name"], "up")
        self.assertEqual(known_names, {"up", "node_load1"})
        requests = [item[0] for item in opener.requests]
        self.assertEqual(
            requests[0].full_url,
            "http://127.0.0.1:9091/prometheus/metrics",
        )
        self.assertEqual(requests[0].method, "GET")
        self.assertEqual(
            requests[1].full_url,
            "http://127.0.0.1:9091/prometheus/api/v1/parse_query",
        )
        self.assertEqual(requests[1].method, "POST")
        self.assertEqual(
            requests[1].data,
            b"query=up+%3D%3D+0",
        )
        self.assertEqual(
            requests[2].full_url,
            "http://127.0.0.1:9091/prometheus/api/v1/label/__name__/values",
        )
        self.assertEqual([item[1] for item in opener.requests], [2, 2, 2])

    def test_rejects_invalid_or_unsuccessful_api_responses(self):
        cases = (
            b"not-json",
            b'{"status":"error","error":"unsupported"}',
            b'{"status":"success"}',
            b'{"status":"success","data":"unsupported"}',
        )
        for payload in cases:
            with self.subTest(payload=payload):
                client = reload_rules.PrometheusClient(
                    opener=ScriptedOpener(payload)
                )
                with self.assertRaises(reload_rules.AdvisoryCheckError):
                    client.parse_query("up")


class ReloadCoordinatorTests(unittest.TestCase):
    def run_reload(
        self,
        executor,
        client,
        clock=None,
        warnings=None,
        advisory_checker=None,
        timeout=3,
    ):
        if clock is None:
            clock = FakeClock()
        if warnings is None:
            warnings = []
        if advisory_checker is None:
            advisory_checker = lambda: None
        result = reload_rules.reload_prometheus_rules(
            executor=executor,
            client=client,
            warning=warnings.append,
            timeout=timeout,
            poll_interval=1,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            advisory_checker=advisory_checker,
        )
        return result, warnings, clock

    def test_successful_reload_does_not_restart_the_service(self):
        executor = ScriptedExecutor(
            command_result(),
            command_result(),
            command_result(),
        )
        client = ScriptedClient(
            reload_rules.ReloadState(1, 10),
            reload_rules.ReloadState(1, 11),
        )
        advisory_calls = []

        result, warnings, _clock = self.run_reload(
            executor,
            client,
            advisory_checker=lambda: advisory_calls.append(True),
        )

        self.assertEqual(result, "reloaded")
        self.assertEqual(warnings, [])
        self.assertEqual(advisory_calls, [True])
        commands = [call[0] for call in executor.calls]
        self.assertEqual(commands[0], ["provision-prometheus"])
        self.assertIn("reload", commands[2])
        self.assertFalse(any("try-restart" in command for command in commands))
        self.assertTrue(all(call[1]["shell"] is False for call in executor.calls))

    def test_inactive_service_is_left_stopped(self):
        executor = ScriptedExecutor(command_result(), command_result(3))
        client = ScriptedClient()

        result, warnings, _clock = self.run_reload(executor, client)

        self.assertEqual(result, "inactive")
        self.assertEqual(warnings, [])
        self.assertEqual(client.reload_calls, 0)
        self.assertEqual(len(executor.calls), 2)

    def test_missing_pre_reload_timestamp_uses_restart_fallback(self):
        executor = ScriptedExecutor(
            command_result(),
            command_result(),
            command_result(),
        )
        client = ScriptedClient(
            reload_rules.ReloadState(1, None),
            reload_rules.ReloadState(1, None),
        )

        result, warnings, _clock = self.run_reload(executor, client)

        self.assertEqual(result, "restarted")
        commands = [call[0] for call in executor.calls]
        self.assertFalse(any("reload" in command for command in commands))
        self.assertTrue(any("try-restart" in command for command in commands))
        self.assertIn("timestamp is missing", "\n".join(warnings))

    def test_reload_timeout_falls_back_to_a_verified_restart(self):
        executor = ScriptedExecutor(
            command_result(),
            command_result(),
            command_result(),
            command_result(),
        )
        client = ScriptedClient(
            reload_rules.ReloadState(1, 10),
            reload_rules.ReloadState(1, 10),
            reload_rules.ReloadState(1, 10),
            reload_rules.ReloadState(1, 10),
            reload_rules.ReloadState(1, 11),
        )

        result, warnings, clock = self.run_reload(
            executor, client, timeout=2
        )

        self.assertEqual(result, "restarted")
        self.assertEqual(clock.sleeps, [1, 1])
        self.assertIn("timestamp did not advance", "\n".join(warnings))
        commands = [call[0] for call in executor.calls]
        self.assertTrue(any("reload" in command for command in commands))
        self.assertTrue(any("try-restart" in command for command in commands))

    def test_failed_reload_command_uses_restart_fallback(self):
        executor = ScriptedExecutor(
            command_result(),
            command_result(),
            command_result(1, "reload rejected"),
            command_result(),
        )
        client = ScriptedClient(
            reload_rules.ReloadState(1, 10),
            reload_rules.ReloadState(1, 11),
        )

        result, warnings, _clock = self.run_reload(executor, client)

        self.assertEqual(result, "restarted")
        self.assertIn("reload rejected", "\n".join(warnings))

    def test_failed_restart_or_verification_returns_an_error(self):
        restart_failure = ScriptedExecutor(
            command_result(),
            command_result(),
            command_result(1, "restart failed"),
        )
        missing_timestamp = ScriptedClient(
            reload_rules.ReloadState(1, None)
        )
        with self.assertRaisesRegex(
            reload_rules.RuleReloadError, "restart failed"
        ):
            self.run_reload(restart_failure, missing_timestamp)

        verification_failure = ScriptedExecutor(
            command_result(),
            command_result(),
            command_result(),
        )
        client = ScriptedClient(
            reload_rules.ReloadState(1, None),
            reload_rules.ReloadState(0, None),
            reload_rules.ReloadState(0, None),
            reload_rules.ReloadState(0, None),
        )
        with self.assertRaisesRegex(
            reload_rules.RuleReloadError, "could not be verified"
        ):
            self.run_reload(verification_failure, client, timeout=2)

    def test_provision_and_service_state_failures_propagate(self):
        provision_failure = ScriptedExecutor(
            command_result(1, "provision failed")
        )
        with self.assertRaisesRegex(
            reload_rules.RuleReloadError, "provision failed"
        ):
            self.run_reload(provision_failure, ScriptedClient())

        state_failure = ScriptedExecutor(
            command_result(),
            command_result(4, "unit unknown"),
        )
        with self.assertRaisesRegex(
            reload_rules.RuleReloadError,
            "cannot determine whether Prometheus is active",
        ):
            self.run_reload(state_failure, ScriptedClient())

    def test_advisory_failure_does_not_fail_a_verified_reload(self):
        executor = ScriptedExecutor(
            command_result(),
            command_result(),
            command_result(),
        )
        client = ScriptedClient(
            reload_rules.ReloadState(1, 10),
            reload_rules.ReloadState(1, 11),
        )

        result, warnings, _clock = self.run_reload(
            executor,
            client,
            advisory_checker=lambda: (_ for _ in ()).throw(
                RuntimeError("advisory unavailable")
            ),
        )

        self.assertEqual(result, "reloaded")
        self.assertIn("advisory unavailable", "\n".join(warnings))


class MetricReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.rules_directory = pathlib.Path(self.temp_directory.name) / "rules.d"
        self.rules_directory.mkdir()

    def write_provider_rule(
        self,
        publisher_id,
        rule_set_name,
        alert_name,
        expression,
    ):
        redis_key = f"module/{publisher_id}/metrics_alert_rules"
        source = reload_rules.metrics_alert_rules.AlertRuleSource(
            publisher_id=publisher_id,
            rule_set_name=rule_set_name,
            payload="",
            redis_key=redis_key,
        )
        document = {
            "groups": [
                {
                    "name": f"ns8:{publisher_id}:{rule_set_name}",
                    "rules": [
                        {
                            "alert": alert_name,
                            "expr": expression,
                            "labels": {"module_id": publisher_id},
                        }
                    ],
                }
            ]
        }
        path = self.rules_directory / source.filename
        path.write_text(
            reload_rules.metrics_alert_rules._ownership_header(source)
            + yaml.safe_dump(document, sort_keys=False)
        )
        return path

    def test_collects_static_names_and_ignores_absent_and_dynamic_selectors(self):
        ast = {
            "type": "binaryExpr",
            "lhs": {"type": "vectorSelector", "name": "up"},
            "rhs": {
                "type": "call",
                "func": {"name": "absent"},
                "args": [
                    {"type": "vectorSelector", "name": "missing_ignored"}
                ],
            },
            "extra": [
                {
                    "type": "matrixSelector",
                    "name": "requests_total",
                    "matchers": [
                        {
                            "name": "__name__",
                            "type": "=",
                            "value": "requests_total",
                        }
                    ],
                },
                {
                    "type": "vectorSelector",
                    "name": "",
                    "matchers": [
                        {
                            "name": "__name__",
                            "type": "=",
                            "value": "exact_metric",
                        }
                    ],
                },
                {
                    "type": "vectorSelector",
                    "name": "",
                    "matchers": [
                        {
                            "name": "__name__",
                            "type": "=~",
                            "value": ".+",
                        }
                    ],
                },
                {
                    "type": "call",
                    "func": "absent_over_time",
                    "args": [
                        {"type": "vectorSelector", "name": "also_ignored"}
                    ],
                },
            ],
        }

        names = reload_rules.collect_static_metric_names(ast)

        self.assertEqual(
            names,
            {"up", "requests_total", "exact_metric"},
        )

    def test_warns_for_unknown_metrics_with_exact_source_context(self):
        self.write_provider_rule(
            "app1",
            "availability",
            "ApplicationDown",
            "up + missing_metric",
        )
        custom_path = self.rules_directory / "custom.yml"
        custom_path.write_text(
            "groups:\n- name: Custom\n  rules:\n"
            "  - alert: Local\n    expr: custom_missing\n"
        )

        class Client:
            def __init__(self):
                self.metric_name_calls = 0

            def parse_query(self, expression):
                self.assert_expression = expression
                return {
                    "type": "binaryExpr",
                    "lhs": {"type": "vectorSelector", "name": "up"},
                    "rhs": {
                        "type": "vectorSelector",
                        "name": "missing_metric",
                    },
                }

            def metric_names(self):
                self.metric_name_calls += 1
                return {"up"}

        client = Client()
        warnings = []

        reload_rules.check_metric_references(
            client,
            rules_directory=self.rules_directory,
            warning=warnings.append,
        )

        self.assertEqual(client.assert_expression, "up + missing_metric")
        self.assertEqual(client.metric_name_calls, 1)
        warning_text = "\n".join(warnings)
        self.assertIn("unknown metric 'missing_metric'", warning_text)
        self.assertIn("module/app1/metrics_alert_rules", warning_text)
        self.assertIn("field 'availability'", warning_text)
        self.assertIn("alert 'ApplicationDown'", warning_text)
        self.assertNotIn("custom_missing", warning_text)

    def test_parser_failures_are_isolated_and_known_names_are_fetched_once(self):
        self.write_provider_rule(
            "app1", "broken", "Broken", "broken_expression"
        )
        self.write_provider_rule(
            "app2", "valid", "Valid", "known + unknown"
        )

        class Client:
            metric_name_calls = 0

            def parse_query(self, expression):
                if expression == "broken_expression":
                    raise reload_rules.AdvisoryCheckError("unsupported parser")
                return {
                    "type": "binaryExpr",
                    "lhs": {"type": "vectorSelector", "name": "known"},
                    "rhs": {"type": "vectorSelector", "name": "unknown"},
                }

            def metric_names(self):
                self.metric_name_calls += 1
                return {"known"}

        client = Client()
        warnings = []

        reload_rules.check_metric_references(
            client,
            rules_directory=self.rules_directory,
            warning=warnings.append,
        )

        warning_text = "\n".join(warnings)
        self.assertIn("unsupported parser", warning_text)
        self.assertIn("unknown metric 'unknown'", warning_text)
        self.assertEqual(client.metric_name_calls, 1)

    def test_dynamic_empty_and_unavailable_name_sets_fail_open(self):
        self.write_provider_rule(
            "app1", "dynamic", "Dynamic", '{__name__=~".+"}'
        )

        class DynamicClient:
            def parse_query(self, expression):
                return {
                    "type": "vectorSelector",
                    "name": "",
                    "matchers": [
                        {"name": "__name__", "type": "=~", "value": ".+"}
                    ],
                }

            def metric_names(self):
                raise AssertionError("metric names must not be fetched")

        warnings = []
        reload_rules.check_metric_references(
            DynamicClient(), self.rules_directory, warnings.append
        )
        self.assertIn("no static metric names", "\n".join(warnings))

        class EmptyClient:
            def parse_query(self, expression):
                return {"type": "vectorSelector", "name": "up"}

            def metric_names(self):
                return set()

        warnings = []
        reload_rules.check_metric_references(
            EmptyClient(), self.rules_directory, warnings.append
        )
        self.assertIn("empty metric-name set", "\n".join(warnings))

        class UnavailableClient(EmptyClient):
            def metric_names(self):
                raise OSError("API unavailable")

        warnings = []
        reload_rules.check_metric_references(
            UnavailableClient(), self.rules_directory, warnings.append
        )
        self.assertIn("API unavailable", "\n".join(warnings))


class EventAndUnitTests(unittest.TestCase):
    def test_systemd_reload_uses_the_prometheus_cidfile(self):
        service = (
            REPOSITORY_ROOT
            / "imageroot"
            / "systemd"
            / "user"
            / "prometheus.service"
        ).read_text()

        self.assertIn(
            "ExecReload=/usr/bin/podman kill --signal HUP "
            "--cidfile %t/prometheus.ctr-id",
            service,
        )

    def test_rule_and_module_removal_events_use_the_reload_coordinator(self):
        for event_name in (
            "metrics-alert-rules-changed",
            "module-removed",
        ):
            with self.subTest(event_name=event_name):
                handler = (
                    REPOSITORY_ROOT
                    / "imageroot"
                    / "events"
                    / event_name
                    / "15handler"
                )
                self.assertTrue(handler.stat().st_mode & 0o111)
                self.assertIn(
                    "exec reload-prometheus-rules", handler.read_text()
                )

        target_handler = (
            REPOSITORY_ROOT
            / "imageroot"
            / "events"
            / "metrics-target-changed"
            / "15handler"
        ).read_text()
        self.assertIn("provision-prometheus", target_handler)
        self.assertNotIn("reload-prometheus-rules", target_handler)


if __name__ == "__main__":
    unittest.main()
