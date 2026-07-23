#!/usr/bin/env python3

#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

import importlib.machinery
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


class FakeResponse:
    def __init__(self, text, status):
        self.text = text
        self.status = status


def load_alert_proxy():
    aiohttp_module = types.ModuleType('aiohttp')
    web_module = types.ModuleType('aiohttp.web')
    web_module.Response = FakeResponse
    aiohttp_module.web = web_module

    script_path = (
        pathlib.Path(__file__).resolve().parents[1]
        / 'alert-proxy'
        / 'alert-proxy'
    )
    loader = importlib.machinery.SourceFileLoader(
        'alert_proxy_under_test', str(script_path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        'aiohttp': aiohttp_module,
        'aiohttp.web': web_module,
    }):
        loader.exec_module(module)
    return module


alert_proxy = load_alert_proxy()


class FakeRequest:
    def __init__(self, labels):
        self.labels = labels

    async def json(self):
        return {'alerts': [{'status': 'firing', 'labels': self.labels}]}


class AlertIdentifierTests(unittest.IsolatedAsyncioTestCase):
    async def dispatch(self, labels):
        raise_alert = AsyncMock()
        with (
            patch.object(alert_proxy, 'mimir_url', ''),
            patch.object(alert_proxy, 'raise_alert', raise_alert),
        ):
            response = await alert_proxy.handle_post_request(
                FakeRequest(labels)
            )
        self.assertEqual(response.status, 200)
        return raise_alert.await_args.args[1]

    async def test_module_identity_scopes_mapped_and_generic_alerts(self):
        cases = (
            (
                {'alertname': 'LokiOffline', 'module_id': 'loki1', 'node': '4'},
                'loki-offline:loki1:node:4',
            ),
            (
                {'alertname': 'LokiOffline', 'module_id': 'loki2', 'node': '4'},
                'loki-offline:loki2:node:4',
            ),
            (
                {
                    'alertname': 'Application_Down',
                    'module_id': 'postgresql1',
                    'node': '2',
                },
                'application-down:postgresql1:node:2',
            ),
        )

        for labels, expected in cases:
            with self.subTest(labels=labels):
                self.assertEqual(await self.dispatch(labels), expected)

    async def test_moduleless_and_empty_module_ids_keep_legacy_ids(self):
        cases = (
            (
                {'alertname': 'LokiOffline', 'node': '4'},
                'loki-offline:node:4',
            ),
            (
                {
                    'alertname': 'Application_Down',
                    'module_id': '',
                    'node': '2',
                },
                'application-down:node:2',
            ),
        )

        for labels, expected in cases:
            with self.subTest(labels=labels):
                self.assertEqual(await self.dispatch(labels), expected)


if __name__ == '__main__':
    unittest.main()
