#!/usr/bin/env python3

#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

import fnmatch
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import sys
import tempfile
import types
import unittest
import warnings
from contextlib import contextmanager, redirect_stderr
from unittest.mock import patch

import yaml


@contextmanager
def working_directory(path):
    previous_directory = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous_directory)


def load_provision_prometheus():
    agent_module = types.ModuleType('agent')
    agent_module.get_hostname = lambda: 'node.example.org'
    agent_module.get_smarthost_settings = lambda redis_client: {'enabled': False}
    agent_module.redis_connect = lambda use_replica: None
    sys.modules['agent'] = agent_module

    bin_directory = (
        pathlib.Path(__file__).resolve().parents[1] / 'imageroot' / 'bin'
    )
    script_path = bin_directory / 'provision-prometheus'
    loader = importlib.machinery.SourceFileLoader(
        'provision_prometheus', str(script_path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(bin_directory))
    try:
        loader.exec_module(module)
    finally:
        sys.path.remove(str(bin_directory))
    return module


provision_prometheus = load_provision_prometheus()


class FakeRedis:
    def __init__(self, hashes=None, sets=None):
        self.hashes = hashes or {}
        self.sets = sets or {}

    def exists(self, key):
        return key in self.hashes

    def hgetall(self, key):
        return self.hashes.get(key, {})

    def hvals(self, key):
        return list(self.hgetall(key).values())

    def scan_iter(self, pattern):
        keys = sorted(set(self.hashes) | set(self.sets))
        return iter(key for key in keys if fnmatch.fnmatch(key, pattern))

    def sismember(self, key, value):
        return value in self.sets.get(key, set())


class ProviderTargetTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.work_directory = pathlib.Path(self.temp_directory.name)
        (self.work_directory / 'prometheus.d').mkdir()

    def generate_targets(self, redis_client):
        with working_directory(self.work_directory):
            provision_prometheus.validate_and_generate_provider_configs(redis_client)

    def read_yaml(self, relative_path):
        with open(self.work_directory / relative_path, encoding='utf-8') as stream:
            return yaml.safe_load(stream)

    def test_conflicting_target_type_is_overwritten_and_warned(self):
        redis_key = 'module/postgresql1/metrics_targets'
        redis_client = FakeRedis(hashes={
            redis_key: {
                'postgres': '''
- targets: ["127.0.0.1:9187"]
  labels:
    module_id: postgresql1
    target_type: database
''',
            },
        })
        warnings = io.StringIO()

        with redirect_stderr(warnings):
            self.generate_targets(redis_client)

        targets = self.read_yaml('prometheus.d/provision_postgresql1_postgres.yml')
        self.assertEqual(targets[0]['labels']['target_type'], 'postgres')
        warning_text = warnings.getvalue()
        self.assertIn(redis_key, warning_text)
        self.assertIn('field postgres, item 0', warning_text)
        self.assertIn("target_type='database' overwritten with 'postgres'", warning_text)

    def test_non_string_target_names_do_not_block_valid_fields(self):
        # Redis-backed Robot fixtures cover string names; retain Python types here.
        invalid_names = (None, 123)
        redis_key = 'module/postgresql1/metrics_targets'
        for index, invalid_name in enumerate(invalid_names):
            with self.subTest(name=invalid_name):
                valid_name = f'valid{index}'
                redis_client = FakeRedis(hashes={
                    redis_key: {
                        invalid_name: '- targets: ["127.0.0.1:9187"]\n',
                        valid_name: '- targets: ["127.0.0.1:9188"]\n',
                    },
                })
                warnings = io.StringIO()

                with redirect_stderr(warnings):
                    self.generate_targets(redis_client)

                target = self.read_yaml(
                    f'prometheus.d/provision_postgresql1_{valid_name}.yml'
                )
                self.assertEqual(target[0]['targets'], ['127.0.0.1:9188'])
                self.assertIn(redis_key, warnings.getvalue())
                self.assertIn(f'invalid target type {invalid_name!r}', warnings.getvalue())
                self.assertEqual(
                    len(list((self.work_directory / 'prometheus.d').iterdir())),
                    index + 1,
                )
        self.assertEqual(
            list(self.work_directory.iterdir()),
            [self.work_directory / 'prometheus.d'],
        )

    def test_target_filesystem_failures_still_propagate(self):
        redis_client = FakeRedis(hashes={
            'module/postgresql1/metrics_targets': {
                'postgres': '- targets: ["127.0.0.1:9187"]\n',
            },
        })
        with patch('builtins.open', side_effect=PermissionError('read-only target directory')):
            with self.assertRaisesRegex(PermissionError, 'read-only target directory'):
                self.generate_targets(redis_client)


class ProvisioningIntegrationTests(unittest.TestCase):
    def test_main_keeps_existing_custom_generator_and_creates_directories(self):
        redis_client = FakeRedis(hashes={
            'module/metrics1/custom_alerts': {
                'local': 'alert: LocalAlert\nexpr: up == 0\n',
            },
        })

        with tempfile.TemporaryDirectory() as temp_directory:
            with working_directory(temp_directory):
                with (
                    patch.dict(os.environ, {'MODULE_ID': 'metrics1'}),
                    patch.object(
                        provision_prometheus.agent,
                        'redis_connect',
                        return_value=redis_client,
                    ),
                    patch.object(
                        provision_prometheus.metrics_alert_rules,
                        'provision_module_alert_rules',
                    ) as provision_rules,
                ):
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore', ResourceWarning)
                        provision_prometheus.main()

                self.assertTrue(pathlib.Path('prometheus.d').is_dir())
                self.assertTrue(pathlib.Path('rules.d').is_dir())
                self.assertTrue(pathlib.Path('rules.d/custom.yml').is_file())
                provision_rules.assert_called_once_with(redis_client)

                with open('rules.d/custom.yml', encoding='utf-8') as stream:
                    custom_rules = yaml.safe_load(stream)

        self.assertEqual(custom_rules['groups'][0]['name'], 'Custom')
        self.assertEqual(
            custom_rules['groups'][0]['rules'][0]['alert'],
            'LocalAlert',
        )


if __name__ == '__main__':
    unittest.main()
