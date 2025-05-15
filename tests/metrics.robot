*** Settings ***
Library    SSHLibrary

*** Test Cases ***
Check if metrics is installed correctly
    ${output}  ${rc} =    Execute Command    add-module ${IMAGE_URL} 1
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}  0
    &{output} =    Evaluate    ${output}
    Set Suite Variable    ${module_id}    ${output.module_id}

Check if prometheus is running
    ${rc} =    Execute Command    sleep 10 && curl -f http://127.0.0.1:9091/
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0

Check if alertmanager is running
    ${rc} =    Execute Command    sleep 10 && curl -f http://127.0.0.1:9093/
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0

Check if alert-proxy is running
    ${rc} =    Execute Command    sleep 10 && curl -f http://127.0.0.1:9095/
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0

Check if t can be configured
    ${rc} =    Execute Command    api-cli run module/${module_id}/configure-module --data '{"prometheus_path": "prometheus", "grafana_path": "grafana"}'
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0

Check if grafana is running
    ${rc} =    Execute Command    sleep 10 && curl -f http://127.0.0.1:3000/
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0

Check if Grafana is accessible from Traefik with basic auth
    ${rc} =    Execute Command    sleep 10 && curl -f -Lk https://admin:Nethesis,1234@127.0.0.1/grafana/
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0

Check if Prometheus is accessible from Traefik with basic auth
    ${rc} =    Execute Command    sleep 10 && curl -f -Lk https://admin:Nethesis,1234@127.0.0.1/prometheus/
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0

Check if it is removed correctly
    ${rc} =    Execute Command    remove-module --no-preserve ${module_id}
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0
