#!/usr/bin/env python3

#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

import copy
import importlib.machinery
import importlib.util
import io
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, patch


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_alert_proxy():
    script_path = REPOSITORY_ROOT / "alert-proxy" / "alert-proxy"
    loader = importlib.machinery.SourceFileLoader(
        "alert_proxy_under_test", str(script_path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


alert_proxy = load_alert_proxy()


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class AlertProxyRequestTests(unittest.IsolatedAsyncioTestCase):
    async def dispatch(self, payload, mimir_url=""):
        forward = AsyncMock()
        raise_alert = AsyncMock()
        with (
            patch.object(alert_proxy, "mimir_url", mimir_url),
            patch.object(alert_proxy, "forward_to_mimir", forward),
            patch.object(alert_proxy, "raise_alert", raise_alert),
            patch.object(alert_proxy.sys, "stderr", io.StringIO()),
        ):
            response = await alert_proxy.handle_post_request(
                FakeRequest(payload)
            )
        return response, forward, raise_alert

    async def deliver(self, labels, status="firing", provider="nsent", token="test-token"):
        payload = {"alerts": [{"status": status, "labels": labels}]}
        original = copy.deepcopy(payload)
        forward = AsyncMock()
        send = AsyncMock()
        log = io.StringIO()
        with (
            patch.object(alert_proxy, "mimir_url", "https://mimir.example"),
            patch.object(alert_proxy, "forward_to_mimir", forward),
            patch.object(alert_proxy, "send_alert", send),
            patch.object(alert_proxy, "auth_token", token),
            patch.object(alert_proxy, "alert_provider", provider),
            patch.object(alert_proxy, "dartagnan_url", "https://community.example"),
            patch.object(alert_proxy.sys, "stderr", log),
        ):
            response = await alert_proxy.handle_post_request(FakeRequest(payload))
        self.assertEqual(response.status, 200)
        forward.assert_awaited_once_with(original["alerts"])
        self.assertEqual(payload, original)
        return send, log.getvalue()

    async def test_module_load_alerts_reach_both_portals(self):
        for provider, url in (
            ("nsent", "https://my.nethesis.it/isa/alerts/store"),
            ("nscom", "https://community.example/machine/alerts/store"),
        ):
            for status, value in (
                ("firing", alert_proxy.CRITICAL),
                ("resolved", alert_proxy.CLEAR),
            ):
                for name, expected_id in (
                    ("LoadQueueHigh", "loadqueuehigh:postgresql1:node:2"),
                    ("Load_Queue_High", "load-queue-high:postgresql1:node:2"),
                ):
                    with self.subTest(provider=provider, status=status, name=name):
                        send, log = await self.deliver(
                            {
                                "alertname": name,
                                "module_id": "postgresql1",
                                "node": "2",
                                "severity": "warning",
                            },
                            status=status,
                            provider=provider,
                        )
                        send.assert_awaited_once_with(url, value, expected_id)
                        self.assertNotIn("NOTICE", log)

    async def test_module_less_load_alerts_keep_legacy_filtering(self):
        identities = (
            {},
            {"module_id": ""},
            {"module_id": None},
            {"module_id": 123},
            {"module_id": ["postgresql1"]},
            {"module_id": {"id": "postgresql1"}},
        )
        for identity in identities:
            for status in ("firing", "resolved"):
                with self.subTest(identity=identity, status=status):
                    send, log = await self.deliver(
                        {"alertname": "LoadQueueHigh", "node": "2", **identity},
                        status=status,
                    )
                    send.assert_not_awaited()
                    self.assertIn("NOTICE", log)

    async def test_module_load_alerts_still_require_portal_credentials(self):
        send, log = await self.deliver(
            {"alertname": "LoadQueueHigh", "module_id": "postgresql1", "node": "2"},
            token=None,
        )
        send.assert_not_awaited()
        self.assertIn("No auth token, alert not sent", log)

    async def test_module_less_non_load_alerts_still_reach_portal(self):
        send, _log = await self.deliver(
            {"alertname": "Application_Down", "node": "2"}
        )
        send.assert_awaited_once_with(
            "https://my.nethesis.it/isa/alerts/store",
            alert_proxy.CRITICAL,
            "application-down:node:2",
        )

    async def test_generic_identifier_includes_only_a_nonempty_module_id(self):
        cases = (
            (
                {"alertname": "Application_Down", "node": "2"},
                "application-down:node:2",
            ),
            (
                {
                    "alertname": "Application_Down",
                    "node": "2",
                    "module_id": "",
                },
                "application-down:node:2",
            ),
            (
                {
                    "alertname": "Application_Down",
                    "node": "2",
                    "module_id": "postgresql1",
                },
                "application-down:postgresql1:node:2",
            ),
        )

        for labels, expected in cases:
            with self.subTest(labels=labels):
                payload = {
                    "alerts": [{"status": "firing", "labels": labels}]
                }
                response, forward, raise_alert = await self.dispatch(payload)
                self.assertEqual(response.status, 200)
                forward.assert_not_awaited()
                raise_alert.assert_awaited_once_with(
                    alert_proxy.CRITICAL, expected, module_id=labels.get("module_id")
                )

    async def test_known_identifiers_remain_unchanged(self):
        cases = (
            (
                {
                    "alertname": "DiskSpaceLow",
                    "mountpoint": "/var/lib",
                },
                "disk-low:lib:node:4",
            ),
            (
                {"alertname": "DiskSpaceLow", "device": "/dev/vda2"},
                "disk-low:/dev/vda2:node:4",
            ),
            (
                {
                    "alertname": "DiskSpaceCritical",
                    "mountpoint": "/var/lib",
                },
                "disk-full:lib:node:4",
            ),
            (
                {
                    "alertname": "DiskSpaceCritical",
                    "device": "/dev/vda2",
                },
                "disk:/dev/vda2:node:4",
            ),
            ({"alertname": "SwapFull"}, "swap:node:4"),
            ({"alertname": "SwapNotPresent"}, "swap-notpresent:node:4"),
            (
                {"alertname": "RaidDiskFailed", "device": "md0"},
                "raid-disk-failed:md0:node:4",
            ),
            (
                {"alertname": "RaidDriveMissing", "device": "md1"},
                "raid-drive-missing:md1:node:4",
            ),
            (
                {"alertname": "BackupFailed", "name": "home"},
                "backup-failed:home:node:4",
            ),
            ({"alertname": "NodeOffline"}, "node-offline:node:4"),
            ({"alertname": "LokiOffline"}, "loki-offline:node:4"),
            (
                {"alertname": "CertExpiringSoon", "cn": "soon.example"},
                "cert-expiring-soon:soon.example:node:4",
            ),
            (
                {
                    "alertname": "CertExpiringCritical",
                    "cn": "critical.example",
                },
                "cert-expiring-critical:critical.example:node:4",
            ),
            (
                {"alertname": "CertExpired", "cn": "old.example"},
                "cert-expired:old.example:node:4",
            ),
        )

        for specific_labels, expected in cases:
            labels = {
                "node": "4",
                "module_id": "postgresql1",
                **specific_labels,
            }
            with self.subTest(alert_name=labels["alertname"]):
                payload = {
                    "alerts": [{"status": "firing", "labels": labels}]
                }
                response, forward, raise_alert = await self.dispatch(payload)
                self.assertEqual(response.status, 200)
                forward.assert_not_awaited()
                raise_alert.assert_awaited_once_with(
                    alert_proxy.CRITICAL, expected, module_id=labels.get("module_id")
                )

    async def test_mimir_receives_the_complete_unmodified_alert_payload(self):
        alerts = [
            {
                "status": "firing",
                "labels": {
                    "alertname": "ApplicationDown",
                    "module_id": "postgresql1",
                    "node": "2",
                    "severity": "critical",
                },
                "annotations": {"description": "complete payload"},
                "startsAt": "2026-07-21T10:00:00Z",
                "generatorURL": "/prometheus/graph?g0.expr=up",
                "fingerprint": "1234abcd",
            }
        ]
        payload = {
            "receiver": "default-receiver",
            "alerts": alerts,
            "groupLabels": {
                "alertname": "ApplicationDown",
                "module_id": "postgresql1",
            },
        }
        original = copy.deepcopy(payload)
        response, forward, raise_alert = await self.dispatch(
            payload, mimir_url="https://mimir.example"
        )

        self.assertEqual(response.status, 200)
        forward.assert_awaited_once_with(alerts)
        self.assertIs(forward.await_args.args[0], alerts)
        raise_alert.assert_awaited_once_with(
            alert_proxy.CRITICAL,
            "applicationdown:postgresql1:node:2",
            module_id="postgresql1",
        )
        self.assertEqual(payload, original)


if __name__ == "__main__":
    unittest.main()
