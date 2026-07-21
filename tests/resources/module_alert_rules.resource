*** Settings ***
Library    SSHLibrary
Library    String


*** Variables ***
${METRICS_ID}             metrics1
${RULE_PUBLISHER_A}       metrics1
${RULE_PUBLISHER_B}       traefik1
${TARGET_FIELD}           phase5target
${PRIMARY_RULE_FIELD}     phase5primary
${SECONDARY_RULE_FIELD}   phase5secondary


*** Keywords ***
Initialize Module Rule Fixtures
    ${traefik_id} =    Execute Command
    ...    runagent redis-exec GET cluster/default_instance/traefik
    ${traefik_id} =    Strip String    ${traefik_id}
    Should Not Be Empty    ${traefik_id}
    Should Not Be Equal    ${traefik_id}    None
    Set Suite Variable    ${RULE_PUBLISHER_B}    ${traefik_id}
    ${prometheus_path} =    Execute Command
    ...    runagent -m ${METRICS_ID} printenv PROMETHEUS_PATH
    ${prometheus_path} =    Strip String
    ...    ${prometheus_path}
    ...    characters=/
    ${url_prefix} =    Set Variable If
    ...    '${prometheus_path}' == ''
    ...    ${EMPTY}
    ...    /${prometheus_path}
    Set Suite Variable
    ...    ${PROMETHEUS_BASE_URL}
    ...    http://127.0.0.1:9091${url_prefix}
    Reset Module Rule Fixtures

Reset Module Rule Fixtures
    Ensure Prometheus Is Active
    Delete Test Publisher Data
    ${event_cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}
    Wait Until Keyword Succeeds    90s    2s
    ...    Module Event Should Complete
    ...    ${event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-alert-rules-changed
    Wait Until Keyword Succeeds    60s    2s
    ...    Test Publisher Files Should Be Absent

Clean Up Module Rule Fixtures
    Ensure Prometheus Is Active
    Delete Test Publisher Data
    ${event_cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}
    Wait Until Keyword Succeeds    90s    2s
    ...    Module Event Should Complete
    ...    ${event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-alert-rules-changed
    Wait Until Keyword Succeeds    60s    2s
    ...    Test Publisher Files Should Be Absent

Delete Test Publisher Data
    FOR    ${publisher_id}    IN    ${RULE_PUBLISHER_A}    ${RULE_PUBLISHER_B}
        ${output}    ${rc} =    Execute Command
        ...    runagent redis-exec HDEL module/${publisher_id}/metrics_targets ${TARGET_FIELD}
        ...    return_rc=${True}
        Should Be Equal As Integers    ${rc}    0    ${output}
        ${output}    ${rc} =    Execute Command
        ...    runagent redis-exec HDEL module/${publisher_id}/metrics_alert_rules ${PRIMARY_RULE_FIELD} ${SECONDARY_RULE_FIELD}
        ...    return_rc=${True}
        Should Be Equal As Integers    ${rc}    0    ${output}
    END

Write Publisher Hash Field
    [Arguments]    ${redis_key}    ${field_name}    ${value}
    ${encoded} =    Evaluate
    ...    base64.b64encode($value.encode('utf-8')).decode('ascii')
    ...    modules=base64
    ${command} =    Catenate    SEPARATOR=
    ...    runagent python3 -c 'import agent,base64; agent.redis_connect(privileged=True).hset("${redis_key}", "${field_name}", base64.b64decode("${encoded}"))'
    ${output}    ${rc} =    Execute Command
    ...    ${command}
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}

Write Publisher Target
    [Arguments]    ${publisher_id}    ${payload}
    Write Publisher Hash Field
    ...    module/${publisher_id}/metrics_targets
    ...    ${TARGET_FIELD}
    ...    ${payload}

Write Publisher Rule
    [Arguments]    ${publisher_id}    ${field_name}    ${payload}
    Write Publisher Hash Field
    ...    module/${publisher_id}/metrics_alert_rules
    ...    ${field_name}
    ...    ${payload}

Delete Publisher Rule Field
    [Arguments]    ${publisher_id}    ${field_name}
    ${output}    ${rc} =    Execute Command
    ...    runagent redis-exec HDEL module/${publisher_id}/metrics_alert_rules ${field_name}
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}

Signal Module Rule Change
    [Arguments]    ${publisher_id}
    ${event_cursor} =    Journal Cursor
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${publisher_id} redis-exec PUBLISH module/${publisher_id}/event/metrics-alert-rules-changed '{}'
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}
    RETURN    ${event_cursor}

Signal Metrics Target Change
    [Arguments]    ${publisher_id}
    ${event_cursor} =    Journal Cursor
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${publisher_id} redis-exec PUBLISH module/${publisher_id}/event/metrics-target-changed '{}'
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}
    RETURN    ${event_cursor}

Target Payload Without Labels
    [Arguments]    ${port}
    ${payload} =    Catenate    SEPARATOR=\n
    ...    - targets:
    ...    \ \ - 127.0.0.1:${port}
    RETURN    ${payload}

Target Payload With Module Label
    [Arguments]    ${port}    ${module_id}
    ${payload} =    Catenate    SEPARATOR=\n
    ...    - targets:
    ...    \ \ - 127.0.0.1:${port}
    ...    \ \ labels:
    ...    \ \ \ \ module_id: ${module_id}
    ...    \ \ \ \ fixture: phase5
    RETURN    ${payload}

Single Alert Rule Payload
    [Arguments]    ${alert_name}    ${expression}
    ${payload} =    Catenate    SEPARATOR=\n
    ...    alert: ${alert_name}
    ...    expr: ${expression}
    ...    for: 0m
    ...    labels:
    ...    \ \ severity: critical
    ...    \ \ fixture: phase5
    ...    annotations:
    ...    \ \ summary_en: Phase 5 alert
    ...    \ \ summary_it: Avviso fase 5
    ...    \ \ description_en: Provider rule integration fixture
    ...    \ \ description_it: Fixture integrazione regola provider
    RETURN    ${payload}

Full Alert Rule Payload
    [Arguments]    ${group_name}    ${alert_name}    ${expression}    ${authored_module_id}
    ${payload} =    Catenate    SEPARATOR=\n
    ...    groups:
    ...    - name: ${group_name}
    ...    \ \ labels:
    ...    \ \ \ \ module_id: ${authored_module_id}
    ...    \ \ rules:
    ...    \ \ - alert: ${alert_name}
    ...    \ \ \ \ expr: ${expression}
    ...    \ \ \ \ labels:
    ...    \ \ \ \ \ \ severity: warning
    ...    \ \ \ \ \ \ module_id: ${authored_module_id}
    ...    \ \ \ \ annotations:
    ...    \ \ \ \ \ \ summary_en: Phase 5 full rule
    ...    \ \ \ \ \ \ summary_it: Regola completa fase 5
    ...    \ \ \ \ \ \ description_en: Provider full rule fixture
    ...    \ \ \ \ \ \ description_it: Fixture regola completa provider
    RETURN    ${payload}

Minimal Alert Rule Payload
    [Arguments]    ${alert_name}    ${expression}
    ${payload} =    Catenate    SEPARATOR=\n
    ...    alert: ${alert_name}
    ...    expr: ${expression}
    RETURN    ${payload}

Provider Target File Should Contain
    [Arguments]    ${publisher_id}    @{expected_fragments}
    ${content} =    Execute Command
    ...    runagent -m ${METRICS_ID} cat prometheus.d/provision_${publisher_id}_${TARGET_FIELD}.yml
    FOR    ${fragment}    IN    @{expected_fragments}
        Should Contain    ${content}    ${fragment}
    END

Provider Rule File Should Contain
    [Arguments]    ${publisher_id}    ${field_name}    @{expected_fragments}
    ${content} =    Execute Command
    ...    runagent -m ${METRICS_ID} cat rules.d/provision_${publisher_id}_${field_name}.yml
    FOR    ${fragment}    IN    @{expected_fragments}
        Should Contain    ${content}    ${fragment}
    END

Provider Rule File Should Exist
    [Arguments]    ${publisher_id}    ${field_name}
    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} /usr/bin/test -f rules.d/provision_${publisher_id}_${field_name}.yml
    ...    return_rc=${True}
    ...    return_stdout=${False}
    Should Be Equal As Integers    ${rc}    0

Provider Rule File Should Be Absent
    [Arguments]    ${publisher_id}    ${field_name}
    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} /usr/bin/test ! -e rules.d/provision_${publisher_id}_${field_name}.yml
    ...    return_rc=${True}
    ...    return_stdout=${False}
    Should Be Equal As Integers    ${rc}    0

Test Publisher Files Should Be Absent
    FOR    ${publisher_id}    IN    ${RULE_PUBLISHER_A}    ${RULE_PUBLISHER_B}
        Provider Rule File Should Be Absent
        ...    ${publisher_id}
        ...    ${PRIMARY_RULE_FIELD}
        Provider Rule File Should Be Absent
        ...    ${publisher_id}
        ...    ${SECONDARY_RULE_FIELD}
        ${rc} =    Execute Command
        ...    runagent -m ${METRICS_ID} /usr/bin/test ! -e prometheus.d/provision_${publisher_id}_${TARGET_FIELD}.yml
        ...    return_rc=${True}
        ...    return_stdout=${False}
        Should Be Equal As Integers    ${rc}    0
    END

Provider Rule Checksum
    [Arguments]    ${publisher_id}    ${field_name}
    ${checksum} =    Execute Command
    ...    runagent -m ${METRICS_ID} sha256sum rules.d/provision_${publisher_id}_${field_name}.yml | cut -d ' ' -f 1
    RETURN    ${checksum}

Prometheus Rule Should Exist
    [Arguments]    ${alert_name}
    ${rc} =    Execute Command
    ...    curl -fsS ${PROMETHEUS_BASE_URL}/api/v1/rules | grep -F -q '"name":"${alert_name}"'
    ...    return_rc=${True}
    ...    return_stdout=${False}
    Should Be Equal As Integers    ${rc}    0

Prometheus Rule Should Be Absent
    [Arguments]    ${alert_name}
    ${rc} =    Execute Command
    ...    curl -fsS ${PROMETHEUS_BASE_URL}/api/v1/rules | grep -F -q '"name":"${alert_name}"'
    ...    return_rc=${True}
    ...    return_stdout=${False}
    Should Be Equal As Integers    ${rc}    1

Prometheus Rule Count Should Be
    [Arguments]    ${alert_name}    ${expected_count}
    ${count} =    Execute Command
    ...    curl -fsS ${PROMETHEUS_BASE_URL}/api/v1/rules | grep -F -o '"name":"${alert_name}"' | wc -l
    Should Be Equal As Integers    ${count}    ${expected_count}

Prometheus Container ID
    ${container_id} =    Execute Command
    ...    runagent -m ${METRICS_ID} podman inspect --format '{{.Id}}' prometheus
    RETURN    ${container_id}

Prometheus Reload Timestamp
    ${timestamp} =    Execute Command
    ...    curl -fsS ${PROMETHEUS_BASE_URL}/metrics | awk '$1 == "prometheus_config_last_reload_success_timestamp_seconds" {print $2}'
    Should Not Be Empty    ${timestamp}
    RETURN    ${timestamp}

Prometheus Reload Timestamp Should Advance
    [Arguments]    ${previous_timestamp}
    ${current_timestamp} =    Prometheus Reload Timestamp
    ${advanced} =    Evaluate
    ...    float($current_timestamp) > float($previous_timestamp)
    Should Be True    ${advanced}

Prometheus Last Reload Should Be Successful
    ${value} =    Execute Command
    ...    curl -fsS ${PROMETHEUS_BASE_URL}/metrics | awk '$1 == "prometheus_config_last_reload_successful" {print $2}'
    Should Be Equal As Numbers    ${value}    1

Alertmanager Configuration Should Use Module Identity
    ${command} =    Catenate    SEPARATOR=
    ...    runagent -m ${METRICS_ID} python3 -c 'import yaml; data=yaml.safe_load(open("alertmanager.yml")); assert data["route"]["group_by"] == ["alertname", "node", "module_id"]; assert data["inhibit_rules"][0]["equal"] == ["alertname", "module_id"]'
    ${output}    ${rc} =    Execute Command
    ...    ${command}
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}

Journal Should Contain
    [Arguments]    ${since_epoch}    ${message}
    ${rc} =    Execute Command
    ...    journalctl -S @${since_epoch} --no-pager | grep -F -q -- "${message}"
    ...    return_rc=${True}
    ...    return_stdout=${False}
    Should Be Equal As Integers    ${rc}    0

Journal Should Not Contain
    [Arguments]    ${since_epoch}    ${message}
    ${rc} =    Execute Command
    ...    journalctl -S @${since_epoch} --no-pager | grep -F -q -- "${message}"
    ...    return_rc=${True}
    ...    return_stdout=${False}
    Should Be Equal As Integers    ${rc}    1

Journal Cursor
    ${cursor} =    Execute Command
    ...    journalctl -n 0 --show-cursor --no-pager | sed -n 's/^-- cursor: //p'
    Should Not Be Empty    ${cursor}
    RETURN    ${cursor}

Module Event Should Complete
    [Arguments]    ${cursor}    ${publisher_id}    ${event_name}
    ${rc} =    Execute Command
    ...    journalctl --after-cursor '${cursor}' --no-pager | grep -F -q -- 'Handler of module/${publisher_id}/event/${event_name} exited with status "completed"'
    ...    return_rc=${True}
    ...    return_stdout=${False}
    Should Be Equal As Integers    ${rc}    0

Current Epoch
    ${epoch} =    Execute Command    date +%s
    RETURN    ${epoch}

Ensure Prometheus Is Active
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} systemctl --user start prometheus.service
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}
    Wait Until Keyword Succeeds    60s    2s
    ...    Prometheus Service Should Be Active

Prometheus Service Should Be Active
    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} systemctl --user --quiet is-active prometheus.service
    ...    return_rc=${True}
    ...    return_stdout=${False}
    Should Be Equal As Integers    ${rc}    0

Prometheus Service Should Be Inactive
    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} systemctl --user --quiet is-active prometheus.service
    ...    return_rc=${True}
    ...    return_stdout=${False}
    Should Be Equal As Integers    ${rc}    3
