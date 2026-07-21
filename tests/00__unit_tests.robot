*** Settings ***
Documentation    Run the local Python unit-test suite
Library    Process

*** Test Cases ***
Python unit tests pass
    ${result} =    Run Process
    ...    %{venvroot}/bin/python3
    ...    -m
    ...    unittest
    ...    discover
    ...    -s
    ...    tests
    ...    -p
    ...    test_*.py
    ...    -v
    ...    cwd=${CURDIR}/..
    ...    stderr=STDOUT
    Log    ${result.stdout}    console=${True}
    Should Be Equal As Integers    ${result.rc}    0
