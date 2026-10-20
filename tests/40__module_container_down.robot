*** Settings ***
Documentation     Containers listed in metrics_containers feed the ModuleContainerDown alert
Library           SSHLibrary
Suite Setup       Skip If The Core Has No Container Metrics
Suite Teardown    Remove Container List

*** Variables ***
${MID}            metrics1
# Suite 10 sets prometheus_path=prometheus
${PROM_API}       http://127.0.0.1:9091/prometheus/api/v1
${MISSING}        e2e-missing

*** Test Cases ***
Check listed containers become expected series
    Execute Command    redis-cli HSET module/${MID}/metrics_containers prometheus warning ${MISSING} ''
    Signal Rule Change
    Wait Until Keyword Succeeds    90s    5s
    ...    Query Should Count    ns8:module_container_expected{module_id="${MID}"}    2
    Query Should Count    ns8:module_container_expected{container="${MISSING}",severity="critical"}    1

Check only the missing container raises the alert
    # The core collector runs every 60s, then Prometheus scrapes it
    Wait Until Keyword Succeeds    180s    10s
    ...    Query Should Count    ALERTS{alertname="ModuleContainerDown",module_id="${MID}",container="${MISSING}"}    1
    Query Should Count    ALERTS{alertname="ModuleContainerDown",module_id="${MID}",container="prometheus"}    0

Check removing the list drops the expected series
    Remove Container List
    # Prometheus marks the series of a removed rule group stale only
    # after two evaluation intervals (1m each by default)
    Wait Until Keyword Succeeds    180s    5s
    ...    Query Should Count    ns8:module_container_expected{module_id="${MID}"}    0

*** Keywords ***
Skip If The Core Has No Container Metrics
    # ns8_container_* series come with core 3.22.0
    ${rc} =    Execute Command    test -x /var/lib/nethserver/node/bin/refresh-container-metrics
    ...    return_rc=True    return_stdout=False
    Skip If    ${rc} != 0    The core does not export container metrics

Signal Rule Change
    ${rc} =    Execute Command
    ...    runagent -m ${MID} redis-exec PUBLISH module/${MID}/event/metrics-alert-rules-changed '{}'
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0

Remove Container List
    Execute Command    redis-cli DEL module/${MID}/metrics_containers
    Signal Rule Change

Query Should Count
    [Arguments]    ${query}    ${expected}
    ${count} =    Execute Command    curl -sf --get ${PROM_API}/query --data-urlencode 'query=${query}' | python3 -c 'import json, sys; print(len(json.load(sys.stdin)["data"]["result"]))'
    Should Be Equal As Integers    ${count}    ${expected}
