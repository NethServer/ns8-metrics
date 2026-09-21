*** Settings ***
Library    SSHLibrary
Library    String
Library    Collections
Library    OperatingSystem    WITH NAME    LocalFiles


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
@{PENDING_WRITES}
${CLEANUP_PENDING}    ${True}
${PHASE_SNAPSHOT}    ${None}
${PHASE_CURSOR}    ${None}
${PHASE_JOURNAL}    ${None}
${INITIAL_PROMETHEUS_ACTIVE}    ${True}


*** Keywords ***
Initialize Module Rule Fixtures
    ${script} =    LocalFiles.Get File    ${CURDIR}/module_alert_rules_transport.py
    ${script} =    Quote Shell Argument    ${script}
    Set Suite Variable    ${TRANSPORT_SCRIPT}    ${script}
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
    # Batch expectation formatting uses promtool in the running service.
    ${running_image} =    Execute Command
    ...    runagent -m ${METRICS_ID} podman inspect --format '{{.ImageName}}' prometheus
    Should Be Equal    ${running_image}    ${PINNED_PROMETHEUS_IMAGE}
    ${targets} =    Module Directory Files    prometheus.d
    ${rules} =    Module Directory Files    rules.d
    Set Suite Variable    ${BASELINE_TARGET_FILES}    ${targets}
    Set Suite Variable    ${BASELINE_RULE_FILES}    ${rules}

Finish Module Rule Fixtures
    TRY
        IF    $CLEANUP_PENDING    Reset Module Rule Fixtures
    FINALLY
        IF    not $INITIAL_PROMETHEUS_ACTIVE
            ${output}    ${rc} =    Execute Command
            ...    runagent -m ${METRICS_ID} systemctl --user stop prometheus.service
            ...    return_rc=${True}
            Should Be Equal As Integers    ${rc}    0    ${output}
        END
    END

Prepare Module Rule Fixtures
    IF    $CLEANUP_PENDING    Reset Module Rule Fixtures
    ${cases} =    Create List
    Set Test Variable    ${BATCH_CASES}    ${cases}

Reset Module Rule Fixtures
    Set Suite Variable    ${CLEANUP_PENDING}    ${True}
    ${status}    ${detail} =    Run Keyword And Ignore Error    Clean Module Rule Fixtures
    IF    $status == 'PASS'
        Set Suite Variable    ${CLEANUP_PENDING}    ${False}
    ELSE
        Fail    ${detail}
    END

Clean Module Rule Fixtures
    Invalidate Fixture Snapshot
    # Unsent writes need no cleanup. Registered fields survive failed writes
    # and failed teardown, so the next setup can retry without leaking fixtures.
    ${pending} =    Create List
    Set Suite Variable    ${PENDING_WRITES}    ${pending}
    TRY
        Run Keyword And Continue On Failure    Delete Test Publisher Data
        Run Keyword And Continue On Failure    Remove Fixture Files
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
    IF    not $FIXTURE_FIELDS    RETURN
    ${entries} =    Copy List    ${FIXTURE_FIELDS}
    ${encoded} =    Encode Redis Entries    ${entries}
    ${results} =    Execute Fixture Transport    delete    ${encoded}
    ${expected_length} =    Evaluate    len($entries) * 2
    Length Should Be    ${results}    ${expected_length}
    FOR    ${index}    ${entry}    IN ENUMERATE    @{entries}
        ${deleted} =    Set Variable    ${results}[${index * 2}]
        ${exists} =    Set Variable    ${results}[${index * 2 + 1}]
        IF    isinstance($deleted, int) and $exists is False
            Remove Values From List    ${FIXTURE_FIELDS}    ${entry}
        ELSE
            Run Keyword And Continue On Failure    Fail    Cleanup failed for ${entry}: ${deleted}, ${exists}
        END
    END

Remove Fixture Files
    IF    not $FIXTURE_FILES    RETURN
    ${paths} =    Create List
    FOR    ${path}    IN    @{FIXTURE_FILES}
        ${quoted} =    Quote Shell Argument    ${path}
        Append To List    ${paths}    ${quoted}
    END
    ${arguments} =    Catenate    SEPARATOR=${SPACE}    @{paths}
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} rm -f -- ${arguments}
    ...    return_rc=${True}
    IF    $rc == 0
        ${empty} =    Create List
        Set Suite Variable    ${FIXTURE_FILES}    ${empty}
    ELSE
        Fail    Fixture file cleanup failed: ${output}
    END

Encode Redis Entries
    [Arguments]    ${entries}
    ${encoded} =    Create List
    FOR    ${entry}    IN    @{entries}
        ${row} =    Create List
        FOR    ${value}    IN    @{entry}
            ${value} =    Encode Transport Value    ${value}
            Append To List    ${row}    ${value}
        END
        Append To List    ${encoded}    ${row}
    END
    RETURN    ${encoded}

Execute Fixture Transport
    [Arguments]    ${operation}    ${values}    ${scope}=${EMPTY}
    ${json} =    Evaluate    json.dumps($values)    modules=json
    ${encoded} =    Encode Transport Value    ${json}
    ${output}    ${rc} =    Execute Command
    ...    runagent ${scope} python3 -c ${TRANSPORT_SCRIPT} ${operation} ${encoded}
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}
    ${result} =    Evaluate    json.loads($output)    modules=json
    RETURN    ${result}

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
    ${entry} =    Create List    ${redis_key}    ${field_name}    ${value}
    Append To List    ${PENDING_WRITES}    ${entry}

Flush Publisher Writes
    IF    not $PENDING_WRITES    RETURN
    ${new} =    Create List
    FOR    ${write}    IN    @{PENDING_WRITES}
        ${entry} =    Create List    ${write}[0]    ${write}[1]
        IF    $entry not in $FIXTURE_FIELDS and $entry not in $new
            Append To List    ${new}    ${entry}
        END
    END
    IF    $new
        ${encoded} =    Encode Redis Entries    ${new}
        ${exists} =    Execute Fixture Transport    exists    ${encoded}
        ${count} =    Get Length    ${new}
        Length Should Be    ${exists}    ${count}
        FOR    ${entry}    ${present}    IN ZIP    ${new}    ${exists}
            Should Be Equal    ${present}    ${False}    Refusing to overwrite a pre-existing Redis field: ${entry}
        END
        # Register the entire batch BEFORE any mutation, including partial or
        # interrupted pipeline execution. Pre-existing fields never get owned.
        FOR    ${entry}    IN    @{new}
            Append To List    ${FIXTURE_FIELDS}    ${entry}
        END
    END
    ${encoded} =    Encode Redis Entries    ${PENDING_WRITES}
    ${results} =    Execute Fixture Transport    write    ${encoded}
    ${count} =    Get Length    ${PENDING_WRITES}
    Length Should Be    ${results}    ${count}
    FOR    ${result}    IN    @{results}
        Should Be True    isinstance($result, int)    Redis write failed: ${result}
    END
    ${empty} =    Create List
    Set Suite Variable    ${PENDING_WRITES}    ${empty}

Delete Publisher Hash Field
    [Arguments]    ${redis_key}    ${field_name}
    Flush Publisher Writes
    ${entry} =    Create List    ${redis_key}    ${field_name}
    List Should Contain Value    ${FIXTURE_FIELDS}    ${entry}    Refusing to delete an unregistered field
    ${entries} =    Create List    ${entry}
    ${encoded} =    Encode Redis Entries    ${entries}
    ${results} =    Execute Fixture Transport    delete    ${encoded}
    Should Be True    isinstance($results[0], int)    ${results}
    Should Be Equal    ${results}[1]    ${False}
    # Keep registration for later replacement writes and idempotent cleanup.

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
    Flush Publisher Writes
    Invalidate Fixture Snapshot
    ${event_cursor} =    Journal Cursor
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${module_id} redis-exec PUBLISH module/${module_id}/event/metrics-alert-rules-changed '{}'
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}
    RETURN    ${event_cursor}

Signal Metrics Target Change
    [Arguments]    ${module_id}
    Flush Publisher Writes
    Invalidate Fixture Snapshot
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

Read Loaded Rules
    IF    $PHASE_SNAPSHOT is not None
        ${response} =    Set Variable    ${PHASE_SNAPSHOT}[rules]
    ELSE
        ${output}    ${rc} =    Execute Command
        ...    curl -fsS ${PROMETHEUS_BASE_URL}/api/v1/rules
        ...    return_rc=${True}
        Should Be Equal As Integers    ${rc}    0    ${output}
        ${response} =    Evaluate    json.loads($output)    modules=json
    END
    Should Be Equal    ${response}[status]    success
    ${rules} =    Create List
    FOR    ${group}    IN    @{response}[data][groups]
        Append To List    ${rules}    @{group}[rules]
    END
    RETURN    ${rules}

Prometheus Rule Should Exist
    [Arguments]    ${alert_name}
    ${rules} =    Read Loaded Rules
    ${namespace} =    Create Dictionary    alert_name=${alert_name}
    ${matches} =    Evaluate    [r for r in $rules if r['name'] == alert_name]    namespace=${namespace}
    Should Not Be Empty    ${matches}    Missing alert: ${alert_name}

Prometheus Rule Should Be Absent
    [Arguments]    ${alert_name}
    Prometheus Rule Count Should Be    ${alert_name}    0

Prometheus Rule Count Should Be
    [Arguments]    ${alert_name}    ${expected_count}
    ${rules} =    Read Loaded Rules
    ${namespace} =    Create Dictionary    alert_name=${alert_name}
    ${matches} =    Evaluate    [r for r in $rules if r['name'] == alert_name]    namespace=${namespace}
    Length Should Be    ${matches}    ${expected_count}

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

Journal Cursor
    ${cursor} =    Execute Command
    ...    journalctl -n 0 --show-cursor --no-pager | sed -n 's/^-- cursor: //p'
    Should Not Be Empty    ${cursor}
    RETURN    ${cursor}

Module Event Should Complete
    [Arguments]    ${cursor}    ${module_id}    ${event_name}
    ${journal} =    Read Journal After Cursor    ${cursor}
    ${completed} =    Set Variable    Handler of module/${module_id}/event/${event_name} exited with status "completed"
    IF    $completed in $journal
        Set Suite Variable    ${PHASE_CURSOR}    ${cursor}
        Set Suite Variable    ${PHASE_JOURNAL}    ${journal}
        Capture Fixture Snapshot
    ELSE
        Fail    Event has not completed: ${journal}
    END

Ensure Prometheus Is Active
    IF    $PHASE_SNAPSHOT is not None and $PHASE_SNAPSHOT['rules'] is not None    RETURN
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} systemctl --user start prometheus.service
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}
    Wait Until Keyword Succeeds    60s    2s
    ...    Active Fixture Snapshot Should Be Ready

Active Fixture Snapshot Should Be Ready
    Capture Fixture Snapshot
    Should Not Be Equal    ${PHASE_SNAPSHOT}[rules]    ${None}

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
    Capture Fixture Snapshot

Provisioning Event Should Have Exited
    [Arguments]    ${cursor}    ${event_name}
    ${journal} =    Read Journal After Cursor    ${cursor}
    ${result} =    Get Lines Containing String    ${journal}
    ...    Handler of module/${RULE_PUBLISHER_A}/event/${event_name} exited with status
    # Teardown continues after failed assertions: only cache a finished event.
    IF    $result
        Set Suite Variable    ${PHASE_CURSOR}    ${cursor}
        Set Suite Variable    ${PHASE_JOURNAL}    ${journal}
    ELSE
        Fail    Event has not exited: ${journal}
    END
    RETURN    ${result}

Read Journal After Cursor
    [Arguments]    ${cursor}
    IF    $cursor == $PHASE_CURSOR    RETURN    ${PHASE_JOURNAL}
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
    IF    $PHASE_SNAPSHOT is not None
        RETURN    ${PHASE_SNAPSHOT}[files][${path}][content]
    END
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
    IF    $PHASE_SNAPSHOT is not None
        Dictionary Should Contain Key    ${PHASE_SNAPSHOT}[files]    ${path}
        RETURN
    END
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
    IF    $PHASE_SNAPSHOT is not None
        RETURN    ${PHASE_SNAPSHOT}[directories][${directory}]
    END
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
    IF    $PHASE_SNAPSHOT is not None
        RETURN    ${PHASE_SNAPSHOT}[files][${path}][checksum]
    END
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
    Invalidate Fixture Snapshot
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

Target Document Should Have Labels
    [Arguments]    ${module_id}    ${field}    ${expected_labels}
    ${document} =    Read Module YAML    prometheus.d/provision_${module_id}_${field}.yml
    Length Should Be    ${document}    1
    Dictionaries Should Be Equal    ${document}[0][labels]    ${expected_labels}
    ${targets} =    Create List    127.0.0.1:19091
    Lists Should Be Equal    ${document}[0][targets]    ${targets}

New Batch Case
    [Arguments]    ${keyword}
    ${index} =    Get Length    ${BATCH_CASES}
    ${field} =    Set Variable    phase5case${index}
    ${alert} =    Set Variable    Phase5BatchAlert${index}
    ${case} =    Create Dictionary    keyword=${keyword}    field=${field}    alert=${alert}
    Append To List    ${BATCH_CASES}    ${case}
    RETURN    ${case}

Check Batch Cases
    [Arguments]    ${cursor}
    FOR    ${case}    IN    @{BATCH_CASES}
        Run Keyword And Continue On Failure    ${case}[keyword]    ${case}    ${cursor}
    END

Queue Invalid Target
    [Arguments]    ${case}    ${key}    ${field}    ${payload}    ${warning}
    Write Publisher Hash Field    ${key}    ${field}    ${payload}
    ${quoted_key} =    Evaluate    repr($key)
    ${quoted_field} =    Evaluate    repr($field)
    ${source} =    Set Variable    ${quoted_key}
    IF    $warning != 'invalid Redis key shape'
        ${module_id} =    Evaluate    $key.split('/')[1]
        ${quoted_module} =    Evaluate    repr($module_id)
        ${source} =    Set Variable    Skipped target ${quoted_field} for module ${quoted_module} at Redis key ${quoted_key}
    END
    Set To Dictionary    ${case}    source=${source}    warning=${warning}

Check Rejected Target Case
    [Arguments]    ${case}    ${cursor}
    Source Journal Should Contain    ${cursor}    ${case}[source]    ${case}[warning]

Check Rejected Target Batch
    ${baseline} =    Module Directory Files    prometheus.d
    ${valid} =    Target Payload Without Labels    19091
    Write Publisher Target    ${RULE_PUBLISHER_A}    ${valid}
    Write Publisher Target    ${RULE_PUBLISHER_B}    ${valid}
    ${cursor} =    Provision Target Fixtures
    Check Batch Cases    ${cursor}
    FOR    ${publisher}    IN    ${RULE_PUBLISHER_A}    ${RULE_PUBLISHER_B}
        ${labels} =    Create Dictionary    module_id=${publisher}    target_type=${TARGET_FIELD}
        Target Document Should Have Labels    ${publisher}    ${TARGET_FIELD}    ${labels}
    END
    Directory Should Contain Only Added Files    prometheus.d    ${baseline}
    ...    provision_${RULE_PUBLISHER_A}_${TARGET_FIELD}.yml
    ...    provision_${RULE_PUBLISHER_B}_${TARGET_FIELD}.yml

Queue Target Structure
    [Arguments]    ${payload}    ${warning}
    ${case} =    New Batch Case    Check Rejected Target Case
    Queue Invalid Target    ${case}
    ...    module/${RULE_PUBLISHER_A}/metrics_targets    ${case}[field]    ${payload}    ${warning}

Queue Target Field
    [Arguments]    ${field}
    ${case} =    New Batch Case    Check Rejected Target Case
    ${payload} =    Target Payload Without Labels    19091
    Queue Invalid Target    ${case}
    ...    module/${RULE_PUBLISHER_A}/metrics_targets    ${field}    ${payload}    invalid target type

Queue Target Publisher
    [Arguments]    ${module_id}    ${warning}
    ${case} =    New Batch Case    Check Rejected Target Case
    ${payload} =    Target Payload Without Labels    19091
    Queue Invalid Target    ${case}
    ...    module/${module_id}/metrics_targets    ${case}[field]    ${payload}    ${warning}

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

Queue Rejected Rule
    [Arguments]    ${payload}    ${warning}
    ${case} =    New Batch Case    Check Rejected Rule Case
    Set To Dictionary    ${case}    warning=${warning}
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${case}[field]    ${payload}

Rule Payload Should Be Rejected
    [Arguments]    ${payload}    ${warning}
    Queue Rejected Rule    ${payload}    ${warning}
    Check Rejected Rule Batch

Check Rejected Rule Case
    [Arguments]    ${case}    ${cursor}
    Source Journal Should Contain
    ...    ${cursor}    module/${RULE_PUBLISHER_A}/metrics_alert_rules field '${case}[field]'
    ...    Skipped module alert rule    ${case}[warning]
    Provider Rule File Should Be Absent    ${RULE_PUBLISHER_A}    ${case}[field]

Check Rejected Rule Batch
    ${baseline} =    Module Directory Files    rules.d
    # Sort after every rejected field, including the parser-panic input, so
    # successful validation proves subsequent sources are still processed.
    ${valid_a} =    Single Alert Rule Payload    Phase5ValidNeighbourA    up == 0
    ${valid_b} =    Single Alert Rule Payload    Phase5ValidNeighbourB    up == 0
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${SECONDARY_RULE_FIELD}    ${valid_a}
    Write Publisher Rule    ${RULE_PUBLISHER_B}    ${SECONDARY_RULE_FIELD}    ${valid_b}
    ${cursor} =    Provision Rule Fixtures
    Check Batch Cases    ${cursor}
    Prometheus Rule Should Be Absent    Phase5SchemaAlert
    Prometheus Rule Count Should Be    Phase5ValidNeighbourA    1
    Prometheus Rule Count Should Be    Phase5ValidNeighbourB    1
    Directory Should Contain Only Added Files    rules.d    ${baseline}
    ...    provision_${RULE_PUBLISHER_A}_${SECONDARY_RULE_FIELD}.yml
    ...    provision_${RULE_PUBLISHER_B}_${SECONDARY_RULE_FIELD}.yml

Queue Rule Schema
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
    Queue Rejected Rule    ${payload}    ${warning}

Queue Local Group Names
    [Arguments]    ${warning}    @{names}
    ${document} =    Full Alert Rule Document    @{names}
    ${payload} =    Serialize Fixture YAML    ${document}
    Queue Rejected Rule    ${payload}    ${warning}

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
    ${rules} =    Read Loaded Rules
    ${matches} =    Create List
    FOR    ${rule}    IN    @{rules}
        IF    $rule['name'] == $alert
            Should Be Equal    ${rule}[labels][module_id]    ${publisher}
            Should Be Equal    ${rule}[query]    ${expression}
            Append To List    ${matches}    ${rule}
        END
    END
    Length Should Be    ${matches}    1

Invalidate Fixture Snapshot
    Set Suite Variable    ${PHASE_SNAPSHOT}    ${None}
    Set Suite Variable    ${PHASE_CURSOR}    ${None}
    Set Suite Variable    ${PHASE_JOURNAL}    ${None}

Capture Fixture Snapshot
    ${snapshot} =    Execute Fixture Transport    snapshot    ${PROMETHEUS_BASE_URL}    -m ${METRICS_ID}
    Set Suite Variable    ${PHASE_SNAPSHOT}    ${snapshot}

Queue Scoped PromQL
    [Arguments]    ${authored}    ${expected}    ${warn}=${False}
    ${case} =    New Batch Case    Check Scoped PromQL Case
    Set To Dictionary    ${case}    expected=${expected}    warn=${warn}
    ${document} =    Single Alert Rule Document    ${case}[alert]    ${authored}
    ${payload} =    Serialize Fixture YAML    ${document}
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${case}[field]    ${payload}

Check Scoped PromQL Batch
    # Canonicalize explicit expectations with the pinned parser, never with
    # the rewrite operation under test. No new formatting containers needed.
    ${expressions} =    Create List
    FOR    ${case}    IN    @{BATCH_CASES}
        Append To List    ${expressions}    ${case}[expected]
    END
    ${formatted} =    Execute Fixture Transport    format    ${expressions}    -m ${METRICS_ID}
    ${count} =    Get Length    ${BATCH_CASES}
    Length Should Be    ${formatted}    ${count}
    FOR    ${case}    ${expected}    IN ZIP    ${BATCH_CASES}    ${formatted}
        Set To Dictionary    ${case}    expected=${expected}
    END
    Check Successful Rule Batch

Check Successful Rule Batch
    ${cursor} =    Provision Rule Fixtures
    Check Batch Cases    ${cursor}

Check Scoped PromQL Case
    [Arguments]    ${case}    ${cursor}
    ${actual} =    Read Provider Rule Document    ${RULE_PUBLISHER_A}    ${case}[field]
    Should Be Equal    ${actual}[groups][0][rules][0][expr]    ${case}[expected]
    Should Be Equal    ${actual}[groups][0][rules][0][labels][module_id]    ${RULE_PUBLISHER_A}
    Loaded Rule Should Have Expression    ${case}[alert]    ${case}[expected]
    ${source} =    Set Variable    module/${RULE_PUBLISHER_A}/metrics_alert_rules field '${case}[field]'
    IF    $case['warn']
        Source Journal Should Contain    ${cursor}    ${source}
        ...    supplied a broader, partial, negative, regular-expression, mixed, or conflicting module_id matcher
        ...    using module ID '${RULE_PUBLISHER_A}'
    ELSE
        Source Journal Should Not Contain    ${cursor}    ${source}    using module ID
    END

Queue Rejected PromQL
    [Arguments]    ${expression}    ${warning}
    ${document} =    Single Alert Rule Document
    Set To Dictionary    ${document}    expr    ${expression}
    ${payload} =    Serialize Fixture YAML    ${document}
    Queue Rejected Rule    ${payload}    ${warning}

Maximum Filename Field
    [Arguments]    ${publisher}
    ${fixed} =    Get Length    provision_${publisher}_.yml
    ${padding} =    Generate Random String    255    x
    ${field} =    Get Substring    ${padding}    ${fixed}
    Length Should Be    provision_${publisher}_${field}.yml    255
    RETURN    ${field}

Queue Rule Identity
    [Arguments]    ${group_owner}    ${rule_owner}    ${warn}
    ${case} =    New Batch Case    Check Rule Identity Case
    ${document} =    Full Alert Rule Document    identity
    ${group} =    Set Variable    ${document}[groups][0]
    ${rule} =    Set Variable    ${group}[rules][0]
    Set To Dictionary    ${rule}    alert    ${case}[alert]
    Set To Dictionary    ${group}[labels]    module_id    ${group_owner}
    Set To Dictionary    ${rule}[labels]    module_id    ${rule_owner}
    ${payload} =    Serialize Fixture YAML    ${document}
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${case}[field]    ${payload}
    Set To Dictionary    ${group}[labels]    module_id    ${RULE_PUBLISHER_A}
    Set To Dictionary    ${rule}[labels]    module_id    ${RULE_PUBLISHER_A}
    Set To Dictionary    ${case}    group=${group}    rule=${rule}
    ...    group_owner=${group_owner}    rule_owner=${rule_owner}    warn=${warn}

Check Rule Identity Case
    [Arguments]    ${case}    ${cursor}
    ${actual} =    Read Provider Rule Document    ${RULE_PUBLISHER_A}    ${case}[field]
    Dictionaries Should Be Equal    ${actual}[groups][0][labels]    ${case}[group][labels]
    Dictionaries Should Be Equal    ${actual}[groups][0][rules][0][labels]    ${case}[rule][labels]
    Prometheus Rule Should Exist    ${case}[alert]
    ${source} =    Set Variable    module/${RULE_PUBLISHER_A}/metrics_alert_rules field '${case}[field]'
    IF    $case['warn']
        Source Journal Should Contain
        ...    ${cursor}    group 'ns8:${RULE_PUBLISHER_A}:${case}[field]:identity' from ${source}
        ...    has module_id '${case}[group_owner]'; using module ID '${RULE_PUBLISHER_A}'
        Source Journal Should Contain    ${cursor}    alert '${case}[alert]' from ${source}
        ...    has module_id '${case}[rule_owner]'; using module ID '${RULE_PUBLISHER_A}'
    ELSE
        Source Journal Should Not Contain    ${cursor}    ${source}    using module ID
    END

Queue Rule Metadata
    [Arguments]    ${kind}    ${value}
    ${case} =    New Batch Case    Check Rule Metadata Case
    ${document} =    Single Alert Rule Document    ${case}[alert]
    IF    $kind == 'severity'
        Set To Dictionary    ${document}[labels]    severity    ${value}
        ${warning} =    Set Variable    unsupported severity '${value}'
    ELSE
        ${annotations} =    Create Dictionary    ${value}    Authored summary
        Set To Dictionary    ${document}    annotations    ${annotations}
        ${warning} =    Set Variable    missing bilingual annotations: description_en, description_it, summary_it
    END
    ${payload} =    Serialize Fixture YAML    ${document}
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${case}[field]    ${payload}
    Set To Dictionary    ${document}[labels]    module_id    ${RULE_PUBLISHER_A}
    Set To Dictionary    ${case}    document=${document}    warning=${warning}

Check Rule Metadata Case
    [Arguments]    ${case}    ${cursor}
    ${actual} =    Read Provider Rule Document    ${RULE_PUBLISHER_A}    ${case}[field]
    ${rule} =    Set Variable    ${actual}[groups][0][rules][0]
    Dictionaries Should Be Equal    ${rule}[labels]    ${case}[document][labels]
    Dictionaries Should Be Equal    ${rule}[annotations]    ${case}[document][annotations]
    Prometheus Rule Should Exist    ${case}[alert]
    Source Journal Should Contain    ${cursor}
    ...    alert '${case}[alert]' from module/${RULE_PUBLISHER_A}/metrics_alert_rules field '${case}[field]'
    ...    ${case}[warning]

Queue Rule Encoding
    [Arguments]    ${encoding}    ${warning}
    IF    $encoding == 'utf8'
        ${payload} =    Single Alert Rule Payload    Phase5SchemaAlert    up == 0
        # Everything except this annotation byte is a valid, complete rule.
        ${payload} =    Replace String    ${payload}    Phase 5 alert    \xff
        ${payload} =    Convert To Bytes    ${payload}
    ELSE
        ${payload} =    Set Variable    groups: [\n
    END
    Queue Rejected Rule    ${payload}    ${warning}

Rule File Checksums
    [Arguments]    ${files}
    ${checksums} =    Create Dictionary
    FOR    ${file}    IN    @{files}
        ${checksum} =    Module File Checksum    rules.d/${file}
        Set To Dictionary    ${checksums}    ${file}    ${checksum}
    END
    RETURN    ${checksums}

Aliased Metadata Payload
    [Arguments]    ${kind}    ${owner}    ${alert}
    # Literal YAML preserves aliases through the publication transport.
    ${metadata} =    Catenate    SEPARATOR=\n
    ...    \ \ severity: warning
    ...    \ \ fixture: phase5
    IF    $owner != 'absent'
        ${metadata} =    Catenate    SEPARATOR=\n    ${metadata}    \ \ module_id: ${owner}
    END
    IF    $kind == 'single'
        ${payload} =    Catenate    SEPARATOR=\n
        ...    alert: ${alert}
        ...    expr: up == 0
        ...    labels: &metadata
        ...    ${metadata}
        ...    annotations: *metadata
    ELSE
        ${metadata} =    Replace String    ${metadata}    \n    \n${SPACE * 2}
        ${payload} =    Catenate    SEPARATOR=\n
        ...    groups:
        ...    - name: aliases
        ...    \ \ labels: &metadata
        ...    \ \ ${metadata}
        ...    \ \ rules:
        ...    \ \ - alert: ${alert}
        ...    \ \ \ \ expr: up == 0
        ...    \ \ \ \ labels: *metadata
        ...    \ \ \ \ annotations: *metadata
    END
    RETURN    ${payload}

Loaded Alias Rule Should Preserve Metadata
    [Arguments]    ${alert}    ${labels}    ${annotations}
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
                Dictionaries Should Be Equal    ${rule}[labels]    ${labels}
                Dictionaries Should Be Equal    ${rule}[annotations]    ${annotations}
                Append To List    ${matches}    ${rule}
            END
        END
    END
    Length Should Be    ${matches}    1
