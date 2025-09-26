*** Settings ***
Documentation    Test the cluster default Metrics instance, assuming its module ID is "metrics1"
Library    SSHLibrary

*** Variables ***
${MID}    metrics1

*** Test Cases ***

Check if prometheus is running
    Wait Until Keyword Succeeds    10    1s
    ...    HTTP GET has status 200    http://127.0.0.1:9091/

Check if alertmanager is running
    Wait Until Keyword Succeeds    10    1s
    ...    HTTP GET has status 200    http://127.0.0.1:9093/

Check if alert-proxy is running
    Wait Until Keyword Succeeds    10    1s
    ...    HTTP GET has status 200    http://127.0.0.1:9095/

Check if module can be configured
    ${rc} =    Execute Command    api-cli run module/${MID}/configure-module --data '{"prometheus_path": "prometheus", "grafana_path": "grafana"}'
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0

Check if grafana is running
    Wait Until Keyword Succeeds    10    1s
    ...    HTTP GET has status 200    http://127.0.0.1:3000/

Check if Grafana is accessible from Traefik with basic auth
    Wait Until Keyword Succeeds    30    1s
    ...     HTTP-Basic authentication accepted    https://127.0.0.1/grafana/    admin:Nethesis,1234

Check if Prometheus is accessible from Traefik with basic auth
    Wait Until Keyword Succeeds    30    1s
    ...     HTTP-Basic authentication accepted    https://127.0.0.1/prometheus/    admin:Nethesis,1234

*** Keywords ***
HTTP GET has status 200
    [Arguments]    ${url}
    ${rc} =     Execute Command    curl -f '${url}'    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}  0

HTTP-Basic authentication accepted
    [Arguments]    ${url}    ${credentials}
    ${rc} =    Execute Command    curl -f -Lk -u '${credentials}' '${url}'
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0    curl exit code ${rc}
