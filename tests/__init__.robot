*** Settings ***
Library           SSHLibrary
Library           DateTime

*** Variables ***
${SSH_KEYFILE}    %{HOME}/.ssh/id_ecdsa

*** Settings ***
Suite Setup       Run Keywords
...                    Connect to the Node
...                    Save the journal begin timestamp

Suite Teardown    Run Keywords
...                    Collect the suite journal


*** Keywords ***
Connect to the node
    Open Connection   ${NODE_ADDR}
    Login With Public Key    root    ${SSH_KEYFILE}
    ${output} =    Execute Command    systemctl is-system-running  --wait
    Should Be True    '${output}' == 'running' or '${output}' == 'degraded'

Save the journal begin timestamp
    ${tsnow} =    Get Current Date    result_format=epoch
    Set Global Variable    ${JOURNAL_SINCE}    ${tsnow}

Collect the suite journal
    Execute Command    journalctl -S @${JOURNAL_SINCE} >journal-dump.log
    Get File    journal-dump.log    ${OUTPUT DIR}/journal-${SUITE NAME}.log
