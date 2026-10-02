# ns8-metrics

This module implements the metrics engine for NethServer 8.
The module is rootless and runs as a non-privileged user.

It is composed by the following services:

- [Prometheus](https://prometheus.io/)
- [Alertmanager](https://prometheus.io/docs/alerting/alertmanager/)
- [Grafana](https://grafana.com/)

Behavior:

- there is only one instance of the module inside all the cluster, the instance runs only on the leader node
- it automatically monitors all cluster nodes
- if a leader node becomes a worker, the module is automatically removed on the worker node
- Prometheus listens on well-known port 9091 (standard port is 9090, but it has been changed to avoid conflicts with Cockpit)
- Alertmanager listens on well-known port 9093
- Grafana is disabled by default, if a Traefik route is configured Grafana will be run on the well-known port 3000

The configuration for Prometheus and Alertmanager is created when Prometheus service is restarted.
The module is restarted when a new node is added or removed from the cluster.
Prometheus is restarted during a subscription-changed event, to update the my alert target.

Available alerts:
- no SWAP is configured
- SWAP is getting full
- One ore more backups have failed
- Paritions are getting full

Prometheus sends alerts to the local Alertmanager. Enterprise (`nsent`)
clusters also send them directly to my Mimir.
Mail notifications can be enabled by setting the `mail_to` parameter, see the [Configure](#configure) section.

## Install

The module is automatically installed by the cluster initialization script.

## Configure

Launch `configure-module`, by setting the following parameters:
- `prometheus_path`: path to access Prometheus web UI, if left blank Prometheus will be not exposed; if enabled, you can authenticate with the same
   credentials used to access the `/cluster-admin` web UI
- `grafana_path`: path to access Grafana web UI, if left blank grafana will be stopped; if enabled, you can authenticate with the same
   credentials used to access the `/cluster-admin` web UI
- `mail_to`: list of email addresses to receive critical alerts, this requires that mail notifications are enabled at cluster level
- `mail_from`: email address used to send alerts, if left blank the default value is `alertmanager@<node_fqdn>`
- `mail_template`: name of the template to use to send alerts, if left blank the default template is used

Example:

    api-cli run module/metrics1/configure-module --data '{"prometheus_path": "prometheus", "grafana_path": "grafana", "mail_to": ["alert@example.org"], "mail_from": "no-reply@example.org", "mail_template": ""}'

You can send a test alert to verify the mail configuration:

    runagent -m metrics1 test-alert

Configuration files are saved inside the state directory. The most important files and directory are:

- prometheus.yml: Prometheus configuration
  - prometheus.d: directory containing node configuration files
  - rules.d: directory containing built-in, legacy custom, and generated
    provider alert rules
- alertmanager.yml: Alertmanager configuration
  - templates.d: directory containing custom alert templates
- local.yml: Grafana configuration, if enabled

### Forwarding alerts to my.nethesis.it

Prometheus sends every alert to the local Alertmanager, which sends mail
and feeds the cluster alert list.

On Enterprise (`nsent`) clusters, Prometheus also sends alerts directly to
the my Mimir alertmanager. Its URL and credentials are read from the
`cluster/subscription` key in Redis: the URL is derived from
`collect_url`, and login uses `system_id` and `auth_token`. No extra
configuration is needed.

Community (`nscom`) clusters do not forward alerts. Alerts sent straight to
the local Alertmanager, like `test-alert`, are not forwarded.

### Customimze alert rules (experimental)

**This is an experimental feature, do not use in production.**
Configuration may change on the future releases.

All alert rules are defined in the `rules.d` directory. Files can't be
modified directly and will be overwritten upon module update.

You can create a custom rule by adding the configuration to Redis.
A carefully curated list of rules can be found at [Awesome Prometheus
alerts](https://samber.github.io/awesome-prometheus-alerts/).

To add a custom rule, create a rule file, load it into Redis, and restart
Prometheus.

Example of `myalert1.yml`:

```yaml
---
alert: HostMemoryUnderMemoryPressure
expr: (rate(node_vmstat_pgmajfault[5m]) > 1000)
for: 0m
labels:
  severity: warning
annotations:
  summary: Host memory under memory pressure (instance {{ $labels.instance }})
  description: |
    The node is under heavy memory pressure. High rate of loading memory pages from disk.
      VALUE = {{ $value }}
      LABELS = {{ $labels }}
```

Load the configuration into Redis by reading it from the file
`myalert1.yml`:

    redis-cli -x hset module/metrics1/custom_alerts myalert1 <myalert1.yml
    runagent -m metrics1 systemctl --user restart prometheus

To remove the custom alert, run the following command and restart
Prometheus:

    redis-cli hdel module/metrics1/custom_alerts myalert1
    runagent -m metrics1 systemctl --user restart prometheus

If the rule does not appear to be loaded, inspect the module log on the
Logs page, searching for YAML parse errors.


### Customize alert mail template (experimental)

**This is an experimental feature, do not use in production.**
Configuration may change on the future releases.

First, create a template file, for example `myalert.tmpl`. Make sure to
define `myalert_subject` and `myalert_html` sections, as they are
used by the module to render the mail. For additional information refer to
[Alertmanager
documentation](https://prometheus.io/docs/alerting/latest/notification_examples/).

Example of `myalert.tmpl` contents:

```text
{{ define "myalert_subject" }}Alert on {{ range .Alerts.Firing }}{{ .Labels.instance }} {{ end }}{{ end }}
{{ define "myalert_html" }}
<html>
<head>
<title>Alert!</title>
</head>
<body>
{{ range .Alerts.Firing }}
<p>{{ .Labels.alertname }} on {{ .Labels.instance }}<br/>
{{ if ne .Annotations.summary "" }}{{ .Annotations.summary }}{{ end }}</p>
<p>Details:</p>
<p>
{{ range .Annotations.SortedPairs }}
  {{ .Name }} = {{ .Value }}<br/>
{{ end }}
</p>
<p>
{{ range .Labels.SortedPairs }}
  {{ .Name }} = {{ .Value }}<br/>
{{ end }}
</p>
{{ end }}
</body></html>
{{ end }}
```

Load the template file in Redis DB:

```
redis-cli -x hset module/metrics1/custom_templates myalert <myalert.tmpl
```

Configure the module to use the new template:
```
api-cli run module/metrics1/configure-module --data '{"prometheus_path": "prometheus", "grafana_path": "grafana", "mail_from": "no-reply@example.org", "mail_to": ["alert@example.org"], "mail_template": "myalert"}'
```

You can test the template rendering using the following command:
```
runagent -m metrics1
podman exec -ti alertmanager amtool template render --template.glob='/etc/alertmanager/templates/*.tmpl' --template.text='{{ template "myalert_html" . }}'
podman exec -ti alertmanager amtool template render --template.glob='/etc/alertmanager/templates/*.tmpl' --template.text='{{ template "myalert_subject" . }}'
```

### Provisioning Prometheus

The `prometheus` service is configured to load all targets from the `prometheus.d` directory.
If a target is added or removed, prometheus will automatically reload the configuration.

When a module wants to add a new target, it must use the `metrics-target-changed` event.

#### metrics-target-changed event

The `provision-prometheus` script searches for targets in:

```text
module/<module_id>/metrics_targets
```

The Redis hash contains:

- field `<target_type>`, a stable name identifying the target type;
- value `<yaml_config>`, a Prometheus `file_sd_config` YAML list.

The module ID from the Redis key is authoritative. For every target,
provisioning:

- creates the `labels` mapping when it is absent;
- sets `module_id` to `<module_id>`;
- sets `target_type` to the Redis field name;
- preserves every other label.

Provider-supplied `module_id` and `target_type` values cannot override this
identity. Conflicting values are replaced and reported with their Redis key,
field, and item position.

Each Redis field is validated independently. A malformed field is skipped
without preventing valid fields from the same or other publishers from being
materialized.

Target hashes must have exactly the form `module/<module_id>/metrics_targets`.
Module IDs and target field names must be non-empty and use only ASCII letters,
digits, `.`, `_`, and `-`. Neither may be `.` or `..`. The generated filename
must fit the 255-byte limit. Invalid names are rejected before file access and
reported with their Redis key and field. Actual filesystem failures still
abort provisioning. Because generated targets are rebuilt on each pass, an
invalid replacement removes the previously generated target configuration.

For example, publish a PostgreSQL target with:

```sh
redis-cli -x hset \
  module/postgresql1/metrics_targets \
  postgres < target.yml
```

Content of `target.yml`:

```yaml
- targets:
  - 10.5.4.1:9187
  labels:
    node: "1"
```

Provisioning adds the authoritative labels:

```yaml
- targets:
  - 10.5.4.1:9187
  labels:
    node: "1"
    module_id: postgresql1
    target_type: postgres
```

The generated configuration is saved as:

```text
prometheus.d/provision_<module_id>_<target_type>.yml
```

After adding, updating, or removing a target, the publisher must emit the
`metrics-target-changed` event.

#### Provider alert identity

Alerts carrying `module_id` are grouped by the local Alertmanager using `alertname`,
`node`, and `module_id`. This keeps same-name alerts from different module
instances in separate notification groups.

Critical alerts inhibit warning alerts only when both `alertname` and
`module_id` match. Alerts without `module_id`, such as alerts generated from
cluster-node targets, retain their previous grouping and inhibition behavior.

### Module-provided alert rules

A module instance can publish alerts for its own metrics through a Redis hash:

```text
module/<module_id>/metrics_alert_rules
```

The metrics module reads this hash during provisioning, scopes each rule to
the publishing instance, and validates it before installing a generated rule
file. Publish the corresponding scrape targets through
[`metrics_targets`](#metrics-target-changed-event) so their series carry the
same `module_id` used to scope the rules. Generated files belong to the metrics
module and must not be edited directly.

Each hash field is a stable `<rule_set_name>` whose value is UTF-8 YAML. Save
either of the following examples as `alerts.yml`. A complete rule file contains
named groups:

```yaml
groups:
- name: postgresql.rules
  rules:
  - alert: PostgresqlDown
    expr: up{target_type="postgres"} == 0
    for: 5m
    labels:
      severity: critical
    annotations:
      summary_en: PostgreSQL is down
      summary_it: PostgreSQL non raggiungibile
      description_en: The PostgreSQL exporter cannot be scraped.
      description_it: Impossibile contattare l'exporter PostgreSQL.
```

A single alert rule omits the `groups` wrapper:

```yaml
alert: PostgresqlConnectionsHigh
expr: pg_stat_activity_count{target_type="postgres"} > 100
for: 10m
labels:
  severity: warning
annotations:
  summary_en: Too many PostgreSQL connections
  summary_it: Troppe connessioni PostgreSQL
  description_en: PostgreSQL has more than 100 active connections.
  description_it: PostgreSQL ha più di 100 connessioni attive.
```

Module IDs and rule-set names must be non-empty and contain only ASCII letters,
digits, `.`, `_`, and `-`. Neither may be `.` or `..`, and the generated filename
must fit within 255 bytes. Each field generates one file:

```text
rules.d/provision_<module_id>_<rule_set_name>.yml
```

Single rules receive the group name `ns8:<module_id>:<rule_set_name>`, while
groups in complete files receive
`ns8:<module_id>:<rule_set_name>:<local_group_name>`. This keeps group names
distinct across publishers and rule sets. Local group names must be non-empty,
unique within the field after trimming surrounding whitespace, and must not
start with the reserved `ns8:` prefix.

#### Identity and query scoping

The module ID from the Redis key is authoritative for targets, expressions,
and alert labels. Every vector or range selector is rewritten with the exact
module ID matcher. For example, a rule published by `postgresql1` changes from:

```promql
up{target_type="postgres"} == 0
```

to a canonically formatted expression restricted to that instance:

```promql
up{module_id="postgresql1",target_type="postgres"} == 0
```

The generated rule also has the static label `module_id: postgresql1`. This
keeps the alert identity when an aggregation removes labels from its query
result. Because selectors are restricted to the publisher's series, node-wide,
cluster-wide, or cross-module rules belong in the built-in metrics rules.

Existing exact `module_id` matchers are retained after canonical formatting.
Authored matchers that conflict with the publisher's scope, use regular
expressions or negation, or cover only some selectors produce a warning when
rewritten. Conflicting static `module_id` labels are also replaced and reported.
Expressions without a vector or range selector, such as `vector(1)`, are rejected.

Rewriting and rule validation use `promtool` from the module's declared
Prometheus image. Avoid multiple `module_id` matchers in one selector: the
integration suite covers a Prometheus 3.5.3 rewrite failure for
`count({module_id="x",module_id!="y"})`. That input is rejected with the file
retention behavior described below.

#### Validation, retention, and warnings

Each field is validated independently, and a failure rejects the whole field.
Invalid identifiers, non-UTF-8 payloads, malformed YAML, recording rules,
invalid PromQL, duplicate local group names, and failed `promtool check rules`
validation prevent installation. If multiple sources produce the same filename,
all colliding sources are rejected. Valid fields can still be installed.

For a source with valid identifiers, a rejected replacement retains the previous
generated file byte-for-byte when its ownership header matches that exact Redis
key and field. A new invalid source creates no file. Redis still contains the
rejected value, so correct it and publish the event again to install an update.
Redis, container tooling, and filesystem failures instead abort provisioning.
Files already installed during that pass are not rolled back.

Use `severity: warning` or `severity: critical` and provide `summary_en`,
`summary_it`, `description_en`, and `description_it` annotations. Missing or
non-recommended metadata produces warnings rather than rejection by itself,
but the resulting file must still pass `promtool` validation.

Validation does not check whether referenced metrics have been scraped, so a
loaded rule can return no data. It also adds no duplicate-identity warning for
repeated `(alertname, module_id)` pairs. Other labels, including severity, can
distinguish same-name alert instances, subject to normal `promtool` validation.

#### Publish, activate, and remove rules

Run the following commands on an NS8 node with access to the cluster Redis.
Replace `postgresql1` with the publishing module instance and `postgres` with
its stable rule-set name. Store `alerts.yml`, then publish an event with an
empty JSON object on that instance's channel:

```bash
redis-cli -x hset \
  module/postgresql1/metrics_alert_rules \
  postgres < alerts.yml
redis-cli publish \
  module/postgresql1/event/metrics-alert-rules-changed '{}'
```

Successful Redis commands establish publication, not validation or activation.
The event handler provisions the files, then reloads an active Prometheus
instance. It verifies the reload-success metric and an advancing reload
timestamp, falling back to a checked restart if reload fails or cannot be
verified. An inactive service remains stopped and reads the generated files at
its next normal start. If provisioning or restart verification fails, the
handler reports an error.

Inspect the metrics module logs for rejected fields, metadata warnings, or
activation errors. In Prometheus, check the Rules page for the generated group
and its scoped expression. For the complete example above, the group is
`ns8:postgresql1:postgres:postgresql.rules`. A loaded rule only fires when its
expression returns a matching result for the configured `for` duration.

To remove this rule set, delete its field and publish the same event:

```bash
redis-cli hdel module/postgresql1/metrics_alert_rules postgres
redis-cli publish \
  module/postgresql1/event/metrics-alert-rules-changed '{}'
```

Publishers should remove obsolete fields during uninstall, disable, or restore.
Provisioning removes generated files whose source fields no longer exist, and
the `module-removed` event also triggers provisioning and verified activation.

Module alerts use the same [delivery configuration](#forwarding-alerts-to-mynethesisit)
as built-in alerts. Prometheus sends them directly to the local Alertmanager
and, for Enterprise (`nsent`) subscriptions, to my Mimir. Alert names and
labels, including `module_id`, are sent unchanged. Critical alerts use the
configured local email route.

This publisher contract applies only to `metrics_alert_rules`. The existing
experimental metrics-local `custom_alerts` interface remains a separate legacy
path and is not migrated by this feature.

The implementation is in
[`metrics_alert_rules.py`](imageroot/bin/metrics_alert_rules.py) and
[`reload-prometheus-rules`](imageroot/bin/reload-prometheus-rules), with live
workflow coverage in
[`20__module_alert_rules.robot`](tests/20__module_alert_rules.robot).

### Provisioning Grafana

The Grafana service is configured to load all datasources and dashboards from the `datasources` and `dashboards` directories.
The service must be restarted when a new datasource or dashboard is added or removed.

### Dashboards

Dashboards are defined in JSON format. The module provides 2 types of dashboards:
- core dashboards: these dashboards are bundled inside the module in the `imageroot/etc/dashboards` directory.
  Such dashboards are copied inside the `dashboards/core` directory when the Grafana service is started.
- module dashboards: these dashboards are created by other modules, the configuration is stored inside the Redis DB.
  When a module wants to add a new dashboard, it must use the `metrics-dashboard-changed` event.
  Such dashboards are saved inside the `dashboards/modules` directory.

##### metrics-dashboard-changed

The `provision-grafana` script will search for the following key: `module/<module_id>/metrics_dashboards`.
The key is an hash containing the following fields:
- key `<name>`, a name for the dashboard
- value `<json_config>`, the JSON configuration for the dashboard

Each dashboard will be saved on a file inside the `dashboards` directory, named like `provision_<module_id>_<name>.json`.

Example of a dashboard configuration for the `postgresql1` module:
```
cat dashboard.json |  redis-cli -x hset module/postgresql1/metrics_dashboards phonebook
```

**Note**: if multiple modules define the a dashboard with the same uid, only the first one will be used.

### Datasources

Datatsources are defined in YAML format and can be added by other modules.
When a module wants to add a new datasource, it must use the `metrics-datasource-changed` event.

##### metrics-datasource-changed event

The `provision-grafana` script will search for the following key: `module/<module_id>/metrics_datasources`.
The key is an hash containing the following key-value pairs:
- key `<name>`, a name for the datasource
- value `<yaml_config>`, the YAML configuration for the datasource

Each datasource will be saved on a different file inside the `datasources` directory, named like `provision_<module_id>_<name>.json`.

Example of a datasource configuration for the `postgresql1` module:
```
cat datasource.yml | redis-cli -x hset module/postgresql1/metrics_datasources samba_audit
```

The YAML must reflect the Grafana datasource configuration, see this [example](https://grafana.com/docs/grafana/latest/datasources/postgres/configure/#provision-the-data-source) for Postgres.

Example of a datasource configuration for the `postgresql1` module in YAML format:
```yaml
apiVersion: 1
datasources:
- name: SambaAudit
  type: postgres
  url: 10.5.4.1:20004
  user: smbaudit_reader
  secureJsonData:
    password: smbauditpass
  jsonData:
    database: samba_audit
    sslmode: disable
    maxOpenConns: 100
    maxIdleConns: 100
    maxIdleConnsAuto: true
    connMaxLifetime: 14400
    postgresVersion: 14000
    timescaledb: false
```

## Testing

Test the module using the `test-module.sh` script:

    ./test-module.sh <NODE_ADDR> ghcr.io/nethserver/metrics:latest

The tests are made using [Robot Framework](https://robotframework.org/)
