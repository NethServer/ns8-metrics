*** Settings ***
Library    SSHLibrary
Library    String
Library    Collections


*** Variables ***
${METRICS_ID}             metrics1
${RULE_PUBLISHER_A}       metrics1
${RULE_PUBLISHER_B}       traefik1
${TARGET_FIELD}           phase5target
${PRIMARY_RULE_FIELD}     phase5primary
${SECONDARY_RULE_FIELD}   phase5secondary
${PINNED_PROMETHEUS_IMAGE}    quay.io/prometheus/prometheus:v3.5.3
&{EMPTY_MAPPING}
@{FIXTURE_FIELDS}
@{FIXTURE_FILES}
${INITIAL_PROMETHEUS_ACTIVE}    ${True}


*** Keywords ***
Initialize Module Rule Fixtures
    ${active} =    Run Keyword And Return Status    Prometheus Service Should Be Active
    Set Suite Variable    ${INITIAL_PROMETHEUS_ACTIVE}    ${active}
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
    ${image} =    Execute Command    runagent -m ${METRICS_ID} printenv PROMETHEUS_IMAGE
    Should Be Equal    ${image}    ${PINNED_PROMETHEUS_IMAGE}
    Reset Module Rule Fixtures
    ${targets} =    Module Directory Files    prometheus.d
    ${rules} =    Module Directory Files    rules.d
    Set Suite Variable    ${BASELINE_TARGET_FILES}    ${targets}
    Set Suite Variable    ${BASELINE_RULE_FILES}    ${rules}

Finish Module Rule Fixtures
    TRY
        Reset Module Rule Fixtures
    FINALLY
        IF    not $INITIAL_PROMETHEUS_ACTIVE
            ${output}    ${rc} =    Execute Command
            ...    runagent -m ${METRICS_ID} systemctl --user stop prometheus.service
            ...    return_rc=${True}
            Should Be Equal As Integers    ${rc}    0    ${output}
        END
    END

Reset Module Rule Fixtures
    # Teardown continues through every field/file and restores the service even
    # if a test or an earlier cleanup assertion failed. Never delete whole hashes.
    TRY
        Run Keyword And Continue On Failure    Delete Test Publisher Data
        ${files} =    Copy List    ${FIXTURE_FILES}
        FOR    ${path}    IN    @{files}
            ${status}    ${detail} =    Run Keyword And Ignore Error    Remove Fixture File    ${path}
            IF    $status == 'PASS'
                Remove Values From List    ${FIXTURE_FILES}    ${path}
            ELSE
                Run Keyword And Continue On Failure    Fail    ${detail}
            END
        END
        Run Keyword And Continue On Failure    Provision Rule Fixtures
        ${baseline} =    Get Variable Value    ${BASELINE_TARGET_FILES}    ${None}
        IF    $baseline is not None
            Run Keyword And Continue On Failure
            ...    Directory Should Contain Only Added Files    prometheus.d    ${BASELINE_TARGET_FILES}
            Run Keyword And Continue On Failure
            ...    Directory Should Contain Only Added Files    rules.d    ${BASELINE_RULE_FILES}
        END
    FINALLY
        Ensure Prometheus Is Active
    END

Delete Test Publisher Data
    ${entries} =    Copy List    ${FIXTURE_FIELDS}
    FOR    ${entry}    IN    @{entries}
        ${status}    ${detail} =    Run Keyword And Ignore Error
        ...    Delete Publisher Hash Field    ${entry}[0]    ${entry}[1]
        IF    $status == 'PASS'
            Remove Values From List    ${FIXTURE_FIELDS}    ${entry}
        ELSE
            Run Keyword And Continue On Failure    Fail    ${detail}
        END
    END

Encode Transport Value
    [Arguments]    ${value}
    # Encoding only: all fixture construction and assertions live in Robot.
    ${encoded} =    Evaluate
    ...    base64.b64encode($value if isinstance($value, bytes) else $value.encode('utf-8')).decode('ascii')
    ...    modules=base64
    RETURN    ${encoded}

Quote Shell Argument
    [Arguments]    ${value}
    ${quoted} =    Evaluate    shlex.quote(str($value))    modules=shlex
    RETURN    ${quoted}

Write Publisher Hash Field
    [Arguments]    ${redis_key}    ${field_name}    ${value}
    ${key} =    Encode Transport Value    ${redis_key}
    ${field} =    Encode Transport Value    ${field_name}
    ${entry} =    Create List    ${redis_key}    ${field_name}
    IF    $entry not in $FIXTURE_FIELDS
        ${exists}    ${rc} =    Execute Command
        ...    runagent python3 -c 'import agent,base64; print(int(agent.redis_connect(privileged=True).hexists(base64.b64decode("${key}"),base64.b64decode("${field}"))))'
        ...    return_rc=${True}
        Should Be Equal As Integers    ${rc}    0    ${exists}
        Should Be Equal    ${exists}    0    Refusing to overwrite a pre-existing Redis field
        # Register before HSET so a lost SSH response cannot leak a fixture.
        Append To List    ${FIXTURE_FIELDS}    ${entry}
    END
    ${encoded} =    Encode Transport Value    ${value}
    ${output}    ${rc} =    Execute Command
    ...    runagent python3 -c 'import agent,base64; agent.redis_connect(privileged=True).hset(base64.b64decode("${key}"),base64.b64decode("${field}"),base64.b64decode("${encoded}"))'
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}

Delete Publisher Hash Field
    [Arguments]    ${redis_key}    ${field_name}
    ${key} =    Encode Transport Value    ${redis_key}
    ${field} =    Encode Transport Value    ${field_name}
    ${output}    ${rc} =    Execute Command
    ...    runagent python3 -c 'import agent,base64; r=agent.redis_connect(privileged=True); r.hdel(base64.b64decode("${key}"),base64.b64decode("${field}")); print(int(r.hexists(base64.b64decode("${key}"),base64.b64decode("${field}"))))'
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}
    Should Be Equal    ${output}    0

Write Publisher Target
    [Arguments]    ${module_id}    ${payload}    ${field_name}=${TARGET_FIELD}
    Write Publisher Hash Field
    ...    module/${module_id}/metrics_targets
    ...    ${field_name}
    ...    ${payload}

Write Publisher Rule
    [Arguments]    ${module_id}    ${field_name}    ${payload}
    Write Publisher Hash Field
    ...    module/${module_id}/metrics_alert_rules
    ...    ${field_name}
    ...    ${payload}

Delete Publisher Rule Field
    [Arguments]    ${module_id}    ${field_name}
    Delete Publisher Hash Field    module/${module_id}/metrics_alert_rules    ${field_name}

Signal Module Rule Change
    [Arguments]    ${module_id}
    ${event_cursor} =    Journal Cursor
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${module_id} redis-exec PUBLISH module/${module_id}/event/metrics-alert-rules-changed '{}'
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}
    RETURN    ${event_cursor}

Signal Metrics Target Change
    [Arguments]    ${module_id}
    ${event_cursor} =    Journal Cursor
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${module_id} redis-exec PUBLISH module/${module_id}/event/metrics-target-changed '{}'
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
    [Arguments]    ${module_id}    @{expected_fragments}
    Module File Should Contain    prometheus.d/provision_${module_id}_${TARGET_FIELD}.yml    @{expected_fragments}

Provider Rule File Should Contain
    [Arguments]    ${module_id}    ${field_name}    @{expected_fragments}
    Module File Should Contain    rules.d/provision_${module_id}_${field_name}.yml    @{expected_fragments}

Provider Rule File Should Exist
    [Arguments]    ${module_id}    ${field_name}
    Module File Should Exist    rules.d/provision_${module_id}_${field_name}.yml

Provider Rule File Should Be Absent
    [Arguments]    ${module_id}    ${field_name}
    Module File Should Be Absent    rules.d/provision_${module_id}_${field_name}.yml

Provider Rule Checksum
    [Arguments]    ${module_id}    ${field_name}
    ${checksum} =    Module File Checksum    rules.d/provision_${module_id}_${field_name}.yml
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
    ${config} =    Read Module YAML    alertmanager.yml
    ${group_by} =    Create List    alertname    node    module_id
    ${equal} =    Create List    alertname    module_id
    Lists Should Be Equal    ${config}[route][group_by]    ${group_by}
    Lists Should Be Equal    ${config}[inhibit_rules][0][equal]    ${equal}

Journal Should Contain
    [Arguments]    ${since_epoch}    ${message}
    ${journal} =    Execute Command    journalctl -S @${since_epoch} --no-pager
    Should Contain    ${journal}    ${message}

Journal Cursor
    ${cursor} =    Execute Command
    ...    journalctl -n 0 --show-cursor --no-pager | sed -n 's/^-- cursor: //p'
    Should Not Be Empty    ${cursor}
    RETURN    ${cursor}

Module Event Should Complete
    [Arguments]    ${cursor}    ${module_id}    ${event_name}
    ${rc} =    Execute Command
    ...    journalctl --after-cursor '${cursor}' --no-pager | grep -F -q -- 'Handler of module/${module_id}/event/${event_name} exited with status "completed"'
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

Provision Rule Fixtures
    ${cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}
    Wait For Provisioning Event    ${cursor}    metrics-alert-rules-changed    180s
    RETURN    ${cursor}

Provision Target Fixtures
    ${cursor} =    Signal Metrics Target Change    ${RULE_PUBLISHER_A}
    Wait For Provisioning Event    ${cursor}    metrics-target-changed    90s
    RETURN    ${cursor}

Wait For Provisioning Event
    [Arguments]    ${cursor}    ${event_name}    ${timeout}
    # An aborted handler is a finished event, not a reason to poll for minutes.
    ${result} =    Wait Until Keyword Succeeds    ${timeout}    2s
    ...    Provisioning Event Should Have Exited    ${cursor}    ${event_name}
    Should Contain    ${result}    exited with status "completed"

Provisioning Event Should Have Exited
    [Arguments]    ${cursor}    ${event_name}
    ${journal} =    Read Journal After Cursor    ${cursor}
    ${result} =    Get Lines Containing String    ${journal}
    ...    Handler of module/${RULE_PUBLISHER_A}/event/${event_name} exited with status
    Should Not Be Empty    ${result}
    RETURN    ${result}

Read Journal After Cursor
    [Arguments]    ${cursor}
    ${quoted} =    Quote Shell Argument    ${cursor}
    ${journal}    ${rc} =    Execute Command
    ...    journalctl --after-cursor ${quoted} --no-pager
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${journal}
    RETURN    ${journal}

Source Journal Should Contain
    [Arguments]    ${cursor}    ${source}    @{messages}
    ${journal} =    Read Journal After Cursor    ${cursor}
    ${lines} =    Get Lines Containing String    ${journal}    ${source}
    Should Not Be Empty    ${lines}    No journal entry for ${source}
    FOR    ${message}    IN    @{messages}
        Should Contain    ${lines}    ${message}
    END

Source Journal Should Not Contain
    [Arguments]    ${cursor}    ${source}    ${message}
    ${journal} =    Read Journal After Cursor    ${cursor}
    ${lines} =    Get Lines Containing String    ${journal}    ${source}
    Should Not Contain    ${lines}    ${message}

Read Module File
    [Arguments]    ${path}
    ${quoted} =    Quote Shell Argument    ${path}
    ${content}    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} cat -- ${quoted}
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${content}
    RETURN    ${content}

Read Module YAML
    [Arguments]    ${path}
    ${content} =    Read Module File    ${path}
    ${document} =    Evaluate    yaml.safe_load($content)    modules=yaml
    RETURN    ${document}

Serialize Fixture YAML
    [Arguments]    ${document}
    # Robot dictionaries are DotDict instances; JSON normalizes them to ordinary
    # mappings before YAML encoding. This helper only serializes fixture data.
    ${payload} =    Evaluate
    ...    yaml.safe_dump(json.loads(json.dumps($document)), sort_keys=False)
    ...    modules=yaml,json
    RETURN    ${payload}

Module File Should Contain
    [Arguments]    ${path}    @{fragments}
    ${content} =    Read Module File    ${path}
    FOR    ${fragment}    IN    @{fragments}
        Should Contain    ${content}    ${fragment}
    END

Module File Should Exist
    [Arguments]    ${path}
    ${quoted} =    Quote Shell Argument    ${path}
    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} /usr/bin/test -f ${quoted}
    ...    return_rc=${True}    return_stdout=${False}
    Should Be Equal As Integers    ${rc}    0    Missing file: ${path}

Module File Should Be Absent
    [Arguments]    ${path}
    # An overlong pathname must not pass merely because test(1) got ENAMETOOLONG.
    ${directory}    ${name} =    Split String From Right    ${path}    /    1
    ${files} =    Module Directory Files    ${directory}
    List Should Not Contain Value    ${files}    ${name}

Module Directory Files
    [Arguments]    ${directory}
    ${quoted} =    Quote Shell Argument    ${directory}
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} find ${quoted} -mindepth 1 -maxdepth 1 -printf '%f\n'
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}
    ${files} =    Split To Lines    ${output}
    Sort List    ${files}
    RETURN    ${files}

Directory Should Contain Only Added Files
    [Arguments]    ${directory}    ${baseline}    @{added}
    ${expected} =    Copy List    ${baseline}
    FOR    ${name}    IN    @{added}
        IF    $name not in $expected    Append To List    ${expected}    ${name}
    END
    Sort List    ${expected}
    ${actual} =    Module Directory Files    ${directory}
    Lists Should Be Equal    ${actual}    ${expected}

Module File Checksum
    [Arguments]    ${path}
    ${quoted} =    Quote Shell Argument    ${path}
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} sha256sum -- ${quoted}
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}
    ${checksum} =    Fetch From Left    ${output}    ${SPACE}
    Should Match Regexp    ${checksum}    ^[0-9a-f]{64}$
    RETURN    ${checksum}

Write New Fixture File
    [Arguments]    ${path}    ${content}
    ${quoted} =    Quote Shell Argument    ${path}
    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} /usr/bin/test ! -e ${quoted}
    ...    return_rc=${True}    return_stdout=${False}
    Should Be Equal As Integers    ${rc}    0    Refusing to overwrite ${path}
    Append To List    ${FIXTURE_FILES}    ${path}
    ${encoded} =    Encode Transport Value    ${content}
    ${script} =    Quote Shell Argument    set -C; printf '%s' '${encoded}' | base64 -d > ${quoted}
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} sh -c ${script}
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}

Remove Fixture File
    [Arguments]    ${path}
    ${quoted} =    Quote Shell Argument    ${path}
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} rm -f -- ${quoted}
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}

Target Document Should Have Labels
    [Arguments]    ${module_id}    ${field}    ${expected_labels}
    ${document} =    Read Module YAML    prometheus.d/provision_${module_id}_${field}.yml
    Length Should Be    ${document}    1
    Dictionaries Should Be Equal    ${document}[0][labels]    ${expected_labels}
    ${targets} =    Create List    127.0.0.1:19091
    Lists Should Be Equal    ${document}[0][targets]    ${targets}

Invalid Target Should Be Isolated
    [Arguments]    ${key}    ${field}    ${payload}    ${source}    ${warning}
    ${baseline} =    Module Directory Files    prometheus.d
    Write Publisher Hash Field    ${key}    ${field}    ${payload}
    ${valid} =    Target Payload Without Labels    19091
    Write Publisher Target    ${RULE_PUBLISHER_A}    ${valid}
    Write Publisher Target    ${RULE_PUBLISHER_B}    ${valid}
    ${cursor} =    Provision Target Fixtures
    Source Journal Should Contain    ${cursor}    ${source}    ${warning}
    FOR    ${publisher}    IN    ${RULE_PUBLISHER_A}    ${RULE_PUBLISHER_B}
        ${labels} =    Create Dictionary    module_id=${publisher}    target_type=${TARGET_FIELD}
        Target Document Should Have Labels    ${publisher}    ${TARGET_FIELD}    ${labels}
    END
    Directory Should Contain Only Added Files    prometheus.d    ${baseline}
    ...    provision_${RULE_PUBLISHER_A}_${TARGET_FIELD}.yml
    ...    provision_${RULE_PUBLISHER_B}_${TARGET_FIELD}.yml

Target Structure Should Be Rejected
    [Arguments]    ${payload}    ${warning}
    Invalid Target Should Be Isolated
    ...    module/${RULE_PUBLISHER_A}/metrics_targets    phase5invalid    ${payload}
    ...    Skipped target 'phase5invalid'    ${warning}

Target Field Should Be Rejected
    [Arguments]    ${field}
    ${payload} =    Target Payload Without Labels    19091
    Invalid Target Should Be Isolated
    ...    module/${RULE_PUBLISHER_A}/metrics_targets    ${field}    ${payload}
    ...    at Redis key 'module/${RULE_PUBLISHER_A}/metrics_targets'    invalid target type
    Delete Publisher Hash Field    module/${RULE_PUBLISHER_A}/metrics_targets    ${field}

Target Publisher Should Be Rejected
    [Arguments]    ${module_id}    ${warning}
    ${payload} =    Target Payload Without Labels    19091
    Invalid Target Should Be Isolated
    ...    module/${module_id}/metrics_targets    phase5invalid    ${payload}
    ...    metrics_targets    ${warning}
    Delete Publisher Hash Field    module/${module_id}/metrics_targets    phase5invalid

Single Alert Rule Document
    [Arguments]    ${alert}=Phase5SchemaAlert    ${expression}=up == 0
    ${payload} =    Single Alert Rule Payload    ${alert}    ${expression}
    ${document} =    Evaluate    yaml.safe_load($payload)    modules=yaml
    RETURN    ${document}

Full Alert Rule Document
    [Arguments]    @{names}
    ${groups} =    Create List
    FOR    ${name}    IN    @{names}
        ${rule} =    Single Alert Rule Document
        ${rules} =    Create List    ${rule}
        ${labels} =    Create Dictionary    environment=production
        ${group} =    Create Dictionary    name=${name}    rules=${rules}    labels=${labels}
        Append To List    ${groups}    ${group}
    END
    ${document} =    Create Dictionary    groups=${groups}
    RETURN    ${document}

Rule Payload Should Be Rejected
    [Arguments]    ${payload}    ${warning}
    ${baseline} =    Module Directory Files    rules.d
    ${valid} =    Single Alert Rule Payload    Phase5ValidNeighbour    up == 0
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${SECONDARY_RULE_FIELD}    ${valid}
    Write Publisher Rule    ${RULE_PUBLISHER_B}    ${SECONDARY_RULE_FIELD}    ${valid}
    Write Publisher Hash Field
    ...    module/${RULE_PUBLISHER_A}/metrics_alert_rules    ${PRIMARY_RULE_FIELD}    ${payload}
    ${cursor} =    Provision Rule Fixtures
    Source Journal Should Contain
    ...    ${cursor}    module/${RULE_PUBLISHER_A}/metrics_alert_rules field '${PRIMARY_RULE_FIELD}'
    ...    Skipped module alert rule    ${warning}
    Provider Rule File Should Be Absent    ${RULE_PUBLISHER_A}    ${PRIMARY_RULE_FIELD}
    Prometheus Rule Should Be Absent    Phase5SchemaAlert
    Prometheus Rule Count Should Be    Phase5ValidNeighbour    2
    Directory Should Contain Only Added Files    rules.d    ${baseline}
    ...    provision_${RULE_PUBLISHER_A}_${SECONDARY_RULE_FIELD}.yml
    ...    provision_${RULE_PUBLISHER_B}_${SECONDARY_RULE_FIELD}.yml

Rule Schema Should Be Rejected
    [Arguments]    ${location}    ${key}    ${value}    ${warning}
    ${document} =    Full Alert Rule Document    schema
    IF    $location == 'document'
        ${subject} =    Set Variable    ${document}
    ELSE IF    $location == 'group'
        ${subject} =    Set Variable    ${document}[groups][0]
    ELSE
        ${subject} =    Set Variable    ${document}[groups][0][rules][0]
    END
    IF    $key == '__self__'
        IF    $location == 'document'
            ${document} =    Set Variable    ${value}
        ELSE IF    $location == 'group'
            Set List Value    ${document}[groups]    0    ${value}
        ELSE
            Set List Value    ${document}[groups][0][rules]    0    ${value}
        END
    ELSE IF    $value == '__remove__'
        Remove From Dictionary    ${subject}    ${key}
    ELSE
        Set To Dictionary    ${subject}    ${key}    ${value}
        IF    $key == 'record'    Remove From Dictionary    ${subject}    alert
    END
    ${payload} =    Serialize Fixture YAML    ${document}
    Rule Payload Should Be Rejected    ${payload}    ${warning}

Local Group Names Should Be Rejected
    [Arguments]    ${warning}    @{names}
    ${document} =    Full Alert Rule Document    @{names}
    ${payload} =    Serialize Fixture YAML    ${document}
    Rule Payload Should Be Rejected    ${payload}    ${warning}

Read Provider Rule Document
    [Arguments]    ${publisher}=${RULE_PUBLISHER_A}    ${field}=${PRIMARY_RULE_FIELD}
    ${document} =    Read Module YAML    rules.d/provision_${publisher}_${field}.yml
    RETURN    ${document}

Provider Group Names
    [Arguments]    ${publisher}=${RULE_PUBLISHER_A}    ${field}=${PRIMARY_RULE_FIELD}
    ${document} =    Read Provider Rule Document    ${publisher}    ${field}
    ${names} =    Create List
    FOR    ${group}    IN    @{document}[groups]
        Append To List    ${names}    ${group}[name]
    END
    RETURN    ${names}

Loaded Rule Should Have Expression
    [Arguments]    ${alert}    ${expression}    ${publisher}=${RULE_PUBLISHER_A}
    ${output}    ${rc} =    Execute Command
    ...    curl -fsS ${PROMETHEUS_BASE_URL}/api/v1/rules
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}
    ${response} =    Evaluate    json.loads($output)    modules=json
    Should Be Equal    ${response}[status]    success
    ${matches} =    Create List
    FOR    ${group}    IN    @{response}[data][groups]
        FOR    ${rule}    IN    @{group}[rules]
            IF    $rule['name'] == $alert
                Should Be Equal    ${rule}[labels][module_id]    ${publisher}
                Should Be Equal    ${rule}[query]    ${expression}
                Append To List    ${matches}    ${rule}
            END
        END
    END
    Length Should Be    ${matches}    1

Pinned Promtool Format
    [Arguments]    ${expression}
    ${quoted} =    Quote Shell Argument    ${expression}
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} podman run --rm --network=none --entrypoint=/bin/promtool ${PINNED_PROMETHEUS_IMAGE} --experimental promql format -- ${quoted}
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}
    RETURN    ${output}

PromQL Should Be Scoped
    [Arguments]    ${authored}    ${expected}    ${warn}=${False}
    # Explicit semantic expectations are canonicalized by the real pinned parser,
    # never by the rewrite operation under test (notably unary and label ordering).
    ${document} =    Single Alert Rule Document    Phase5PromqlAlert
    Set To Dictionary    ${document}    expr    ${authored}
    ${payload} =    Serialize Fixture YAML    ${document}
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${PRIMARY_RULE_FIELD}    ${payload}
    ${cursor} =    Provision Rule Fixtures
    ${expected} =    Pinned Promtool Format    ${expected}
    ${actual} =    Read Provider Rule Document
    Should Be Equal    ${actual}[groups][0][rules][0][expr]    ${expected}
    Should Be Equal    ${actual}[groups][0][rules][0][labels][module_id]    ${RULE_PUBLISHER_A}
    Loaded Rule Should Have Expression    Phase5PromqlAlert    ${expected}
    ${source} =    Set Variable    module/${RULE_PUBLISHER_A}/metrics_alert_rules field '${PRIMARY_RULE_FIELD}'
    IF    $warn
        Source Journal Should Contain    ${cursor}    ${source}
        ...    supplied a broader, partial, negative, regular-expression, mixed, or conflicting module_id matcher
        ...    using module ID '${RULE_PUBLISHER_A}'
    ELSE
        Source Journal Should Not Contain    ${cursor}    ${source}    using module ID
    END

PromQL Should Be Rejected
    [Arguments]    ${expression}    ${warning}
    ${document} =    Single Alert Rule Document
    Set To Dictionary    ${document}    expr    ${expression}
    ${payload} =    Serialize Fixture YAML    ${document}
    Rule Payload Should Be Rejected    ${payload}    ${warning}

Maximum Filename Field
    [Arguments]    ${publisher}
    ${fixed} =    Get Length    provision_${publisher}_.yml
    ${padding} =    Generate Random String    255    x
    ${field} =    Get Substring    ${padding}    ${fixed}
    Length Should Be    provision_${publisher}_${field}.yml    255
    RETURN    ${field}

Rule Identity Should Be Enforced
    [Arguments]    ${group_owner}    ${rule_owner}    ${warn}
    ${document} =    Full Alert Rule Document    identity
    ${group} =    Set Variable    ${document}[groups][0]
    ${rule} =    Set Variable    ${group}[rules][0]
    Set To Dictionary    ${group}[labels]    module_id    ${group_owner}
    Set To Dictionary    ${rule}[labels]    module_id    ${rule_owner}
    ${payload} =    Serialize Fixture YAML    ${document}
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${PRIMARY_RULE_FIELD}    ${payload}
    ${cursor} =    Provision Rule Fixtures
    ${actual} =    Read Provider Rule Document
    Set To Dictionary    ${group}[labels]    module_id    ${RULE_PUBLISHER_A}
    Set To Dictionary    ${rule}[labels]    module_id    ${RULE_PUBLISHER_A}
    Dictionaries Should Be Equal    ${actual}[groups][0][labels]    ${group}[labels]
    Dictionaries Should Be Equal    ${actual}[groups][0][rules][0][labels]    ${rule}[labels]
    Prometheus Rule Should Exist    Phase5SchemaAlert
    ${source} =    Set Variable    module/${RULE_PUBLISHER_A}/metrics_alert_rules field '${PRIMARY_RULE_FIELD}'
    IF    $warn
        Source Journal Should Contain
        ...    ${cursor}    group 'ns8:${RULE_PUBLISHER_A}:${PRIMARY_RULE_FIELD}:identity' from ${source}
        ...    has module_id '${group_owner}'; using module ID '${RULE_PUBLISHER_A}'
        Source Journal Should Contain    ${cursor}    alert 'Phase5SchemaAlert' from ${source}
        ...    has module_id '${rule_owner}'; using module ID '${RULE_PUBLISHER_A}'
    ELSE
        Source Journal Should Not Contain    ${cursor}    ${source}    using module ID
    END

Rule Metadata Should Be Retained
    [Arguments]    ${case}    ${value}
    ${document} =    Single Alert Rule Document
    IF    $case == 'severity'
        Set To Dictionary    ${document}[labels]    severity    ${value}
        ${warning} =    Set Variable    unsupported severity '${value}'
    ELSE
        ${annotations} =    Create Dictionary    ${value}    Authored summary
        Set To Dictionary    ${document}    annotations    ${annotations}
        ${warning} =    Set Variable    missing bilingual annotations: description_en, description_it, summary_it
    END
    ${payload} =    Serialize Fixture YAML    ${document}
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${PRIMARY_RULE_FIELD}    ${payload}
    ${cursor} =    Provision Rule Fixtures
    ${actual} =    Read Provider Rule Document
    ${rule} =    Set Variable    ${actual}[groups][0][rules][0]
    Set To Dictionary    ${document}[labels]    module_id    ${RULE_PUBLISHER_A}
    Dictionaries Should Be Equal    ${rule}[labels]    ${document}[labels]
    Dictionaries Should Be Equal    ${rule}[annotations]    ${document}[annotations]
    Prometheus Rule Should Exist    Phase5SchemaAlert
    Source Journal Should Contain    ${cursor}
    ...    alert 'Phase5SchemaAlert' from module/${RULE_PUBLISHER_A}/metrics_alert_rules field '${PRIMARY_RULE_FIELD}'
    ...    ${warning}

Rule Encoding Should Be Rejected
    [Arguments]    ${encoding}    ${warning}
    IF    $encoding == 'utf8'
        ${payload} =    Single Alert Rule Payload    Phase5SchemaAlert    up == 0
        # Everything except this annotation byte is a valid, complete rule.
        ${payload} =    Replace String    ${payload}    Phase 5 alert    \xff
        ${payload} =    Convert To Bytes    ${payload}
    ELSE
        ${payload} =    Set Variable    groups: [\n
    END
    Rule Payload Should Be Rejected    ${payload}    ${warning}

Rule File Checksums
    [Arguments]    ${files}
    ${checksums} =    Create Dictionary
    FOR    ${file}    IN    @{files}
        ${checksum} =    Module File Checksum    rules.d/${file}
        Set To Dictionary    ${checksums}    ${file}    ${checksum}
    END
    RETURN    ${checksums}
