*** Settings ***
Documentation    Test alert rules provisioned by module instances
Library    SSHLibrary
Suite Setup    Prepare alert-rule tests
Suite Teardown    Clean up alert-rule tests

*** Variables ***
${MID}    metrics1
${PROVIDER}    testmodule1
${REMOTE_FIXTURES}    /tmp/metrics-alert-rule-tests
${PROMETHEUS_API}    http://127.0.0.1:9091
@{RULE_FIXTURES}
...    full.yml
...    single.yml
...    full-update.yml
...    invalid-yaml.yml
...    invalid-promql.yml
...    warnings.yml
...    recording.yml
...    duplicate.yml
...    custom.yml

*** Test Cases ***
Publish full and single alert-rule formats
    ${container_before} =    Prometheus container ID
    Set Suite Variable    ${ORIGINAL_CONTAINER_ID}    ${container_before}
    Publish provider rule    full    full.yml
    Publish provider rule    single    single.yml

    Signal alert-rule change

    Generated rule file should exist    provision_${PROVIDER}_full.yml
    Generated rule file should exist    provision_${PROVIDER}_single.yml
    ${container_after} =    Prometheus container ID
    Should Be Equal    ${container_after}    ${container_before}
    ${reload_success} =    Prometheus metric value    prometheus_config_last_reload_successful
    Should Be Equal As Numbers    ${reload_success}    1

Preserve labels and load both rules
    Generated labels should be unchanged
    Prometheus rule should exist    TestModuleServiceDown
    Prometheus rule should exist    TestModuleConnectionsHigh

Keep previous files after invalid updates
    ${full_checksum} =    Generated rule checksum    provision_${PROVIDER}_full.yml
    ${single_checksum} =    Generated rule checksum    provision_${PROVIDER}_single.yml
    Publish provider rule    full    invalid-yaml.yml
    Publish provider rule    single    invalid-promql.yml

    Signal alert-rule change

    Generated rule checksum should be    provision_${PROVIDER}_full.yml    ${full_checksum}
    Generated rule checksum should be    provision_${PROVIDER}_single.yml    ${single_checksum}
    Prometheus rule should exist    TestModuleServiceDown
    Prometheus rule should exist    TestModuleConnectionsHigh

Apply valid updates independently
    ${full_checksum} =    Generated rule checksum    provision_${PROVIDER}_full.yml
    ${single_checksum} =    Generated rule checksum    provision_${PROVIDER}_single.yml
    Publish provider rule    full    full-update.yml

    Signal alert-rule change

    Generated rule checksum should differ    provision_${PROVIDER}_full.yml    ${full_checksum}
    Generated rule checksum should be    provision_${PROVIDER}_single.yml    ${single_checksum}
    Prometheus rule should exist    TestModuleServiceRecovered
    Prometheus rule should exist    TestModuleConnectionsHigh

Accept warnings and duplicates but reject recordings
    Publish provider rule    warnings    warnings.yml
    Publish provider rule    recording    recording.yml
    Publish provider rule    duplicate    duplicate.yml

    Signal alert-rule change

    Generated rule file should exist    provision_${PROVIDER}_warnings.yml
    Generated rule file should exist    provision_${PROVIDER}_duplicate.yml
    Generated rule file should not exist    provision_${PROVIDER}_recording.yml
    Prometheus rule should exist    TestModuleUnknownMetric
    Prometheus rule count should be    TestModuleConnectionsHigh    2
    Wait Until Keyword Succeeds    10    1s
    ...    Journal should contain    unsupported severity 'info'
    Wait Until Keyword Succeeds    10    1s
    ...    Journal should contain    missing bilingual annotations
    Wait Until Keyword Succeeds    10    1s
    ...    Journal should contain    duplicate alert name 'TestModuleConnectionsHigh'
    Wait Until Keyword Succeeds    10    1s
    ...    Journal should contain    unknown metric 'ns8_test_metric_that_does_not_exist'

Validate local custom alerts through the shared pipeline
    Publish custom rule    shared-valid    custom.yml
    Publish custom rule    shared-invalid    recording.yml

    Signal alert-rule change

    Generated rule file should exist    provision_${MID}_shared-valid.yml
    Generated rule file should not exist    provision_${MID}_shared-invalid.yml
    Prometheus rule should exist    TestMetricsCustomAlert

Remove deleted rules without touching built-ins
    Execute Command    redis-cli HDEL module/${PROVIDER}/metrics_alert_rules full warnings

    Signal alert-rule change

    Generated rule file should not exist    provision_${PROVIDER}_full.yml
    Generated rule file should not exist    provision_${PROVIDER}_warnings.yml
    Prometheus rule should not exist    TestModuleServiceRecovered
    Prometheus rule should exist    NodeOffline

*** Keywords ***
Prepare alert-rule tests
    Execute Command    mkdir -p '${REMOTE_FIXTURES}'
    FOR    ${fixture}    IN    @{RULE_FIXTURES}
        Put File    ${CURDIR}/fixtures/alert-rules/${fixture}    ${REMOTE_FIXTURES}/${fixture}
    END
    Execute Command    redis-cli HSET module/${PROVIDER}/environment MODULE_ID ${PROVIDER}
    ${path} =    Execute Command    runagent -m ${MID} printenv PROMETHEUS_PATH
    IF    '${path}' != ''
        Set Suite Variable    ${PROMETHEUS_API}    http://127.0.0.1:9091/${path}
    END

Clean up alert-rule tests
    Execute Command    redis-cli DEL module/${PROVIDER}/metrics_alert_rules module/${PROVIDER}/environment
    Execute Command    redis-cli HDEL module/${MID}/custom_alerts shared-valid shared-invalid
    Execute Command    redis-cli PUBLISH module/${MID}/event/metrics-alert-rules-changed '{}'
    Execute Command    rm -rf '${REMOTE_FIXTURES}'

Publish provider rule
    [Arguments]    ${name}    ${fixture}
    ${rc} =    Execute Command
    ...    redis-cli -x HSET module/${PROVIDER}/metrics_alert_rules '${name}' < '${REMOTE_FIXTURES}/${fixture}'
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0

Publish custom rule
    [Arguments]    ${name}    ${fixture}
    ${rc} =    Execute Command
    ...    redis-cli -x HSET module/${MID}/custom_alerts '${name}' < '${REMOTE_FIXTURES}/${fixture}'
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0

Signal alert-rule change
    ${previous_timestamp} =    Prometheus metric value    prometheus_config_last_reload_success_timestamp_seconds
    Execute Command    redis-cli PUBLISH module/${MID}/event/metrics-alert-rules-changed '{}'
    Wait Until Keyword Succeeds    40    1s
    ...    Prometheus reload timestamp should advance    ${previous_timestamp}

Prometheus metric value
    [Arguments]    ${metric_name}
    ${value} =    Execute Command
    ...    curl -fsS '${PROMETHEUS_API}/metrics' | awk '$1 == "${metric_name}" { print $2; exit }'
    RETURN    ${value}

Prometheus reload timestamp should advance
    [Arguments]    ${previous_timestamp}
    ${timestamp} =    Prometheus metric value    prometheus_config_last_reload_success_timestamp_seconds
    Should Be True    ${timestamp} > ${previous_timestamp}

Prometheus container ID
    ${container_id} =    Execute Command    runagent -m ${MID} sh -c 'cat "$XDG_RUNTIME_DIR/prometheus.ctr-id"'
    RETURN    ${container_id}

Generated rule file should exist
    [Arguments]    ${filename}
    ${rc} =    Execute Command    runagent -m ${MID} test -f 'rules.d/${filename}'
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0

Generated rule file should not exist
    [Arguments]    ${filename}
    ${rc} =    Execute Command    runagent -m ${MID} test -e 'rules.d/${filename}'
    ...    return_rc=True    return_stdout=False
    Should Not Be Equal As Integers    ${rc}    0

Generated labels should be unchanged
    ${rc} =    Execute Command
    ...    runagent -m ${MID} python3 -c 'import yaml; d=yaml.safe_load(open("rules.d/provision_${PROVIDER}_full.yml")); labels=d["groups"][0]["rules"][0]["labels"]; assert labels == {"severity": "critical", "service": "test-service", "module_id": "preserved-label"}; assert "source_module_id" not in labels'
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0

Generated rule checksum
    [Arguments]    ${filename}
    ${checksum} =    Execute Command    runagent -m ${MID} sha256sum 'rules.d/${filename}' | cut -d' ' -f1
    RETURN    ${checksum}

Generated rule checksum should be
    [Arguments]    ${filename}    ${expected}
    ${actual} =    Generated rule checksum    ${filename}
    Should Be Equal    ${actual}    ${expected}

Generated rule checksum should differ
    [Arguments]    ${filename}    ${unexpected}
    ${actual} =    Generated rule checksum    ${filename}
    Should Not Be Equal    ${actual}    ${unexpected}

Prometheus rule should exist
    [Arguments]    ${rule_name}
    ${rc} =    Execute Command
    ...    curl -fsS '${PROMETHEUS_API}/api/v1/rules' | grep -q '"name":"${rule_name}"'
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0

Prometheus rule should not exist
    [Arguments]    ${rule_name}
    ${rc} =    Execute Command
    ...    curl -fsS '${PROMETHEUS_API}/api/v1/rules' | grep -q '"name":"${rule_name}"'
    ...    return_rc=True    return_stdout=False
    Should Not Be Equal As Integers    ${rc}    0

Prometheus rule count should be
    [Arguments]    ${rule_name}    ${expected}
    ${count} =    Execute Command
    ...    curl -fsS '${PROMETHEUS_API}/api/v1/rules' | python3 -c 'import json,sys; data=json.load(sys.stdin)["data"]["groups"]; print(sum(rule.get("name") == "${rule_name}" for group in data for rule in group.get("rules", [])))'
    Should Be Equal As Integers    ${count}    ${expected}

Journal should contain
    [Arguments]    ${message}
    ${rc} =    Execute Command
    ...    journalctl --since '@${JOURNAL_SINCE}' --no-pager | grep -Fq -- "${message}"
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0
