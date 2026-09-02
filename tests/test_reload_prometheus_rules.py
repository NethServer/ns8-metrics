#!/usr/bin/env python3

#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

import importlib.machinery
import importlib.util
import os
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


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
    loader.exec_module(module)
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

    def test_reads_metrics_from_prefixed_endpoint(self):
        metrics = b"""\
# HELP prometheus_config_last_reload_successful Whether the last reload worked.
prometheus_config_last_reload_successful 1
prometheus_config_last_reload_success_timestamp_seconds 42
"""
        opener = ScriptedOpener(metrics)
        client = reload_rules.PrometheusClient(
            base_url="http://127.0.0.1:9091/prometheus",
            opener=opener,
            timeout=2,
        )

        state = client.reload_state()

        self.assertEqual(
            state,
            reload_rules.ReloadState(successful=1, timestamp=42),
        )
        self.assertEqual(len(opener.requests), 1)
        request, timeout = opener.requests[0]
        self.assertEqual(
            request.full_url,
            "http://127.0.0.1:9091/prometheus/metrics",
        )
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(timeout, 2)


class ReloadCoordinatorTests(unittest.TestCase):
    def run_reload(
        self,
        executor,
        client,
        clock=None,
        warnings=None,
        timeout=3,
    ):
        if clock is None:
            clock = FakeClock()
        if warnings is None:
            warnings = []
        result = reload_rules.reload_prometheus_rules(
            executor=executor,
            client=client,
            warning=warnings.append,
            timeout=timeout,
            poll_interval=1,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
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

        result, warnings, _clock = self.run_reload(
            executor,
            client,
        )

        self.assertEqual(result, "reloaded")
        self.assertEqual(warnings, [])
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

    def test_successful_provisioning_warnings_are_forwarded(self):
        executor = ScriptedExecutor(
            command_result(
                stdout="Skipped module alert rule\nWarning: missing metadata\n"
            ),
            command_result(3),
        )

        result, warnings, _clock = self.run_reload(
            executor, ScriptedClient()
        )

        self.assertEqual(result, "inactive")
        self.assertEqual(warnings, [
            "Skipped module alert rule",
            "Warning: missing metadata",
        ])

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
