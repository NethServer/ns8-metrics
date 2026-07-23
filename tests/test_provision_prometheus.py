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

    script_path = (
        pathlib.Path(__file__).resolve().parents[1]
        / 'imageroot'
        / 'bin'
        / 'provision-prometheus'
    )
    loader = importlib.machinery.SourceFileLoader(
        'provision_prometheus', str(script_path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
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

    def test_missing_labels_are_created(self):
        redis_client = FakeRedis(hashes={
            'module/postgresql1/metrics_targets': {
                'postgres': '- targets: ["127.0.0.1:9187"]\n',
            },
        })

        self.generate_targets(redis_client)

        targets = self.read_yaml('prometheus.d/provision_postgresql1_postgres.yml')
        self.assertEqual(targets[0]['labels'], {
            'module_id': 'postgresql1',
            'target_type': 'postgres',
        })

    def test_matching_identity_and_custom_labels_are_preserved(self):
        redis_client = FakeRedis(hashes={
            'module/postgresql1/metrics_targets': {
                'postgres': '''
- targets: ["127.0.0.1:9187"]
  labels:
    module_id: postgresql1
    target_type: postgres
    node: "1"
    environment: production
''',
            },
        })
        warnings = io.StringIO()

        with redirect_stderr(warnings):
            self.generate_targets(redis_client)

        targets = self.read_yaml('prometheus.d/provision_postgresql1_postgres.yml')
        self.assertEqual(targets[0]['labels'], {
            'module_id': 'postgresql1',
            'target_type': 'postgres',
            'node': '1',
            'environment': 'production',
        })
        self.assertEqual(warnings.getvalue(), '')

    def test_conflicting_identity_is_overwritten_and_warned(self):
        redis_key = 'module/postgresql1/metrics_targets'
        redis_client = FakeRedis(hashes={
            redis_key: {
                'postgres': '''
- targets: ["127.0.0.1:9187"]
  labels:
    module_id: mariadb1
    target_type: database
''',
            },
        })
        warnings = io.StringIO()

        with redirect_stderr(warnings):
            self.generate_targets(redis_client)

        targets = self.read_yaml('prometheus.d/provision_postgresql1_postgres.yml')
        self.assertEqual(targets[0]['labels']['module_id'], 'postgresql1')
        self.assertEqual(targets[0]['labels']['target_type'], 'postgres')
        warning_text = warnings.getvalue()
        self.assertIn(redis_key, warning_text)
        self.assertIn('field postgres, item 0', warning_text)
        self.assertIn("module_id='mariadb1' overwritten with 'postgresql1'", warning_text)
        self.assertIn("target_type='database' overwritten with 'postgres'", warning_text)

    def test_malformed_field_is_skipped_without_affecting_valid_field(self):
        malformed_documents = {
            'invalid-yaml': '- targets: [\n',
            'not-a-list': 'targets: ["127.0.0.1:9000"]\n',
            'non-mapping-item': '- "127.0.0.1:9000"\n',
            'non-mapping-labels': '''
- targets: ["127.0.0.1:9000"]
  labels: invalid
''',
        }

        for malformed_name, malformed_document in malformed_documents.items():
            with self.subTest(malformed_name=malformed_name):
                redis_client = FakeRedis(hashes={
                    'module/example1/metrics_targets': {
                        malformed_name: malformed_document,
                        'valid': '- targets: ["127.0.0.1:9001"]\n',
                    },
                })
                warnings = io.StringIO()

                with redirect_stderr(warnings):
                    self.generate_targets(redis_client)

                self.assertFalse(
                    (self.work_directory / 'prometheus.d'
                     / f'provision_example1_{malformed_name}.yml').exists()
                )
                self.assertTrue(
                    (self.work_directory / 'prometheus.d'
                     / 'provision_example1_valid.yml').exists()
                )
                self.assertIn(
                    f'Skipped target {malformed_name} for module example1',
                    warnings.getvalue(),
                )

    def test_builtin_node_target_has_no_module_id(self):
        redis_client = FakeRedis(hashes={
            'node/1/vpn': {'ip_address': '10.0.0.1'},
        })

        self.generate_targets(redis_client)

        targets = self.read_yaml('prometheus.d/node_1.yml')
        self.assertEqual(targets[0]['labels'], {
            'target_type': 'node',
            'node': 1,
        })
        self.assertNotIn('module_id', targets[0]['labels'])


class AlertmanagerConfigurationTests(unittest.TestCase):
    def test_grouping_and_inhibition_include_module_identity(self):
        redis_client = FakeRedis()

        with tempfile.TemporaryDirectory() as temp_directory:
            with working_directory(temp_directory):
                with patch.dict(os.environ, {'MODULE_ID': 'metrics1'}):
                    provision_prometheus.generate_alertmanagr_config(redis_client)
                with open('alertmanager.yml', encoding='utf-8') as stream:
                    alertmanager_config = yaml.safe_load(stream)

        self.assertEqual(
            alertmanager_config['route']['group_by'],
            ['alertname', 'node', 'module_id'],
        )
        self.assertEqual(
            alertmanager_config['inhibit_rules'][0]['equal'],
            ['alertname', 'module_id'],
        )


if __name__ == '__main__':
    unittest.main()
