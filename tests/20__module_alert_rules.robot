*** Settings ***
Documentation    Test provider targets and alert rules through provisioning and Prometheus
Resource         resources/module_alert_rules.resource
Suite Setup      Initialize Module Rule Fixtures
Suite Teardown   Finish Module Rule Fixtures
Test Setup       Reset Module Rule Fixtures
Test Teardown    Reset Module Rule Fixtures


*** Test Cases ***
Provider Targets Receive Their Redis Owner Identity
    ${target_a} =    Target Payload Without Labels    19091
    ${target_b} =    Target Payload With Module Label
    ...    19092
    ...    another1
    ${since} =    Current Epoch

    Write Publisher Target    ${RULE_PUBLISHER_A}    ${target_a}
    Write Publisher Target    ${RULE_PUBLISHER_B}    ${target_b}
    ${event_cursor} =    Signal Metrics Target Change    ${RULE_PUBLISHER_A}

    Wait Until Keyword Succeeds    60s    2s
    ...    Provider Target File Should Contain
    ...    ${RULE_PUBLISHER_A}
    ...    module_id: ${RULE_PUBLISHER_A}
    ...    target_type: ${TARGET_FIELD}
    Wait Until Keyword Succeeds    60s    2s
    ...    Provider Target File Should Contain
    ...    ${RULE_PUBLISHER_B}
    ...    module_id: ${RULE_PUBLISHER_B}
    ...    fixture: phase5
    Wait Until Keyword Succeeds    60s    2s
    ...    Module Event Should Complete
    ...    ${event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-target-changed
    Journal Should Contain    ${since}    overwritten with '${RULE_PUBLISHER_B}'

Two Publishers Load Full And Single Rules Independently
    ${target_a} =    Target Payload Without Labels    19091
    ${target_b} =    Target Payload Without Labels    19092
    Write Publisher Target    ${RULE_PUBLISHER_A}    ${target_a}
    Write Publisher Target    ${RULE_PUBLISHER_B}    ${target_b}
    ${single_rule} =    Single Alert Rule Payload
    ...    SharedApplicationDown
    ...    sum(up{target_type="${TARGET_FIELD}"}) == 0
    ${full_rule} =    Full Alert Rule Payload
    ...    application
    ...    SharedApplicationDown
    ...    up{target_type="${TARGET_FIELD}"} == 0
    ...    another1
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${single_rule}
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_B}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${full_rule}
    ${container_before} =    Prometheus Container ID
    ${timestamp_before} =    Prometheus Reload Timestamp

    ${event_cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}

    Wait Until Keyword Succeeds    90s    2s
    ...    Prometheus Rule Count Should Be
    ...    SharedApplicationDown
    ...    2
    Provider Rule File Should Contain
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ns8:${RULE_PUBLISHER_A}:${PRIMARY_RULE_FIELD}
    ...    module_id\="${RULE_PUBLISHER_A}"
    ...    module_id: ${RULE_PUBLISHER_A}
    ...    fixture: phase5
    Provider Rule File Should Contain
    ...    ${RULE_PUBLISHER_B}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ns8:${RULE_PUBLISHER_B}:${PRIMARY_RULE_FIELD}:application
    ...    module_id\="${RULE_PUBLISHER_B}"
    ...    module_id: ${RULE_PUBLISHER_B}
    Wait Until Keyword Succeeds    30s    1s
    ...    Prometheus Reload Timestamp Should Advance
    ...    ${timestamp_before}
    Prometheus Last Reload Should Be Successful
    ${container_after} =    Prometheus Container ID
    Should Be Equal    ${container_after}    ${container_before}
    Alertmanager Configuration Should Use Module Identity
    Wait Until Keyword Succeeds    60s    2s
    ...    Module Event Should Complete
    ...    ${event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-alert-rules-changed

Invalid And Warning Sources Are Isolated
    ${valid_a} =    Single Alert Rule Payload
    ...    RetainedProviderAlert
    ...    up == 0
    ${valid_b} =    Single Alert Rule Payload
    ...    IndependentlyUpdatedAlert
    ...    up == 0
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${valid_a}
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_B}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${valid_b}
    ${initial_event_cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}
    Wait Until Keyword Succeeds    90s    2s
    ...    Prometheus Rule Should Exist
    ...    IndependentlyUpdatedAlert
    Wait Until Keyword Succeeds    60s    2s
    ...    Module Event Should Complete
    ...    ${initial_event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-alert-rules-changed
    ${checksum_a_before} =    Provider Rule Checksum
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ${checksum_b_before} =    Provider Rule Checksum
    ...    ${RULE_PUBLISHER_B}
    ...    ${PRIMARY_RULE_FIELD}

    ${invalid_a} =    Single Alert Rule Payload
    ...    RetainedProviderAlert
    ...    up{
    ${updated_b} =    Single Alert Rule Payload
    ...    IndependentlyUpdatedAlert
    ...    ns8_phase5_missing_metric == 0
    ${duplicate_b} =    Minimal Alert Rule Payload
    ...    IndependentlyUpdatedAlert
    ...    up == 0
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${invalid_a}
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_B}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${updated_b}
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_B}
    ...    ${SECONDARY_RULE_FIELD}
    ...    ${duplicate_b}
    ${since} =    Current Epoch
    ${event_cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}

    Wait Until Keyword Succeeds    90s    2s
    ...    Provider Rule File Should Exist
    ...    ${RULE_PUBLISHER_B}
    ...    ${SECONDARY_RULE_FIELD}
    Wait Until Keyword Succeeds    90s    2s
    ...    Module Event Should Complete
    ...    ${event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-alert-rules-changed
    ${checksum_a_after} =    Provider Rule Checksum
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ${checksum_b_after} =    Provider Rule Checksum
    ...    ${RULE_PUBLISHER_B}
    ...    ${PRIMARY_RULE_FIELD}
    Should Be Equal    ${checksum_a_after}    ${checksum_a_before}
    Should Not Be Equal    ${checksum_b_after}    ${checksum_b_before}
    Journal Should Contain    ${since}    Skipped module alert rule
    Journal Should Contain    ${since}    no severity label
    Journal Should Contain    ${since}    missing bilingual annotations

Removed Provider Rules Preserve Built-In Rules
    ${rule} =    Single Alert Rule Payload
    ...    RemovedProviderAlert
    ...    up == 0
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${rule}
    ${install_event_cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}
    Wait Until Keyword Succeeds    90s    2s
    ...    Prometheus Rule Should Exist
    ...    RemovedProviderAlert
    Wait Until Keyword Succeeds    60s    2s
    ...    Module Event Should Complete
    ...    ${install_event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-alert-rules-changed

    Delete Publisher Rule Field
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ${remove_event_cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}

    Wait Until Keyword Succeeds    90s    2s
    ...    Provider Rule File Should Be Absent
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    Wait Until Keyword Succeeds    60s    2s
    ...    Module Event Should Complete
    ...    ${remove_event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-alert-rules-changed
    Wait Until Keyword Succeeds    30s    1s
    ...    Prometheus Rule Should Be Absent
    ...    RemovedProviderAlert
    Prometheus Rule Should Exist    NodeOffline
    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} /usr/bin/test -f rules.d/nodes.yml
    ...    return_rc=${True}
    ...    return_stdout=${False}
    Should Be Equal As Integers    ${rc}    0

Rule Events Leave An Inactive Prometheus Stopped
    ${output}    ${rc} =    Execute Command
    ...    runagent -m ${METRICS_ID} systemctl --user stop prometheus.service
    ...    return_rc=${True}
    Should Be Equal As Integers    ${rc}    0    ${output}
    ${rule} =    Single Alert Rule Payload
    ...    InactiveProviderAlert
    ...    up == 0
    Write Publisher Rule
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    ...    ${rule}

    ${event_cursor} =    Signal Module Rule Change    ${RULE_PUBLISHER_A}

    Wait Until Keyword Succeeds    60s    2s
    ...    Provider Rule File Should Exist
    ...    ${RULE_PUBLISHER_A}
    ...    ${PRIMARY_RULE_FIELD}
    Wait Until Keyword Succeeds    60s    2s
    ...    Module Event Should Complete
    ...    ${event_cursor}
    ...    ${RULE_PUBLISHER_A}
    ...    metrics-alert-rules-changed
    Prometheus Service Should Be Inactive

Malformed Target Documents Are Isolated
    [Template]    Target Structure Should Be Rejected
    - targets: [\n                                  while parsing a flow node
    targets: ["127.0.0.1:19091"]                    target document must be a list of mappings
    - "127.0.0.1:19091"                             target item 0 must be a mapping
    - targets: ["127.0.0.1:19091"]\n\ \ labels: invalid    target item 0 labels must be a mapping

Invalid Target Field Names Are Isolated
    [Template]    Target Field Should Be Rejected
    ${EMPTY}
    .
    ..
    bad/name
    ../escaped
    bad\\name
    has space
    has:colon
    métrics
    has\x00nul
    has\nnewline
    has'quote"$(false)`false`

Invalid Target Publishers Are Isolated
    [Template]    Target Publisher Should Be Rejected
    ${EMPTY}         invalid module ID
    .                invalid module ID
    ..               invalid module ID
    a/b              invalid Redis key shape
    ../escaped       invalid Redis key shape
    has space        invalid module ID
    has:colon        invalid module ID
    bad\\name        invalid module ID
    métrics1         invalid module ID
    has'quote        invalid module ID

Target Identifier Spelling And Matching Labels Are Preserved
    ${publisher} =    Set Variable    Phase5_SQL_1.dev-x
    ${field} =    Set Variable    .sql_Metrics-v2
    ${labels} =    Create Dictionary
    ...    module_id=${publisher}    target_type=${field}    node=1    environment=production
    ${targets} =    Create List    127.0.0.1:19091
    ${entry} =    Create Dictionary    targets=${targets}    labels=${labels}
    ${document} =    Create List    ${entry}
    ${payload} =    Serialize Fixture YAML    ${document}
    Write Publisher Target    ${publisher}    ${payload}    ${field}
    ${cursor} =    Provision Target Fixtures
    Target Document Should Have Labels    ${publisher}    ${field}    ${labels}
    Source Journal Should Not Contain    ${cursor}    module/${publisher}/metrics_targets    Target label conflict

Built-In Node Targets Keep Their Original Labels
    ${before} =    Module Directory Files    prometheus.d
    ${nodes} =    Get Matches    ${before}    node_*.yml
    Should Not Be Empty    ${nodes}
    ${documents} =    Create Dictionary
    FOR    ${file}    IN    @{nodes}
        ${document} =    Read Module YAML    prometheus.d/${file}
        Set To Dictionary    ${documents}    ${file}    ${document}
    END
    ${cursor} =    Provision Target Fixtures
    FOR    ${file}    IN    @{nodes}
        ${document} =    Read Module YAML    prometheus.d/${file}
        Should Be Equal    ${document}    ${documents}[${file}]
        FOR    ${entry}    IN    @{document}
            ${keys} =    Get Dictionary Keys    ${entry}[labels]
            ${expected} =    Create List    node    target_type
            Lists Should Be Equal    ${keys}    ${expected}
            Should Be Equal    ${entry}[labels][target_type]    node
            Dictionary Should Not Contain Key    ${entry}[labels]    module_id
        END
    END

Target Filename Byte Limit Rejects Only The Longer Field
    ${field} =    Maximum Filename Field    ${RULE_PUBLISHER_A}
    ${baseline} =    Module Directory Files    prometheus.d
    ${payload} =    Target Payload Without Labels    19091
    Write Publisher Target    ${RULE_PUBLISHER_A}    ${payload}    ${field}
    Write Publisher Target    ${RULE_PUBLISHER_A}    ${payload}    ${field}x
    Write Publisher Target    ${RULE_PUBLISHER_A}    ${payload}
    ${cursor} =    Provision Target Fixtures
    ${labels} =    Create Dictionary    module_id=${RULE_PUBLISHER_A}    target_type=${field}
    Target Document Should Have Labels    ${RULE_PUBLISHER_A}    ${field}    ${labels}
    Source Journal Should Contain    ${cursor}    Skipped target '${field}x'
    ...    generated target filename exceeds 255 bytes
    Directory Should Contain Only Added Files    prometheus.d    ${baseline}
    ...    provision_${RULE_PUBLISHER_A}_${field}.yml
    ...    provision_${RULE_PUBLISHER_A}_${TARGET_FIELD}.yml

Invalid Rule Encodings And YAML Are Isolated
    [Template]    Rule Encoding Should Be Rejected
    utf8    payload is not valid UTF-8
    yaml    invalid YAML

Unsupported Rule Schemas Are Isolated
    [Template]    Rule Schema Should Be Rejected
    document    __self__       scalar              payload must decode to a mapping
    document    __self__       ${EMPTY_MAPPING}    payload must be a groups document or a single alert rule
    document    groups         invalid             'groups' must be a list
    group       __self__       invalid             group 0 must be a mapping
    group       name           __remove__          group 0 must have a non-empty string name
    group       name           ${42}               group 0 must have a non-empty string name
    group       rules          __remove__          must have a rules list
    group       rules          invalid             must have a rules list
    group       labels         invalid             labels must be a mapping
    rule        __self__       invalid             rule 0 must be a mapping
    rule        alert          __remove__          must have a non-empty string 'alert' field
    rule        alert          ${SPACE}            must have a non-empty string 'alert' field
    rule        alert          ${42}               must have a non-empty string 'alert' field
    rule        expr           __remove__          must have a non-empty string 'expr' field
    rule        expr           ${EMPTY}            must have a non-empty string 'expr' field
    rule        expr           ${42}               must have a non-empty string 'expr' field
    rule        record         saved_up            is a recording rule; only alerts are supported
    rule        labels         invalid             labels must be a mapping
    rule        annotations    invalid             annotations must be a mapping

Single Recording Rules Are Rejected
    ${document} =    Single Alert Rule Document
    Remove From Dictionary    ${document}    alert
    Set To Dictionary    ${document}    record    saved_up
    ${payload} =    Serialize Fixture YAML    ${document}
    Rule Payload Should Be Rejected    ${payload}    recording rules are not supported

Reserved Duplicate And Blank Group Names Are Rejected
    [Template]    Local Group Names Should Be Rejected
    uses the reserved 'ns8:' prefix          ns8:reserved
    duplicate local group name 'repeated'    repeated    ${SPACE}repeated${SPACE}
    must have a non-empty string name        ${SPACE * 3}

Group Names Are Stable After Reordering
    ${document} =    Full Alert Rule Document    availability    capacity
    ${payload} =    Serialize Fixture YAML    ${document}
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${PRIMARY_RULE_FIELD}    ${payload}
    Provision Rule Fixtures
    ${before} =    Provider Group Names
    ${expected} =    Create List
    ...    ns8:${RULE_PUBLISHER_A}:${PRIMARY_RULE_FIELD}:availability
    ...    ns8:${RULE_PUBLISHER_A}:${PRIMARY_RULE_FIELD}:capacity
    Lists Should Be Equal    ${before}    ${expected}
    Reverse List    ${document}[groups]
    ${payload} =    Serialize Fixture YAML    ${document}
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${PRIMARY_RULE_FIELD}    ${payload}
    Provision Rule Fixtures
    ${after} =    Provider Group Names
    Reverse List    ${expected}
    Lists Should Be Equal    ${after}    ${expected}
    Prometheus Rule Count Should Be    Phase5SchemaAlert    2

Group Names Distinguish Publishers Rule Sets And Payload Formats
    ${seen} =    Create List
    FOR    ${format}    IN    single    full
        FOR    ${publisher}    IN    ${RULE_PUBLISHER_A}    ${RULE_PUBLISHER_B}
            FOR    ${field}    IN    ${PRIMARY_RULE_FIELD}    ${SECONDARY_RULE_FIELD}
                IF    $format == 'single'
                    ${document} =    Single Alert Rule Document
                    ${expected} =    Set Variable    ns8:${publisher}:${field}
                ELSE
                    ${document} =    Full Alert Rule Document    local:group
                    ${expected} =    Set Variable    ns8:${publisher}:${field}:local:group
                END
                ${payload} =    Serialize Fixture YAML    ${document}
                Write Publisher Rule    ${publisher}    ${field}    ${payload}
            END
        END
        Provision Rule Fixtures
        FOR    ${publisher}    IN    ${RULE_PUBLISHER_A}    ${RULE_PUBLISHER_B}
            FOR    ${field}    IN    ${PRIMARY_RULE_FIELD}    ${SECONDARY_RULE_FIELD}
                ${names} =    Provider Group Names    ${publisher}    ${field}
                Length Should Be    ${names}    1
                ${expected} =    Set Variable    ns8:${publisher}:${field}
                IF    $format == 'full'
                    ${expected} =    Set Variable    ${expected}:local:group
                END
                Should Be Equal    ${names}[0]    ${expected}
                List Should Not Contain Value    ${seen}    ${names}[0]
                Append To List    ${seen}    ${names}[0]
            END
        END
        Prometheus Rule Count Should Be    Phase5SchemaAlert    4
    END
    Length Should Be    ${seen}    8

Group And Rule Identity Conflicts Are Reported Precisely
    [Template]    Rule Identity Should Be Enforced
    another1               another2               ${True}
    ${RULE_PUBLISHER_A}     ${RULE_PUBLISHER_A}     ${False}

Authored Metadata Is Retained With Specific Warnings
    [Template]    Rule Metadata Should Be Retained
    severity       info
    annotations    summary_en

PromQL Selector Shapes Are Scoped By The Real Parser
    [Template]    PromQL Should Be Scoped
    -up == -1                           -up{module_id="${RULE_PUBLISHER_A}"} == -1
    ${SPACE * 2}-up == -1                -up{module_id="${RULE_PUBLISHER_A}"} == -1
    rate(requests_total[5m]) > 1         rate(requests_total{module_id="${RULE_PUBLISHER_A}"}[5m]) > 1
    max(sum(rate(requests_total[5m]))) > 1    max(sum(rate(requests_total{module_id="${RULE_PUBLISHER_A}"}[5m]))) > 1
    errors_total / requests_total       errors_total{module_id="${RULE_PUBLISHER_A}"} / requests_total{module_id="${RULE_PUBLISHER_A}"}
    sum by (node) (up)                  sum by (node) (up{module_id="${RULE_PUBLISHER_A}"})
    absent(up)                          absent(up{module_id="${RULE_PUBLISHER_A}"})
    absent_over_time(up[5m])             absent_over_time(up{module_id="${RULE_PUBLISHER_A}"}[5m])

Authored Module Matchers Are Scoped Independently On Every Selector
    [Template]    PromQL Should Be Scoped
    up{module_id="${RULE_PUBLISHER_A}"}       up{module_id="${RULE_PUBLISHER_A}"}
    count({module_id="${RULE_PUBLISHER_A}"})    count({module_id="${RULE_PUBLISHER_A}"})
    count({module_id=~".+"})                 count({module_id="${RULE_PUBLISHER_A}"})    ${True}
    up{module_id="other1"}                   up{module_id="${RULE_PUBLISHER_A}"}    ${True}
    up{module_id=~"metrics.*"}                up{module_id="${RULE_PUBLISHER_A}"}    ${True}
    up{module_id!="other1"}                   up{module_id="${RULE_PUBLISHER_A}"}    ${True}
    up{module_id!~"metrics.*"}                up{module_id="${RULE_PUBLISHER_A}"}    ${True}
    up{module_id="${RULE_PUBLISHER_A}"} + errors_total    up{module_id="${RULE_PUBLISHER_A}"} + errors_total{module_id="${RULE_PUBLISHER_A}"}    ${True}
    up{module_id="${RULE_PUBLISHER_A}"} + errors_total{module_id="other1"}    up{module_id="${RULE_PUBLISHER_A}"} + errors_total{module_id="${RULE_PUBLISHER_A}"}    ${True}

Authored Temporary Label Lookalikes Survive Rewriting
    [Template]    PromQL Should Be Scoped
    up{__ns8_rule_scope="authored"}    up{__ns8_rule_scope="authored",module_id="${RULE_PUBLISHER_A}"}
    up{__ns8_rule_scope="authored",__ns8_rule_scope_="also authored"}    up{__ns8_rule_scope="authored",__ns8_rule_scope_="also authored",module_id="${RULE_PUBLISHER_A}"}
    up{job="__ns8_rule_scope"}    up{job="__ns8_rule_scope",module_id="${RULE_PUBLISHER_A}"}

Selector-Free And Invalid PromQL Are Rejected Independently
    [Template]    PromQL Should Be Rejected
    vector(1)    no vector or range selector
    1 + 2        no vector or range selector
    up{          parse error
    # Promtool v3.5.3 panics when deleting two matchers for the same label.
    # Provisioning must reject that source and still load its valid neighbours.
    count({module_id="x",module_id!="y"})    panic: runtime error: slice bounds out of range

Colliding Rule Filenames Reject Both Sources
    ${first} =    Single Alert Rule Payload    Phase5CollisionFirst    up == 0
    ${second} =    Single Alert Rule Payload    Phase5CollisionSecond    up == 0
    Write Publisher Rule    phase5_a_b    c    ${first}
    Write Publisher Rule    phase5_a    b_c    ${second}
    ${cursor} =    Provision Rule Fixtures
    Provider Rule File Should Be Absent    phase5_a_b    c
    Prometheus Rule Should Be Absent    Phase5CollisionFirst
    Prometheus Rule Should Be Absent    Phase5CollisionSecond
    FOR    ${source}    IN    module/phase5_a_b/metrics_alert_rules field 'c'    module/phase5_a/metrics_alert_rules field 'b_c'
        Source Journal Should Contain    ${cursor}    Skipped module alert rule from ${source}
        ...    output filename collision for 'provision_phase5_a_b_c.yml'
        ...    module/phase5_a_b/metrics_alert_rules field 'c'
        ...    module/phase5_a/metrics_alert_rules field 'b_c'
    END

Only The Exact Recorded Owner Can Retain A Valid Rule File
    ${payload} =    Single Alert Rule Payload    Phase5OwnedRule    up == 0
    Write Publisher Rule    phase5_a_b    c    ${payload}
    Provision Rule Fixtures
    Prometheus Rule Should Exist    Phase5OwnedRule
    ${before} =    Provider Rule Checksum    phase5_a_b    c
    Provider Rule File Should Contain    phase5_a_b    c
    ...    \# ns8-metrics-source: {"field":"c","redis_key":"module/phase5_a_b/metrics_alert_rules"}
    Write Publisher Rule    phase5_a_b    c    groups: [\n
    ${cursor} =    Provision Rule Fixtures
    ${after} =    Provider Rule Checksum    phase5_a_b    c
    Should Be Equal    ${after}    ${before}
    Prometheus Rule Should Exist    Phase5OwnedRule
    Source Journal Should Contain    ${cursor}    module/phase5_a_b/metrics_alert_rules field 'c'    invalid YAML
    # Same basename, different Redis key AND field: the old owner has disappeared.
    Delete Publisher Rule Field    phase5_a_b    c
    Write Publisher Rule    phase5_a    b_c    groups: [\n
    ${cursor} =    Provision Rule Fixtures
    Provider Rule File Should Be Absent    phase5_a    b_c
    Prometheus Rule Should Be Absent    Phase5OwnedRule
    Source Journal Should Contain    ${cursor}    module/phase5_a/metrics_alert_rules field 'b_c'    invalid YAML

Maximum Length Rule Filename Installs Independently Of An Overlong Name
    ${field} =    Maximum Filename Field    ${RULE_PUBLISHER_A}
    ${payload} =    Single Alert Rule Payload    Phase5MaximumFilename    up == 0
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${field}    ${payload}
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${field}x    ${payload}
    ${cursor} =    Provision Rule Fixtures
    Provider Rule File Should Exist    ${RULE_PUBLISHER_A}    ${field}
    Provider Rule File Should Be Absent    ${RULE_PUBLISHER_A}    ${field}x
    Prometheus Rule Count Should Be    Phase5MaximumFilename    1
    Source Journal Should Contain    ${cursor}    field '${field}x'    generated rule filename exceeds 255 bytes

Valid And Invalid Updates Within One Publisher Are Independent
    ${first} =    Single Alert Rule Payload    Phase5RetainedWithinPublisher    up == 0
    ${second} =    Single Alert Rule Payload    Phase5ReplacedWithinPublisher    up == 0
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${PRIMARY_RULE_FIELD}    ${first}
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${SECONDARY_RULE_FIELD}    ${second}
    Provision Rule Fixtures
    ${first_before} =    Provider Rule Checksum    ${RULE_PUBLISHER_A}    ${PRIMARY_RULE_FIELD}
    ${second_before} =    Provider Rule Checksum    ${RULE_PUBLISHER_A}    ${SECONDARY_RULE_FIELD}
    ${updated} =    Single Alert Rule Payload    Phase5UpdatedWithinPublisher    absent(up)
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${PRIMARY_RULE_FIELD}    groups: [\n
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${SECONDARY_RULE_FIELD}    ${updated}
    ${cursor} =    Provision Rule Fixtures
    ${first_after} =    Provider Rule Checksum    ${RULE_PUBLISHER_A}    ${PRIMARY_RULE_FIELD}
    ${second_after} =    Provider Rule Checksum    ${RULE_PUBLISHER_A}    ${SECONDARY_RULE_FIELD}
    Should Be Equal    ${first_after}    ${first_before}
    Should Not Be Equal    ${second_after}    ${second_before}
    Prometheus Rule Should Exist    Phase5RetainedWithinPublisher
    Prometheus Rule Should Exist    Phase5UpdatedWithinPublisher
    Prometheus Rule Should Be Absent    Phase5ReplacedWithinPublisher
    Source Journal Should Contain    ${cursor}    field '${PRIMARY_RULE_FIELD}'    invalid YAML

Source Removal Cleans Orphans And Preserves Built-In And Legacy Files
    ${legacy} =    Single Alert Rule Payload    Phase5LegacyCustom    up == 0
    Write Publisher Hash Field    module/${METRICS_ID}/custom_alerts    phase5legacy    ${legacy}
    ${payload} =    Single Alert Rule Payload    Phase5RemovedSource    up == 0
    Write Publisher Rule    ${RULE_PUBLISHER_A}    ${PRIMARY_RULE_FIELD}    ${payload}
    Provision Rule Fixtures
    Prometheus Rule Should Exist    Phase5LegacyCustom
    Prometheus Rule Should Exist    Phase5RemovedSource
    ${builtins_before} =    Rule File Checksums    ${BASELINE_RULE_FILES}
    ${custom_before} =    Module File Checksum    rules.d/custom.yml
    ${orphan} =    Read Module File    rules.d/provision_${RULE_PUBLISHER_A}_${PRIMARY_RULE_FIELD}.yml
    Write New Fixture File    rules.d/provision_phase5_orphan.yml    ${orphan}
    Write New Fixture File    rules.d/provision_phase5_unowned.yml    groups: []
    Delete Publisher Rule Field    ${RULE_PUBLISHER_A}    ${PRIMARY_RULE_FIELD}
    Provision Rule Fixtures
    Provider Rule File Should Be Absent    ${RULE_PUBLISHER_A}    ${PRIMARY_RULE_FIELD}
    Module File Should Be Absent    rules.d/provision_phase5_orphan.yml
    Module File Should Be Absent    rules.d/provision_phase5_unowned.yml
    ${builtins_after} =    Rule File Checksums    ${BASELINE_RULE_FILES}
    ${custom_after} =    Module File Checksum    rules.d/custom.yml
    Dictionaries Should Be Equal    ${builtins_after}    ${builtins_before}
    Should Be Equal    ${custom_after}    ${custom_before}
    Prometheus Rule Should Be Absent    Phase5RemovedSource
    Prometheus Rule Should Exist    NodeOffline
    Prometheus Rule Should Exist    Phase5LegacyCustom
