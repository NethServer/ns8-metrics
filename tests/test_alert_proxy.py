#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

import importlib.machinery
import importlib.util
from pathlib import Path
import unittest


_LOADER = importlib.machinery.SourceFileLoader(
    "alert_proxy_under_test",
    str(Path(__file__).parents[1] / "alert-proxy/alert-proxy"),
)
_SPEC = importlib.util.spec_from_loader(_LOADER.name, _LOADER)
alert_proxy = importlib.util.module_from_spec(_SPEC)
_LOADER.exec_module(alert_proxy)


class AlertProxyIdentityTest(unittest.TestCase):
    def test_generic_id_includes_module_identity(self):
        self.assertEqual(
            alert_proxy.build_alert_id(
                "Postgresql_Down",
                {"module_id": "postgresql1"},
                "1",
            ),
            "postgresql-down:postgresql1:node:1",
        )

    def test_generic_id_without_module_preserves_fallback(self):
        self.assertEqual(
            alert_proxy.build_alert_id("Postgresql_Down", {}, "1"),
            "postgresql-down:node:1",
        )

    def test_known_mapping_ignores_module_identity(self):
        self.assertEqual(
            alert_proxy.build_alert_id(
                "DiskSpaceLow",
                {
                    "module_id": "postgresql1",
                    "mountpoint": "/var/lib/postgresql",
                },
                "1",
            ),
            "disk-low:postgresql:node:1",
        )


if __name__ == "__main__":
    unittest.main()
