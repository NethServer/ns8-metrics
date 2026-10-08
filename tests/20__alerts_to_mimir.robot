*** Settings ***
Documentation     Prometheus sends alerts to the local Alertmanager and to my Mimir
Library           SSHLibrary
Suite Setup       Skip If The Cluster Has A Subscription
Suite Teardown    Remove The Test Data

*** Variables ***
${MID}            metrics1
# Suite 10 sets prometheus_path=prometheus
${PROM_API}       http://127.0.0.1:9091/prometheus/api/v1
${LOCAL_AM}       http://localhost:9093/api/v2/alerts
${MIMIR_AM}       https://mimir.invalid:443/collect/api/services/mimir/alertmanager/api/v2/alerts
${FAKE_SYSTEM}    fake-system
${FAKE_TOKEN}     fake-token-0123

*** Test Cases ***
Check that alert-proxy is gone
    ${rc} =    Execute Command    runagent -m ${MID} systemctl --user is-active alert-proxy.service
    ...    return_rc=True    return_stdout=False
    Should Not Be Equal As Integers    ${rc}    0
    ${containers} =    Execute Command    runagent -m ${MID} podman ps -a --filter name=alert-proxy -q
    Should Be Empty    ${containers}
    ${units} =    Execute Command    runagent -m ${MID} find ../systemd/user -name alert-proxy.service
    Should Be Empty    ${units}
    ${images} =    Execute Command    runagent -m ${MID} podman images --format '{{.Repository}}' | grep -c alert-proxy
    Should Be Equal    ${images}    0
    ${env} =    Execute Command    runagent -m ${MID} grep -c ALERT_PROXY environment
    Should Be Equal    ${env}    0

Check Prometheus uses only the local Alertmanager without subscription
    Wait Until Keyword Succeeds    60s    2s
    ...    Active Alertmanagers Should Be    ${LOCAL_AM}

Check Prometheus starts with a subscription not migrated to my
    # Before migrate-to-my the subscription has no collect_url
    Execute Command    redis-cli HSET cluster/subscription provider nsent system_id ${FAKE_SYSTEM} auth_token ${FAKE_TOKEN}
    Run The Subscription Handler
    Wait Until Keyword Succeeds    60s    2s
    ...    Active Alertmanagers Should Be    ${LOCAL_AM}

Check Prometheus also uses Mimir with an enterprise subscription
    Execute Command    redis-cli HSET cluster/subscription provider nsent system_id ${FAKE_SYSTEM} auth_token ${FAKE_TOKEN} collect_url https://mimir.invalid/collect/api/systems
    Run The Subscription Handler
    Wait Until Keyword Succeeds    60s    2s
    ...    Active Alertmanagers Should Be    ${LOCAL_AM} ${MIMIR_AM}

Check the Mimir password is not exposed by Prometheus
    ${config} =    Execute Command    curl -sf ${PROM_API}/status/config
    Should Contain    ${config}    mimir.invalid
    Should Not Contain    ${config}    ${FAKE_TOKEN}

Check alerts reach the local Alertmanager while Mimir is unreachable
    Execute Command    redis-cli HSET module/${MID}/custom_alerts mimir_test '{alert: MimirTestAlwaysFiring, expr: vector(1), labels: {severity: warning}}'
    ${rc} =    Execute Command    runagent -m ${MID} systemctl --user restart prometheus.service
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0
    Wait Until Keyword Succeeds    180s    10s
    ...    Local Alertmanager Has Alert    MimirTestAlwaysFiring

Check the Mimir target goes away with the subscription
    Remove The Test Data
    Wait Until Keyword Succeeds    60s    2s
    ...    Active Alertmanagers Should Be    ${LOCAL_AM}

*** Keywords ***
Skip If The Cluster Has A Subscription
    ${exists} =    Execute Command    redis-cli EXISTS cluster/subscription
    Skip If    '${exists}' == '1'    The node has a real subscription: not overwriting it

Run The Subscription Handler
    ${rc} =    Execute Command    runagent -m ${MID} ../events/subscription-changed/10handler
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0

Active Alertmanagers Should Be
    [Arguments]    ${expected}
    ${urls} =    Execute Command    curl -sf ${PROM_API}/alertmanagers | python3 -c 'import json, sys; print(" ".join(sorted(a["url"] for a in json.load(sys.stdin)["data"]["activeAlertmanagers"])))'
    Should Be Equal    ${urls}    ${expected}

Local Alertmanager Has Alert
    [Arguments]    ${name}
    ${alerts} =    Execute Command    curl -sf http://127.0.0.1:9093/api/v2/alerts
    Should Contain    ${alerts}    "alertname":"${name}"

Remove The Test Data
    Execute Command    redis-cli HDEL module/${MID}/custom_alerts mimir_test
    # Never delete a real subscription
    ${system_id} =    Execute Command    redis-cli HGET cluster/subscription system_id
    IF    '${system_id}' == '${FAKE_SYSTEM}'
        Execute Command    redis-cli DEL cluster/subscription
    END
    Run The Subscription Handler
