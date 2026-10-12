*** Settings ***
Documentation    Module rules travel from Redis through the change event to Prometheus.
Library          SSHLibrary
Library          Collections
Library          String
Suite Setup      Discover Rule Publishers And Prometheus
Test Setup       Initialize Rule Fixtures
Test Teardown    Remove Rule Fixtures


*** Variables ***
${METRICS_ID}    metrics1


*** Test Cases ***
Two Publishers Load Scoped Rules Without Restarting Prometheus
    ${single} =    Set Variable
    ...    {alert: ${ALERT_A}, expr: 'up == 0', labels: {severity: warning}}
    ${grouped} =    Catenate    SEPARATOR=\n
    ...    {groups: [{name: application, rules: [
    ...    {alert: ${ALERT_A}, expr: 'up{module_id="another1"} == 0',
    ...    labels: {severity: warning, module_id: another1}}
    ...    ]}]}
    Publish Rule    ${METRICS_ID}    ${single}
    Publish Rule    ${TRAEFIK_ID}    ${grouped}

    Signal Rule Change

    Wait Until Keyword Succeeds    90s    2s
    ...    Prometheus Reload Should Succeed Without Restart

An Invalid Update Preserves Working Rules And Allows Another Publisher To Update
    Publish Valid Rules
    ${invalid} =    Set Variable
    ...    {alert: ${ALERT_A}, expr: 'up{', labels: {severity: warning}}
    ${replacement} =    Set Variable
    ...    {alert: ${ALERT_UPDATED}, expr: 'up == 0', labels: {severity: warning}}
    Publish Rule    ${METRICS_ID}    ${invalid}
    Publish Rule    ${TRAEFIK_ID}    ${replacement}

    Signal Rule Change

    Wait Until Keyword Succeeds    90s    2s
    ...    Published Rules Should Match    ${ALERT_A}    ${ALERT_UPDATED}
    ${data} =    Get Prometheus Data    rules
    ${alerts} =    Evaluate    [rule['name'] for group in $data['groups'] for rule in group['rules']]
    List Should Not Contain Value    ${alerts}    ${ALERT_B}
    Prometheus Should Be Ready
    ${container_after} =    Prometheus Container ID
    Should Be Equal    ${container_after}    ${CONTAINER_BEFORE}

Removing A Published Rule Preserves Other Monitoring
    Publish Valid Rules

    Delete Rule Field    ${METRICS_ID}
    Signal Rule Change

    Wait Until Keyword Succeeds    90s    2s
    ...    Withdrawn Rule Should Preserve Other Monitoring
    Prometheus Should Be Ready


*** Keywords ***
Discover Rule Publishers And Prometheus
    ${traefik_id} =    Run Command    runagent redis-exec GET cluster/default_instance/traefik
    Should Match Regexp    ${traefik_id}    ^traefik[0-9]+$
    Set Suite Variable    ${TRAEFIK_ID}    ${traefik_id}
    ${path} =    Run Command
    ...    runagent -m ${METRICS_ID} python3 -c 'import os; print(os.environ.get("PROMETHEUS_PATH", ""))'
    ${prefix} =    Evaluate    urllib.parse.quote($path.strip('/'), safe='/')    modules=urllib.parse
    ${base_url} =    Evaluate    ('http://127.0.0.1:9091/' + $prefix).rstrip('/')
    Set Suite Variable    ${PROMETHEUS_URL}    ${base_url}

Initialize Rule Fixtures
    ${publishers} =    Create List
    Set Test Variable    ${FIXTURE_PUBLISHERS}    ${publishers}
    ${suffix} =    Generate Random String    16    [LOWER][NUMBERS]
    Set Test Variable    ${RULE_FIELD}    rule-e2e-${suffix}
    Set Test Variable    ${ALERT_A}    ModuleRuleA_${suffix}
    Set Test Variable    ${ALERT_B}    ModuleRuleB_${suffix}
    Set Test Variable    ${ALERT_UPDATED}    ModuleRuleUpdated_${suffix}
    Wait Until Keyword Succeeds    90s    2s    Prometheus Should Be Ready
    ${container} =    Prometheus Container ID
    Set Test Variable    ${CONTAINER_BEFORE}    ${container}
    ${runtime} =    Get Prometheus Data    status/runtimeinfo
    Set Test Variable    ${RELOAD_BEFORE}    ${runtime}[lastConfigTime]

Publish Rule
    [Arguments]    ${publisher}    ${yaml}
    IF    $publisher not in $FIXTURE_PUBLISHERS
        ${exists} =    Run Command
        ...    redis-cli HEXISTS module/${publisher}/metrics_alert_rules ${RULE_FIELD}
        Should Be Equal    ${exists}    0    Refusing to overwrite an existing Redis field
        # Register before HSET so even a lost SSH response cannot leak a fixture.
        Append To List    ${FIXTURE_PUBLISHERS}    ${publisher}
    END
    ${quoted} =    Evaluate    shlex.quote($yaml)    modules=shlex
    Run Command    redis-cli HSET module/${publisher}/metrics_alert_rules ${RULE_FIELD} ${quoted}

Publish Valid Rules
    ${rule_a} =    Set Variable
    ...    {alert: ${ALERT_A}, expr: 'up == 0', labels: {severity: warning}}
    ${rule_b} =    Set Variable
    ...    {alert: ${ALERT_B}, expr: 'up == 0', labels: {severity: warning}}
    Publish Rule    ${METRICS_ID}    ${rule_a}
    Publish Rule    ${TRAEFIK_ID}    ${rule_b}
    Signal Rule Change
    Wait Until Keyword Succeeds    90s    2s
    ...    Published Rules Should Match    ${ALERT_A}    ${ALERT_B}

Signal Rule Change
    Run Command
    ...    runagent -m ${METRICS_ID} redis-exec PUBLISH module/${METRICS_ID}/event/metrics-alert-rules-changed '{}'

Delete Rule Field
    [Arguments]    ${publisher}
    Run Command    redis-cli HDEL module/${publisher}/metrics_alert_rules ${RULE_FIELD}
    ${exists} =    Run Command
    ...    redis-cli HEXISTS module/${publisher}/metrics_alert_rules ${RULE_FIELD}
    Should Be Equal    ${exists}    0

Remove Rule Fixtures
    IF    not $FIXTURE_PUBLISHERS    RETURN
    # Continue cleanup after a test failure or an individual failed deletion.
    FOR    ${publisher}    IN    @{FIXTURE_PUBLISHERS}
        Run Keyword And Continue On Failure    Delete Rule Field    ${publisher}
    END
    Run Keyword And Continue On Failure    Signal Rule Change
    Wait Until Keyword Succeeds    90s    2s    Fixture Rules Should Be Removed

Get Prometheus Data
    [Arguments]    ${endpoint}
    ${response} =    Run Command    curl -fsS --max-time 5 '${PROMETHEUS_URL}/api/v1/${endpoint}'
    ${document} =    Evaluate    json.loads($response)    modules=json
    Should Be Equal    ${document}[status]    success
    RETURN    ${document}[data]

Get Fixture Groups
    ${data} =    Get Prometheus Data    rules
    ${namespace} =    Create Dictionary    field=${RULE_FIELD}
    ${groups} =    Evaluate
    ...    [group for group in $data['groups'] if field in group['name'].split(':')]
    ...    namespace=${namespace}
    RETURN    ${groups}

Published Rules Should Match
    [Arguments]    ${alert_a}    ${alert_b}    ${group_suffix}=${EMPTY}
    ${groups} =    Get Fixture Groups
    Length Should Be    ${groups}    2
    Group Should Contain Rule    ${groups}    ${METRICS_ID}    ${alert_a}
    Group Should Contain Rule    ${groups}    ${TRAEFIK_ID}    ${alert_b}    ${group_suffix}

Group Should Contain Rule
    [Arguments]    ${groups}    ${publisher}    ${alert}    ${group_suffix}=${EMPTY}
    ${group_name} =    Set Variable    ns8:${publisher}:${RULE_FIELD}${group_suffix}
    ${namespace} =    Create Dictionary    name=${group_name}
    ${matches} =    Evaluate    [group for group in $groups if group['name'] == name]    namespace=${namespace}
    Length Should Be    ${matches}    1
    ${rules} =    Set Variable    ${matches}[0][rules]
    Length Should Be    ${rules}    1
    Should Be Equal    ${rules}[0][name]    ${alert}
    Should Be Equal    ${rules}[0][query]    up{module_id="${publisher}"} == 0
    ${labels} =    Create Dictionary    severity=warning    module_id=${publisher}
    Dictionaries Should Be Equal    ${rules}[0][labels]    ${labels}

Withdrawn Rule Should Preserve Other Monitoring
    ${groups} =    Get Fixture Groups
    Length Should Be    ${groups}    1
    Group Should Contain Rule    ${groups}    ${TRAEFIK_ID}    ${ALERT_B}
    ${data} =    Get Prometheus Data    rules
    ${alerts} =    Evaluate    [rule['name'] for group in $data['groups'] for rule in group['rules']]
    List Should Not Contain Value    ${alerts}    ${ALERT_A}
    List Should Contain Value    ${alerts}    NodeOffline

Fixture Rules Should Be Removed
    ${groups} =    Get Fixture Groups
    Should Be Empty    ${groups}
    ${data} =    Get Prometheus Data    rules
    ${alerts} =    Evaluate    [rule['name'] for group in $data['groups'] for rule in group['rules']]
    FOR    ${alert}    IN    ${ALERT_A}    ${ALERT_B}    ${ALERT_UPDATED}
        List Should Not Contain Value    ${alerts}    ${alert}
    END

Prometheus Reload Should Succeed Without Restart
    Published Rules Should Match    ${ALERT_A}    ${ALERT_A}    :application
    ${runtime} =    Get Prometheus Data    status/runtimeinfo
    Should Be Equal    ${runtime}[reloadConfigSuccess]    ${True}
    Should Not Be Equal    ${runtime}[lastConfigTime]    ${RELOAD_BEFORE}
    Prometheus Should Be Ready
    ${container_after} =    Prometheus Container ID
    Should Be Equal    ${container_after}    ${CONTAINER_BEFORE}

Prometheus Should Be Ready
    Run Command    curl -fsS --max-time 5 '${PROMETHEUS_URL}/-/ready'

Prometheus Container ID
    ${container} =    Run Command    runagent -m ${METRICS_ID} podman inspect --format '{{.Id}}' prometheus
    Should Match Regexp    ${container}    ^[0-9a-f]{64}$
    RETURN    ${container}

Run Command
    [Arguments]    ${command}
    ${output}    ${stderr}    ${rc} =    Execute Command    ${command}
    ...    return_stderr=${True}    return_rc=${True}    timeout=15s
    Should Be Equal As Integers    ${rc}    0    ${output}\n${stderr}
    RETURN    ${output}
