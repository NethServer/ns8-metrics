#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Regression checks for coverage, batching, and recoverable Robot fixtures."""

import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from robot import run
from robot.api import get_model

from resources.module_alert_rules_transport import redis_batch


ROOT = Path(__file__).resolve().parent
RESOURCE = ROOT / 'resources/module_alert_rules.resource'


class FixtureBatchTests(unittest.TestCase):
    def run_fixture_test(self, body, keywords='', *, extra_tests='', expected_status=0):
        source = f'''*** Settings ***
Resource    {RESOURCE}

*** Variables ***
@{{TRANSPORT_RESULTS}}
@{{TRANSPORT_CALLS}}

*** Test Cases ***
Fixture recovery
{body}
{extra_tests}

*** Keywords ***
Execute Fixture Transport
    [Arguments]    ${{operation}}    ${{values}}    ${{scope}}=${{EMPTY}}
    Append To List    ${{TRANSPORT_CALLS}}    ${{operation}}
    ${{result}} =    Remove From List    ${{TRANSPORT_RESULTS}}    0
    IF    $result == 'disconnect'    Fail    SSH disconnected
    RETURN    ${{result}}

{keywords}
'''
        with tempfile.TemporaryDirectory() as directory:
            # Replace transport dependencies in a temporary resource instead
            # of depending on Robot's changing suite/resource search order.
            resource = get_model(RESOURCE)
            overrides = {'Execute Fixture Transport'} | {
                line for line in keywords.splitlines()
                if line and not line[0].isspace()
            }
            for section in resource.sections:
                section.body[:] = [node for node in section.body
                                   if getattr(node, 'name', None) not in overrides]
            copied_resource = Path(directory) / 'fixtures.resource'
            resource.save(copied_resource)
            path = Path(directory) / 'fixtures.robot'
            path.write_text(source.replace(str(RESOURCE), str(copied_resource)))
            output = io.StringIO()
            status = run(str(path), outputdir=directory, log='NONE', report='NONE',
                         stdout=output, stderr=output)
            self.assertEqual(status, expected_status, output.getvalue())
            result = ET.parse(Path(directory) / 'output.xml')
            if extra_tests:
                self.assertEqual(result.findall('.//test')[-1].find('status').get('status'), 'PASS', output.getvalue())

    def test_loaded_rule_assertions_match_exact_names_and_counts(self):
        self.run_fixture_test('''    Prometheus Rule Should Exist    Phase5Alpha
    Prometheus Rule Count Should Be    Phase5Alpha    2
    Prometheus Rule Should Be Absent    Alpha
    Prometheus Rule Should Be Absent    Phase5Missing
    Run Keyword And Expect Error    *Missing alert: Phase5Missing*    Prometheus Rule Should Exist    Phase5Missing
''', '''Read Loaded Rules
    ${first} =    Create Dictionary    name=Phase5Alpha
    ${second} =    Create Dictionary    name=Phase5AlphaExtra
    ${rules} =    Create List    ${first}    ${second}    ${first}
    RETURN    ${rules}
''')

    def test_preexisting_fields_abort_whole_batch_without_registration(self):
        self.run_fixture_test('''    Write Publisher Hash Field    module/app1/metrics_alert_rules    new    payload
    Write Publisher Hash Field    module/app1/metrics_alert_rules    occupied    payload
    ${exists} =    Create List    ${False}    ${True}
    Append To List    ${TRANSPORT_RESULTS}    ${exists}
    Run Keyword And Expect Error    *pre-existing Redis field*    Flush Publisher Writes
    Should Be Empty    ${FIXTURE_FIELDS}
    ${expected} =    Create List    exists
    Lists Should Be Equal    ${TRANSPORT_CALLS}    ${expected}
''')

    def test_lost_partial_write_response_keeps_entire_batch_recoverable(self):
        self.run_fixture_test('''    Write Publisher Hash Field    module/app1/metrics_alert_rules    first    payload
    Write Publisher Hash Field    module/app1/metrics_alert_rules    second    payload
    ${absent} =    Create List    ${False}    ${False}
    Append To List    ${TRANSPORT_RESULTS}    ${absent}    disconnect
    Run Keyword And Expect Error    SSH disconnected    Flush Publisher Writes
    Length Should Be    ${FIXTURE_FIELDS}    2
    ${deleted} =    Create List    ${1}    ${False}    ${0}    ${False}
    Append To List    ${TRANSPORT_RESULTS}    ${deleted}
    Delete Test Publisher Data
    Should Be Empty    ${FIXTURE_FIELDS}
    ${expected} =    Create List    exists    write    delete
    Lists Should Be Equal    ${TRANSPORT_CALLS}    ${expected}
''')

    def test_partial_cleanup_acknowledges_only_confirmed_deletions(self):
        self.run_fixture_test('''    ${first} =    Create List    module/app1/metrics_alert_rules    first
    ${second} =    Create List    module/app1/metrics_alert_rules    second
    Append To List    ${FIXTURE_FIELDS}    ${first}    ${second}
    ${partial} =    Create List    ${1}    ${False}    permission denied    ${True}
    Append To List    ${TRANSPORT_RESULTS}    ${partial}
    Run Keyword And Expect Error    Cleanup failed*    Delete Test Publisher Data
    Length Should Be    ${FIXTURE_FIELDS}    1
    Lists Should Be Equal    ${FIXTURE_FIELDS}[0]    ${second}
    Append To List    ${TRANSPORT_RESULTS}    disconnect
    Run Keyword And Expect Error    SSH disconnected    Delete Test Publisher Data
    Length Should Be    ${FIXTURE_FIELDS}    1
    ${complete} =    Create List    ${1}    ${False}
    Append To List    ${TRANSPORT_RESULTS}    ${complete}
    Delete Test Publisher Data
    Should Be Empty    ${FIXTURE_FIELDS}
''')

    def test_failed_teardown_retries_once_then_setup_stays_clean(self):
        self.run_fixture_test('''    Set Suite Variable    ${CLEANUP_PENDING}    ${True}
    ${entry} =    Create List    module/app1/metrics_alert_rules    first
    Append To List    ${FIXTURE_FIELDS}    ${entry}
    Append To List    ${TRANSPORT_RESULTS}    disconnect
    Run Keyword And Expect Error    SSH disconnected    Reset Module Rule Fixtures
    Should Be True    ${CLEANUP_PENDING}
    Length Should Be    ${FIXTURE_FIELDS}    1
    ${complete} =    Create List    ${1}    ${False}
    Append To List    ${TRANSPORT_RESULTS}    ${complete}
    Prepare Module Rule Fixtures
    Should Not Be True    ${CLEANUP_PENDING}
    Should Be Empty    ${FIXTURE_FIELDS}
    Prepare Module Rule Fixtures
    Length Should Be    ${BATCH_CASES}    0
    ${expected} =    Create List    delete    provision    active    delete    provision    active
    Lists Should Be Equal    ${TRANSPORT_CALLS}    ${expected}
''', '''Provision Rule Fixtures
    Append To List    ${TRANSPORT_CALLS}    provision

Ensure Prometheus Is Active
    Append To List    ${TRANSPORT_CALLS}    active
''')

    def test_teardown_failure_does_not_clear_pending_cleanup(self):
        self.run_fixture_test('''    [Teardown]    Reset Module Rule Fixtures
    ${entry} =    Create List    module/app1/metrics_alert_rules    first
    Append To List    ${FIXTURE_FIELDS}    ${entry}
    Append To List    ${TRANSPORT_RESULTS}    disconnect
''', '''Provision Rule Fixtures
    No Operation

Ensure Prometheus Is Active
    No Operation
''', extra_tests='''
Next setup recovers the failed teardown
    Should Be True    ${CLEANUP_PENDING}
    Length Should Be    ${FIXTURE_FIELDS}    1
    ${complete} =    Create List    ${1}    ${False}
    Append To List    ${TRANSPORT_RESULTS}    ${complete}
    Prepare Module Rule Fixtures
    Should Not Be True    ${CLEANUP_PENDING}
    Should Be Empty    ${FIXTURE_FIELDS}
''', expected_status=1)

    def test_failed_file_cleanup_preserves_registration_in_teardown(self):
        self.run_fixture_test('''    [Teardown]    Remove Fixture Files
    Append To List    ${FIXTURE_FILES}    rules.d/provision_fixture.yml
''', '''Execute Command
    [Arguments]    ${command}    ${return_rc}
    RETURN    permission denied    ${1}
''', extra_tests='''
File cleanup remains recoverable
    ${expected} =    Create List    rules.d/provision_fixture.yml
    Lists Should Be Equal    ${FIXTURE_FILES}    ${expected}
''', expected_status=1)

    def test_teardown_polls_until_event_finishes_before_caching_journal(self):
        for keyword, arguments in (
            ('Provisioning Event Should Have Exited', 'cursor    changed'),
            ('Module Event Should Complete', 'cursor    metrics1    changed'),
        ):
            with self.subTest(keyword=keyword):
                self.run_fixture_test('''    [Teardown]    Check Event Polling
    Set Suite Variable    ${JOURNAL_READS}    ${0}
''', f'''Check Event Polling
    Wait Until Keyword Succeeds    1s    1ms    {keyword}    {arguments}
    Should Be Equal As Integers    ${{JOURNAL_READS}}    2
    ${{journal}} =    Read Journal After Cursor    cursor
    Should Contain    ${{journal}}    exited with status "completed"
    Should Be Equal As Integers    ${{JOURNAL_READS}}    2

Execute Command
    [Arguments]    ${{command}}    ${{return_rc}}
    Set Suite Variable    ${{JOURNAL_READS}}    ${{JOURNAL_READS + 1}}
    IF    $JOURNAL_READS == 1    RETURN    Handler started    ${{0}}
    RETURN    Handler of module/metrics1/event/changed exited with status "completed"    ${{0}}

Capture Fixture Snapshot
    No Operation
''')

    def test_batch_write_errors_keep_cleanup_registrations(self):
        self.run_fixture_test('''    Write Publisher Hash Field    module/app1/metrics_alert_rules    first    payload
    ${absent} =    Create List    ${False}
    ${failed} =    Create List    write failed
    Append To List    ${TRANSPORT_RESULTS}    ${absent}    ${failed}
    Run Keyword And Expect Error    *Redis write failed*    Flush Publisher Writes
    Length Should Be    ${FIXTURE_FIELDS}    1
''')

    def test_transport_preserves_raw_bytes_and_per_command_errors(self):
        class Pipeline:
            def __init__(self):
                self.calls = []

            def hset(self, *args):
                self.calls.append(args)

            def execute(self, raise_on_error):
                self.raise_on_error = raise_on_error
                return [1, RuntimeError('interrupted write')]

        class Client:
            def pipeline(self, transaction):
                self.transaction = transaction
                self.batch = Pipeline()
                return self.batch

        client = Client()
        entry = [b'module/app1/metrics_alert_rules', b'bad\x00field', b'\xff']
        encoded = [base64.b64encode(value).decode() for value in entry]
        results = redis_batch(client, 'write', [encoded, encoded])
        self.assertEqual(client.batch.calls, [tuple(entry), tuple(entry)])
        self.assertEqual(results, [1, 'interrupted write'])
        self.assertFalse(client.transaction)
        self.assertFalse(client.batch.raise_on_error)


class RobotCoverageTests(unittest.TestCase):
    def test_all_30_scenarios_and_78_original_inputs_remain(self):
        model = get_model(ROOT / '20__module_alert_rules.robot')
        cases = model.sections[1].body
        self.assertEqual(len(cases), 30)
        inputs = [(case.name, [list(row.args) for row in case.body
                              if (getattr(row, 'keyword', '') or '').startswith('Queue ')])
                  for case in cases]
        inputs = [(name, rows) for name, rows in inputs if rows]
        self.assertEqual(len(inputs), 12)
        self.assertEqual(sum(len(rows) for _, rows in inputs), 78)
        digest = hashlib.sha256(json.dumps(inputs, ensure_ascii=True).encode()).hexdigest()
        # Snapshot of the original parameter tables, including expected warnings.
        self.assertEqual(digest, '8ca07d1d1f462967b1d0f3dac3d27a4564629acc3657ada7a6ac84f21433393c')

    def test_normal_run_has_38_body_events_and_31_resets(self):
        suite = get_model(ROOT / '20__module_alert_rules.robot')
        resource = get_model(RESOURCE)
        keywords = {node.name: node for section in resource.sections
                    for node in section.body if type(node).__name__ == 'Keyword'}
        signals = {'Signal Module Rule Change', 'Signal Metrics Target Change'}

        def count(node):
            if type(node).__name__ == 'KeywordCall':
                if node.keyword in signals:
                    return 1
                if node.keyword in keywords:
                    return count(keywords[node.keyword])
                return 0
            if type(node).__name__ == 'For':
                events = sum(count(child) for child in node.body)
                if not events:
                    return 0
                # The only publishing loop is the sequential format replacement.
                self.assertEqual(tuple(node.values), ('single', 'full'))
                return 2 * events
            return sum(count(child) for child in getattr(node, 'body', []))

        cases = suite.sections[1].body
        self.assertEqual(sum(count(case) for case in cases), 38)
        settings = {type(row).__name__: getattr(row, 'name', None)
                    for row in suite.sections[0].body}
        self.assertEqual(settings['TestSetup'], 'Prepare Module Rule Fixtures')
        self.assertEqual(settings['TestTeardown'], 'Reset Module Rule Fixtures')
        self.assertEqual(count(keywords['Reset Module Rule Fixtures']), 0)
        # Reset provisions through Continue On Failure, once on initialization
        # and once per test. Setup retry behavior is exercised above.
        resets = [node for node in ast.walk(keywords['Clean Module Rule Fixtures'])
                  if getattr(node, 'args', ()) == ('Provision Rule Fixtures',)]
        self.assertEqual(len(resets), 1)
        self.assertEqual(1 + len(cases), 31)


if __name__ == '__main__':
    unittest.main()
