*** Settings ***
Documentation    Test provider targets and alert rules through provisioning and Prometheus
Resource         resources/module_alert_rules.resource
Suite Setup      Initialize Module Rule Fixtures
Suite Teardown   Finish Module Rule Fixtures
Test Setup       Prepare Module Rule Fixtures
Test Teardown    Reset Module Rule Fixtures


*** Test Cases ***
Provider Targets Receive Their Redis Owner Identity
    ${target_a} =    Target Payload Without Labels    19091
    ${target_b} =    Target Payload With Module Label
    ...    19092
    ...    another1

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
    Source Journal Should Contain    ${event_cursor}
    ...    Target label conflict at Redis key module/${RULE_PUBLISHER_B}/metrics_targets, field ${TARGET_FIELD}, item 0
    ...    overwritten with '${RULE_PUBLISHER_B}'

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
    Source Journal Should Contain    ${event_cursor}
    ...    module/${RULE_PUBLISHER_A}/metrics_alert_rules field '${PRIMARY_RULE_FIELD}'
    ...    Skipped module alert rule
    Source Journal Should Contain    ${event_cursor}
    ...    module/${RULE_PUBLISHER_B}/metrics_alert_rules field '${SECONDARY_RULE_FIELD}'
    ...    no severity label    missing bilingual annotations

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
    Queue Target Structure    - targets: [\n                                  while parsing a flow node
    Queue Target Structure    targets: ["127.0.0.1:19091"]                    target document must be a list of mappings
    Queue Target Structure    - "127.0.0.1:19091"                             target item 0 must be a mapping
    Queue Target Structure    - targets: ["127.0.0.1:19091"]\n\ \ labels: invalid    target item 0 labels must be a mapping
    Check Rejected Target Batch

Invalid Target Field Names Are Isolated
    Queue Target Field    ${EMPTY}
    Queue Target Field    .
    Queue Target Field    ..
    Queue Target Field    bad/name
    Queue Target Field    ../escaped
    Queue Target Field    bad\\name
    Queue Target Field    has space
    Queue Target Field    has:colon
    Queue Target Field    métrics
    Queue Target Field    has\x00nul
    Queue Target Field    has\nnewline
    Queue Target Field    has'quote"$(false)`false`
    Check Rejected Target Batch

Invalid Target Publishers Are Isolated
    Queue Target Publisher    ${EMPTY}         invalid module ID
    Queue Target Publisher    .                invalid module ID
    Queue Target Publisher    ..               invalid module ID
    Queue Target Publisher    a/b              invalid Redis key shape
    Queue Target Publisher    ../escaped       invalid Redis key shape
    Queue Target Publisher    has space        invalid module ID
    Queue Target Publisher    has:colon        invalid module ID
    Queue Target Publisher    bad\\name        invalid module ID
    Queue Target Publisher    métrics1         invalid module ID
    Queue Target Publisher    has'quote        invalid module ID
    Check Rejected Target Batch

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
    Source Journal Should Contain    ${cursor}    Skipped target '${field}x' for module '${RULE_PUBLISHER_A}' at Redis key 'module/${RULE_PUBLISHER_A}/metrics_targets'
    ...    generated target filename exceeds 255 bytes
    Directory Should Contain Only Added Files    prometheus.d    ${baseline}
    ...    provision_${RULE_PUBLISHER_A}_${field}.yml
    ...    provision_${RULE_PUBLISHER_A}_${TARGET_FIELD}.yml

Invalid Rule Encodings And YAML Are Isolated
    Queue Rule Encoding    utf8    payload is not valid UTF-8
    Queue Rule Encoding    yaml    invalid YAML
    Check Rejected Rule Batch

Unsupported Rule Schemas Are Isolated
    Queue Rule Schema    document    __self__       scalar              payload must decode to a mapping
    Queue Rule Schema    document    __self__       ${EMPTY_MAPPING}    payload must be a groups document or a single alert rule
    Queue Rule Schema    document    groups         invalid             'groups' must be a list
    Queue Rule Schema    group       __self__       invalid             group 0 must be a mapping
    Queue Rule Schema    group       name           __remove__          group 0 must have a non-empty string name
    Queue Rule Schema    group       name           ${42}               group 0 must have a non-empty string name
    Queue Rule Schema    group       rules          __remove__          must have a rules list
    Queue Rule Schema    group       rules          invalid             must have a rules list
    Queue Rule Schema    group       labels         invalid             labels must be a mapping
    Queue Rule Schema    rule        __self__       invalid             rule 0 must be a mapping
    Queue Rule Schema    rule        alert          __remove__          must have a non-empty string 'alert' field
    Queue Rule Schema    rule        alert          ${SPACE}            must have a non-empty string 'alert' field
    Queue Rule Schema    rule        alert          ${42}               must have a non-empty string 'alert' field
    Queue Rule Schema    rule        expr           __remove__          must have a non-empty string 'expr' field
    Queue Rule Schema    rule        expr           ${EMPTY}            must have a non-empty string 'expr' field
    Queue Rule Schema    rule        expr           ${42}               must have a non-empty string 'expr' field
    Queue Rule Schema    rule        record         saved_up            is a recording rule; only alerts are supported
    Queue Rule Schema    rule        labels         invalid             labels must be a mapping
    Queue Rule Schema    rule        annotations    invalid             annotations must be a mapping
    Check Rejected Rule Batch

Single Recording Rules Are Rejected
    ${document} =    Single Alert Rule Document
    Remove From Dictionary    ${document}    alert
    Set To Dictionary    ${document}    record    saved_up
    ${payload} =    Serialize Fixture YAML    ${document}
    Rule Payload Should Be Rejected    ${payload}    recording rules are not supported

Reserved Duplicate And Blank Group Names Are Rejected
    Queue Local Group Names    uses the reserved 'ns8:' prefix          ns8:reserved
    Queue Local Group Names    duplicate local group name 'repeated'    repeated    ${SPACE}repeated${SPACE}
    Queue Local Group Names    must have a non-empty string name        ${SPACE * 3}
    Check Rejected Rule Batch

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
    Queue Rule Identity    another1               another2               ${True}
    Queue Rule Identity    ${RULE_PUBLISHER_A}     ${RULE_PUBLISHER_A}     ${False}
    Check Successful Rule Batch

Authored Metadata Is Retained With Specific Warnings
    Queue Rule Metadata    severity       info
    Queue Rule Metadata    annotations    summary_en
    Check Successful Rule Batch

PromQL Selector Shapes Are Scoped By The Real Parser
    Queue Scoped PromQL    -up == -1                           -up{module_id="${RULE_PUBLISHER_A}"} == -1
    Queue Scoped PromQL    ${SPACE * 2}-up == -1                -up{module_id="${RULE_PUBLISHER_A}"} == -1
    Queue Scoped PromQL    rate(requests_total[5m]) > 1         rate(requests_total{module_id="${RULE_PUBLISHER_A}"}[5m]) > 1
    Queue Scoped PromQL    max(sum(rate(requests_total[5m]))) > 1    max(sum(rate(requests_total{module_id="${RULE_PUBLISHER_A}"}[5m]))) > 1
    Queue Scoped PromQL    errors_total / requests_total       errors_total{module_id="${RULE_PUBLISHER_A}"} / requests_total{module_id="${RULE_PUBLISHER_A}"}
    Queue Scoped PromQL    sum by (node) (up)                  sum by (node) (up{module_id="${RULE_PUBLISHER_A}"})
    Queue Scoped PromQL    absent(up)                          absent(up{module_id="${RULE_PUBLISHER_A}"})
    Queue Scoped PromQL    absent_over_time(up[5m])             absent_over_time(up{module_id="${RULE_PUBLISHER_A}"}[5m])
    Check Scoped PromQL Batch

Authored Module Matchers Are Scoped Independently On Every Selector
    Queue Scoped PromQL    up{module_id="${RULE_PUBLISHER_A}"}       up{module_id="${RULE_PUBLISHER_A}"}
    Queue Scoped PromQL    count({module_id="${RULE_PUBLISHER_A}"})    count({module_id="${RULE_PUBLISHER_A}"})
    Queue Scoped PromQL    count({module_id=~".+"})                 count({module_id="${RULE_PUBLISHER_A}"})    ${True}
    Queue Scoped PromQL    up{module_id="other1"}                   up{module_id="${RULE_PUBLISHER_A}"}    ${True}
    Queue Scoped PromQL    up{module_id=~"metrics.*"}                up{module_id="${RULE_PUBLISHER_A}"}    ${True}
    Queue Scoped PromQL    up{module_id!="other1"}                   up{module_id="${RULE_PUBLISHER_A}"}    ${True}
    Queue Scoped PromQL    up{module_id!~"metrics.*"}                up{module_id="${RULE_PUBLISHER_A}"}    ${True}
    Queue Scoped PromQL    up{module_id="${RULE_PUBLISHER_A}"} + errors_total    up{module_id="${RULE_PUBLISHER_A}"} + errors_total{module_id="${RULE_PUBLISHER_A}"}    ${True}
    Queue Scoped PromQL    up{module_id="${RULE_PUBLISHER_A}"} + errors_total{module_id="other1"}    up{module_id="${RULE_PUBLISHER_A}"} + errors_total{module_id="${RULE_PUBLISHER_A}"}    ${True}
    Check Scoped PromQL Batch

Authored Temporary Label Lookalikes Survive Rewriting
    Queue Scoped PromQL    up{__ns8_rule_scope="authored"}    up{__ns8_rule_scope="authored",module_id="${RULE_PUBLISHER_A}"}
    Queue Scoped PromQL    up{__ns8_rule_scope="authored",__ns8_rule_scope_="also authored"}    up{__ns8_rule_scope="authored",__ns8_rule_scope_="also authored",module_id="${RULE_PUBLISHER_A}"}
    Queue Scoped PromQL    up{job="__ns8_rule_scope"}    up{job="__ns8_rule_scope",module_id="${RULE_PUBLISHER_A}"}
    Check Scoped PromQL Batch

Selector-Free And Invalid PromQL Are Rejected Independently
    Queue Rejected PromQL    vector(1)    no vector or range selector
    Queue Rejected PromQL    1 + 2        no vector or range selector
    Queue Rejected PromQL    up{          parse error
    # Promtool v3.5.3 panics when deleting two matchers for the same label.
    # Provisioning must reject that source and still load its valid neighbours.
    Queue Rejected PromQL    count({module_id="x",module_id!="y"})    panic: runtime error: slice bounds out of range
    Check Rejected Rule Batch

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
    Source Journal Should Contain    ${cursor}    module/${RULE_PUBLISHER_A}/metrics_alert_rules field '${field}x'    generated rule filename exceeds 255 bytes

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
    Source Journal Should Contain    ${cursor}    module/${RULE_PUBLISHER_A}/metrics_alert_rules field '${PRIMARY_RULE_FIELD}'    invalid YAML

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

Invalid UTF-8 Targets Do Not Block Valid Publishers
    ${baseline} =    Module Directory Files    prometheus.d
    ${valid} =    Target Payload Without Labels    19091
    ${bad_key} =    Convert To Bytes    module/phase5bad\xff/metrics_targets
    ${bad_field} =    Convert To Bytes    phase5bad\xff
    ${bad_payload} =    Convert To Bytes    \xff
    Write Publisher Hash Field    ${bad_key}    phase5invalid    ${valid}
    Write Publisher Target    ${RULE_PUBLISHER_A}    ${valid}    ${bad_field}
    Write Publisher Target    ${RULE_PUBLISHER_A}    ${bad_payload}    phase5invalid
    Write Publisher Target    ${RULE_PUBLISHER_A}    ${valid}
    Write Publisher Target    ${RULE_PUBLISHER_B}    ${valid}
    ${cursor} =    Provision Target Fixtures
    ${key_repr} =    Evaluate    repr($bad_key)
    ${field_repr} =    Evaluate    repr($bad_field)
    Source Journal Should Contain    ${cursor}    Skipped target hash ${key_repr}
    ...    Redis key is not valid UTF-8
    Source Journal Should Contain    ${cursor}
    ...    Skipped target ${field_repr} for module '${RULE_PUBLISHER_A}'
    ...    target type is not valid UTF-8
    Source Journal Should Contain    ${cursor}
    ...    Skipped target 'phase5invalid' for module '${RULE_PUBLISHER_A}'
    ...    target payload is not valid UTF-8
    FOR    ${publisher}    IN    ${RULE_PUBLISHER_A}    ${RULE_PUBLISHER_B}
        ${labels} =    Create Dictionary    module_id=${publisher}    target_type=${TARGET_FIELD}
        Target Document Should Have Labels    ${publisher}    ${TARGET_FIELD}    ${labels}
    END
    Directory Should Contain Only Added Files    prometheus.d    ${baseline}
    ...    provision_${RULE_PUBLISHER_A}_${TARGET_FIELD}.yml
    ...    provision_${RULE_PUBLISHER_B}_${TARGET_FIELD}.yml

Aliased Annotations Retain Their Authored Values
    FOR    ${kind}    IN    single    group
        FOR    ${owner}    IN    absent    authored-text
            ${field} =    Set Variable    phase5alias-${kind}-${owner}
            ${alert} =    Set Variable    Phase5Alias_${kind}_${owner.replace('-', '_')}
            ${payload} =    Aliased Metadata Payload    ${kind}    ${owner}    ${alert}
            Write Publisher Rule    ${RULE_PUBLISHER_A}    ${field}    ${payload}
        END
    END
    ${cursor} =    Provision Rule Fixtures
    FOR    ${kind}    IN    single    group
        FOR    ${owner}    IN    absent    authored-text
            ${field} =    Set Variable    phase5alias-${kind}-${owner}
            ${alert} =    Set Variable    Phase5Alias_${kind}_${owner.replace('-', '_')}
            ${annotations} =    Create Dictionary    severity=warning    fixture=phase5
            IF    $owner != 'absent'    Set To Dictionary    ${annotations}    module_id=${owner}
            ${labels} =    Copy Dictionary    ${annotations}
            Set To Dictionary    ${labels}    module_id=${RULE_PUBLISHER_A}
            ${document} =    Read Provider Rule Document    ${RULE_PUBLISHER_A}    ${field}
            ${rule} =    Set Variable    ${document}[groups][0][rules][0]
            Dictionaries Should Be Equal    ${rule}[annotations]    ${annotations}
            Dictionaries Should Be Equal    ${rule}[labels]    ${labels}
            IF    $kind == 'group'
                Dictionaries Should Be Equal    ${document}[groups][0][labels]    ${labels}
            END
            Loaded Alias Rule Should Preserve Metadata    ${alert}    ${labels}    ${annotations}
            ${source} =    Set Variable    module/${RULE_PUBLISHER_A}/metrics_alert_rules field '${field}'
            IF    $owner == 'absent'
                Source Journal Should Not Contain    ${cursor}    ${source}    has module_id
            ELSE
                Source Journal Should Contain    ${cursor}    alert '${alert}' from ${source}
                ...    has module_id 'authored-text'; using module ID '${RULE_PUBLISHER_A}'
                IF    $kind == 'group'
                    Source Journal Should Contain    ${cursor}    group 'ns8:${RULE_PUBLISHER_A}:${field}:aliases' from ${source}
                    ...    has module_id 'authored-text'; using module ID '${RULE_PUBLISHER_A}'
                END
            END
        END
    END
