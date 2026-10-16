*** Settings ***
Documentation     Per-module series from node targets get the module_id label
Library           SSHLibrary
Suite Setup       Skip If The Core Has No Container Metrics

*** Variables ***
${MID}            metrics1
# Suite 10 sets prometheus_path=prometheus
${PROM_API}       http://127.0.0.1:9091/prometheus/api/v1

*** Test Cases ***
Check Prometheus loads the module_id relabel rule
    ${config} =    Execute Command    curl -sf ${PROM_API}/status/config
    Should Contain    ${config}    node;;([a-z][a-z0-9-]*[0-9]+)

Check module containers carry module_id
    # The core collector runs every 60s, then Prometheus scrapes it
    Wait Until Keyword Succeeds    180s    10s
    ...    Query Should Return Series
    ...    ns8_container_info{module_id="${MID}",container="prometheus"}

Check core containers do not carry module_id
    Wait Until Keyword Succeeds    180s    10s
    ...    Query Should Return Series    ns8_container_info{module="core"}
    ${count} =    Count Query Series    ns8_container_info{module="core",module_id!=""}
    Should Be Equal As Integers    ${count}    0

*** Keywords ***
Skip If The Core Has No Container Metrics
    # ns8_container_* series come with core 3.22.0
    ${rc} =    Execute Command    test -x /var/lib/nethserver/node/bin/refresh-container-metrics
    ...    return_rc=True    return_stdout=False
    Skip If    ${rc} != 0    The core does not export container metrics

Count Query Series
    [Arguments]    ${query}
    ${count} =    Execute Command    curl -sf --get ${PROM_API}/query --data-urlencode 'query=${query}' | python3 -c 'import json, sys; print(len(json.load(sys.stdin)["data"]["result"]))'
    RETURN    ${count}

Query Should Return Series
    [Arguments]    ${query}
    ${count} =    Count Query Series    ${query}
    Should Be True    ${count} > 0    No series for ${query}
