*** Settings ***
Documentation    Test the cluster default Metrics instance, assuming its module ID is "metrics1"
Library    SSHLibrary

*** Variables ***
${MID}    metrics1

*** Test Cases ***

Check if prometheus is running
    Wait Until Keyword Succeeds    10    1s
    ...    HTTP GET has status 401    http://127.0.0.1:9091/

Check if alertmanager is running
    Wait Until Keyword Succeeds    10    1s
    ...    HTTP GET has status 401    http://127.0.0.1:9093/

Unprivileged user is refused
    ${out} =    Execute Command    runuser -u loki1 -- curl -s -o /dev/null -w '\%{http_code}' http://127.0.0.1:9093/api/v2/silences
    Should Be Equal    ${out}    401

Check if module can be configured
    ${rc} =    Execute Command    api-cli run module/${MID}/configure-module --data '{"grafana_path": "grafana"}'
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0

Check if grafana is running
    Wait Until Keyword Succeeds    10    1s
    ...    HTTP GET has status 200    http://127.0.0.1:3000/grafana/login

Check if Grafana is accessible from Traefik with basic auth
    Wait Until Keyword Succeeds    30    1s
    ...     HTTP-Basic authentication accepted    https://127.0.0.1/grafana/    admin:Nethesis,1234

Grafana refuses a spoofed user header
    ${out} =    Execute Command    curl -s -o /dev/null -w '\%{http_code}' -H 'X-AUTH-USER: admin' http://127.0.0.1:3000/grafana/api/user
    Should Be Equal    ${out}    401

Grafana login through Traefik works
    ${out} =    Execute Command    curl -sk -w '\\n\%{http_code}' -u 'admin:Nethesis,1234' https://127.0.0.1/grafana/api/user
    Should Contain    ${out}    "login":"admin"
    Should Contain    ${out}    "isGrafanaAdmin":true
    Should End With    ${out.strip()}    200

Forged token without login is refused
    ${out} =    Execute Command    curl -sk -o /dev/null -w '\%{http_code}' -H 'X-Auth-Token: x' https://127.0.0.1/grafana/api/user
    Should Be Equal    ${out}    401

Forged token with login does not change the user
    ${out} =    Execute Command    curl -sk -u 'admin:Nethesis,1234' -H 'X-Auth-Token: x' https://127.0.0.1/grafana/api/user
    Should Contain    ${out}    "login":"admin"

Grafana data sources work
    Grafana data source is healthy    .[] | select(.uid=="prometheus") | .uid
    # The Alertmanager data source has no health check: query it through
    # the Grafana proxy, which sends the password
    ${uid} =    Execute Command    curl -sk -u 'admin:Nethesis,1234' https://127.0.0.1/grafana/api/datasources | jq -r '[.[] | select(.name=="Alertmanager")] | first | .uid'
    ${out} =    Execute Command    curl -sk -o /dev/null -w '\%{http_code}' -u 'admin:Nethesis,1234' https://127.0.0.1/grafana/api/datasources/proxy/uid/${uid}/api/v2/status
    Should Be Equal    ${out}    200

Prometheus has no Traefik route
    ${out} =    Execute Command    api-cli run module/traefik1/get-route --data '{"instance":"${MID}_prometheus"}'
    ${route} =    Evaluate    json.loads($out)    modules=json
    Should Be Empty    ${route}

Password works
    ${pw} =    Get Local Password
    FOR    ${url}    IN    http://127.0.0.1:9091/-/healthy    http://127.0.0.1:9093/-/healthy
        ${out} =    Execute Command    curl -s -o /dev/null -w '\%{http_code}' -u 'metrics:${pw}' ${url}
        Should Be Equal    ${out}    200
    END

Core lists the alerts with the password
    ${pw} =    Get Local Password
    ${name} =    Set Variable    RobotListAlerts${{random.randint(1000, 9999)}}
    ${ends} =    Evaluate    (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)).isoformat()    modules=datetime
    ${rc} =    Execute Command    curl -sf -u 'metrics:${pw}' -H 'Content-Type: application/json' http://127.0.0.1:9093/api/v2/alerts --data '[{"labels": {"alertname": "${name}", "module_id": "${MID}", "node": "1"}, "endsAt": "${ends}"}]'
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0
    Wait Until Keyword Succeeds    30s    2s
    ...    Cluster Alert List Should Contain    ${name}

Password is not in module keys or environment
    ${pw} =    Get Local Password
    Should Not Be Empty    ${pw}
    ${hits} =    Execute Command
    ...    redis-cli --scan --pattern 'module/${MID}/*' | cat - <(redis-cli --scan --pattern 'task/module/${MID}/*') | while read -r k; do redis-cli hgetall "$k" </dev/null 2>/dev/null; redis-cli get "$k" </dev/null 2>/dev/null; done | grep -cF '${pw}' || true
    Should Be Equal    ${hits.strip()}    0
    ${env} =    Execute Command    grep -cF '${pw}' /home/${MID}/.config/state/environment || true
    Should Be Equal    ${env.strip()}    0

*** Keywords ***
Cluster Alert List Should Contain
    [Arguments]    ${name}
    ${out} =    Execute Command    api-cli run cluster/list-alerts --data '{}'
    Should Contain    ${out}    ${name}

Get Local Password
    ${pw} =    Execute Command    runagent -m ${MID} python3 -c "import json;print(json.load(open('basic_auth.json'))['password'])"
    RETURN    ${pw}

Grafana data source is healthy
    [Arguments]    ${filter}
    ${uid} =    Execute Command    curl -sk -u 'admin:Nethesis,1234' https://127.0.0.1/grafana/api/datasources | jq -r '${filter}'
    ${out} =    Execute Command    curl -sk -o /dev/null -w '\%{http_code}' -u 'admin:Nethesis,1234' https://127.0.0.1/grafana/api/datasources/uid/${uid}/health
    Should Be Equal    ${out}    200

HTTP GET has status 200
    [Arguments]    ${url}
    ${rc} =     Execute Command    curl -f '${url}'    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}  0

HTTP GET has status 401
    [Arguments]    ${url}
    ${out} =    Execute Command    curl -s -o /dev/null -w '\%{http_code}' '${url}'
    Should Be Equal    ${out}    401

HTTP-Basic authentication accepted
    [Arguments]    ${url}    ${credentials}
    ${rc} =    Execute Command    curl -f -Lk -u '${credentials}' '${url}'
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0    curl exit code ${rc}
