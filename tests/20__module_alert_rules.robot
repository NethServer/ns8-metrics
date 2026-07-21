*** Settings ***
Documentation    Test provider-published alert rules without using legacy custom alerts
Resource         resources/module_alert_rules.resource
Suite Setup      Initialize Module Rule Fixtures
Suite Teardown   Clean Up Module Rule Fixtures
Test Setup       Reset Module Rule Fixtures
Test Teardown    Ensure Prometheus Is Active


*** Test Cases ***
Provider Targets Receive Their Redis Owner Identity
    ${target_a} =    Target Payload Without Labels    19091
    ${target_b} =    Target Payload With Module Label
    ...    19092
    ...    another1
    ${since} =    Current Epoch

    Write Publisher Target    ${RULE_PUBLISHER_A}    ${target_a}
    Write Publisher Target    ${RULE_PUBLISHER_B}    ${target_b}
    ${event_cursor} =    Signal Metrics Target Change    ${RULE_PUBLISHER_A}

    Wait Until Keyword Succeeds    60s    2s
    ...    Provider Target File Should Contain
    ...    ${RULE_PUBLISHER_A}
    ...    module_id: ${RULE_PUBLISHER_A}
    ...    target_type: ${TARGET_FIELD}
    Wait Until Keyword Succeeds    60s    2s
    ...    Provider Target File Should Contain
    ...    ${RULE_PUBLISHER_B}
    ...    module_id: ${RULE_PUBLISHER_B}
    ...    fixture: phase5
    Wait Until Keyword Succeeds    60s    2s
    ...    Module Event Should Complete
    ...    ${event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-target-changed
    Journal Should Contain    ${since}    overwritten with '${RULE_PUBLISHER_B}'

Two Publishers Load Full And Single Rules Independently
    ${target_a} =    Target Payload Without Labels    19091
    ${target_b} =    Target Payload Without Labels    19092
    Write Publisher Target    ${RULE_PUBLISHER_A}    ${target_a}
    Write Publisher Target    ${RULE_PUBLISHER_B}    ${target_b}
    ${single_rule} =    Single Alert Rule Payload
    ...    SharedApplicationDown
    ...    sum(up{target_type="${TARGET_FIELD}"}) == 0
    ${full_rule} =    Full Alert Rule Payload
    ...    application
    ...    SharedApplicationDown
    ...    up{target_type="${TARGET_FIELD}"} == 0
    ...    another1
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${single_rule}
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_B}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${full_rule}
    ${container_before} =    Prometheus Container ID
    ${timestamp_before} =    Prometheus Reload Timestamp
    ${since} =    Current Epoch

    ${event_cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}

    Wait Until Keyword Succeeds    90s    2s
    ...    Prometheus Rule Count Should Be
    ...    SharedApplicationDown
    ...    2
    Provider Rule File Should Contain
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ns8:${RULE_PUBLISHER_A}:${PRIMARY_RULE_FIELD}
    ...    module_id="${RULE_PUBLISHER_A}"
    ...    module_id: ${RULE_PUBLISHER_A}
    ...    fixture: phase5
    Provider Rule File Should Contain
    ...    ${RULE_PUBLISHER_B}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ns8:${RULE_PUBLISHER_B}:${PRIMARY_RULE_FIELD}:application
    ...    module_id="${RULE_PUBLISHER_B}"
    ...    module_id: ${RULE_PUBLISHER_B}
    Wait Until Keyword Succeeds    30s    1s
    ...    Prometheus Reload Timestamp Should Advance
    ...    ${timestamp_before}
    Prometheus Last Reload Should Be Successful
    ${container_after} =    Prometheus Container ID
    Should Be Equal    ${container_after}    ${container_before}
    Alertmanager Configuration Should Use Module Identity
    Wait Until Keyword Succeeds    60s    2s
    ...    Module Event Should Complete
    ...    ${event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-alert-rules-changed
    Journal Should Not Contain
    ...    ${since}
    ...    duplicate installed module alert identity

Invalid And Warning Sources Are Isolated
    ${valid_a} =    Single Alert Rule Payload
    ...    RetainedProviderAlert
    ...    up == 0
    ${valid_b} =    Single Alert Rule Payload
    ...    IndependentlyUpdatedAlert
    ...    up == 0
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${valid_a}
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_B}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${valid_b}
    ${initial_event_cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}
    Wait Until Keyword Succeeds    90s    2s
    ...    Prometheus Rule Should Exist
    ...    IndependentlyUpdatedAlert
    Wait Until Keyword Succeeds    60s    2s
    ...    Module Event Should Complete
    ...    ${initial_event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-alert-rules-changed
    ${checksum_a_before} =    Provider Rule Checksum
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ${checksum_b_before} =    Provider Rule Checksum
    ...    ${RULE_PUBLISHER_B}
    ...    ${PRIMARY_RULE_FIELD}

    ${invalid_a} =    Single Alert Rule Payload
    ...    RetainedProviderAlert
    ...    up{
    ${updated_b} =    Single Alert Rule Payload
    ...    IndependentlyUpdatedAlert
    ...    ns8_phase5_missing_metric == 0
    ${duplicate_b} =    Minimal Alert Rule Payload
    ...    IndependentlyUpdatedAlert
    ...    up == 0
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${invalid_a}
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_B}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${updated_b}
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_B}
    ...    ${SECONDARY_RULE_FIELD}
    ...    ${duplicate_b}
    ${since} =    Current Epoch
    ${event_cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}

    Wait Until Keyword Succeeds    90s    2s
    ...    Provider Rule File Should Exist
    ...    ${RULE_PUBLISHER_B}
    ...    ${SECONDARY_RULE_FIELD}
    Wait Until Keyword Succeeds    90s    2s
    ...    Module Event Should Complete
    ...    ${event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-alert-rules-changed
    ${checksum_a_after} =    Provider Rule Checksum
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ${checksum_b_after} =    Provider Rule Checksum
    ...    ${RULE_PUBLISHER_B}
    ...    ${PRIMARY_RULE_FIELD}
    Should Be Equal    ${checksum_a_after}    ${checksum_a_before}
    Should Not Be Equal    ${checksum_b_after}    ${checksum_b_before}
    Wait Until Keyword Succeeds    30s    1s
    ...    Journal Should Contain
    ...    ${since}
    ...    unknown metric
    Journal Should Contain    ${since}    Skipped module alert rule
    Journal Should Contain    ${since}    duplicate installed module alert identity
    Journal Should Contain    ${since}    no severity label
    Journal Should Contain    ${since}    missing bilingual annotations

Removed Provider Rules Preserve Built-In Rules
    ${rule} =    Single Alert Rule Payload
    ...    RemovedProviderAlert
    ...    up == 0
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${rule}
    ${install_event_cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}
    Wait Until Keyword Succeeds    90s    2s
    ...    Prometheus Rule Should Exist
    ...    RemovedProviderAlert
    Wait Until Keyword Succeeds    60s    2s
    ...    Module Event Should Complete
    ...    ${install_event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-alert-rules-changed

    Delete Publisher Rule Field
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ${remove_event_cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}

    Wait Until Keyword Succeeds    90s    2s
    ...    Provider Rule File Should Be Absent
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    Wait Until Keyword Succeeds    60s    2s
    ...    Module Event Should Complete
    ...    ${remove_event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-alert-rules-changed
    Wait Until Keyword Succeeds    30s    1s
    ...    Prometheus Rule Should Be Absent
    ...    RemovedProviderAlert
    Prometheus Rule Should Exist    NodeOffline
    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} /usr/bin/test -f rules.d/nodes.yml
    ...    return_rc=${True}
    ...    return_stdout=${False}
    Should Be Equal As Integers    ${rc}    0

Rule Events Leave An Inactive Prometheus Stopped
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} systemctl --user stop prometheus.service
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}
    ${rule} =    Single Alert Rule Payload
    ...    InactiveProviderAlert
    ...    up == 0
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${rule}

    ${event_cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}

    Wait Until Keyword Succeeds    60s    2s
    ...    Provider Rule File Should Exist
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    Wait Until Keyword Succeeds    60s    2s
    ...    Module Event Should Complete
    ...    ${event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-alert-rules-changed
    Prometheus Service Should Be Inactive
