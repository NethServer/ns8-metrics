*** Settings ***
Documentation    Run local unit tests for Prometheus alert-rule provisioning
Library    Process

*** Test Cases ***
Check alert-rule provisioning unit tests
    ${python} =    Evaluate    sys.executable    modules=sys
    ${result} =    Run Process
    ...    ${python}    -m    unittest    discover
    ...    -s    tests    -p    test_*.py    -v
    ...    cwd=${EXECDIR}
    ...    env:PYTHONDONTWRITEBYTECODE=1
    ...    stderr=STDOUT
    Log    ${result.stdout}
    Should Be Equal As Integers    ${result.rc}    0
