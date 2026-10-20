*** Settings ***
Documentation     Built-in OOM kill alert rules are loaded by Prometheus
Library           SSHLibrary

*** Variables ***
# Suite 10 sets prometheus_path=prometheus
${PROM_API}       http://127.0.0.1:9091/prometheus/api/v1

*** Test Cases ***
Check Prometheus loads the ContainerOomKilled rule
    Wait Until Keyword Succeeds    60s    5s
    ...    Rule Should Be Healthy    ContainerOomKilled

Check Prometheus loads the NodeOomKilled rule
    Wait Until Keyword Succeeds    60s    5s
    ...    Rule Should Be Healthy    NodeOomKilled

*** Keywords ***
Rule Should Be Healthy
    [Arguments]    ${name}
    ${health} =    Execute Command    curl -sf ${PROM_API}/rules | python3 -c 'import json, sys; print(" ".join(r["health"] for g in json.load(sys.stdin)["data"]["groups"] for r in g["rules"] if r["name"] == "${name}"))'
    Should Be Equal    ${health}    ok    Rule ${name} missing or unhealthy
