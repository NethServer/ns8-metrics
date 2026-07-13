# ns8-metrics

This module implements the metrics engine for NethServer 8.
The module is rootless and runs as a non-privileged user.

It is composed by the following services:

- [Prometheus](https://prometheus.io/)
- [Alertmanager](https://prometheus.io/docs/alerting/alertmanager/)
- [Grafana](https://grafana.com/)
- [alert-proxy](alert-proxy/README.md)

Behavior:

- there is only one instance of the module inside all the cluster, the instance runs only on the leader node
- it automatically monitors all cluster nodes
- if a leader node becomes a worker, the module is automatically removed on the worker node
- Prometheus listens on well-known port 9091 (standard port is 9090, but it has been changed to avoid conflicts with Cockpit)
- Alertmanager listens on well-known port 9093
- alert-proxy listens on well-known port 9095
- Grafana is disabled by default, if a Traefik route is configured Grafana will be run on the well-known port 3000

The configuration for Prometheus and Alertmanager is created when Prometheus service is restarted.
The module is restarted when a new node is added or removed from the cluster.
The alert-proxy service is restarted during a subscription-change event: if there is a valid subscription, the service will start
sending alerts to my.nethesis.it or my.nethserver.com.

Available alerts:
- no SWAP is configured
- SWAP is getting full
- One ore more backups have failed
- Paritions are getting full

By default, the system will send alerts only to Nethesis portals.
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
  - rules.d: directory containing custom alert rules
- alertmanager.yml: Alertmanager configuration
  - templates.d: directory containing custom alert templates
- local.yml: Grafana configuration, if enabled

### Forwarding alerts to my.nethesis.it

Enterprise (`nsent`) clusters with a valid my.nethesis.it subscription forward
their alerts automatically, mirroring `send-heartbeat` / `send-inventory` in
ns8-core. The alert-proxy POSTs alerts to the credential-translation proxy at
`https://my.nethesis.it/proxy/alerts` using the existing subscription
credentials (`system_id` / `auth_token`), which the proxy maps to the new my
credentials before forwarding them to the Mimir alertmanager. No extra
configuration is required: `write-alert-proxy-envfile` derives everything from
`cluster/subscription`.

Community (`nscom`) clusters keep sending alerts to dartagnan
(my.nethserver.com) and are unaffected.

> Migration note: the my switch-off release will repoint this from
> `/proxy/alerts` to the native collect endpoint
> (`/collect/api/services/mimir/alertmanager/api/v2/alerts`) with rotated
> credentials.

### Customize alert rules (experimental)

**This is an experimental feature, do not use in production.**
Configuration may change on the future releases.

All alert rules are defined in the `rules.d` directory. Files can't be
modified directly and will be overwritten upon module update.

Custom alerts use the same accepted formats and validation pipeline described
under the [metrics-alert-rules-changed event](#metrics-alert-rules-changed-event).
Compatibility with the previous experimental behavior is not guaranteed;
malformed or unsupported values are skipped.

You can create a custom rule by adding the configuration to Redis.
A carefully curated list of rules can be found at [Awesome Prometheus
alerts](https://samber.github.io/awesome-prometheus-alerts/).

To add a custom rule, create a rule file, load it into Redis, and signal the
alert-rule event.

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
    redis-cli publish module/metrics1/event/metrics-alert-rules-changed '{}'

To remove the custom alert, run the following commands:

    redis-cli hdel module/metrics1/custom_alerts myalert1
    redis-cli publish module/metrics1/event/metrics-alert-rules-changed '{}'

If the rule does not appear to be loaded, inspect the module log on the
Logs page, searching for validation errors.


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

The `provision-prometheus` script will search for the following key: `module/<module_id>/metrics_targets`.
The key is an hash containing the following fields:
- key `<name>`, a name for the target
- value `<yaml_config>`, the YAML configuration for the target

Example of a target configuration for the `postgresql1` module:
```
cat target.yaml | redis-cli -x hset module/postgresql1/metrics_targets postgres
```

Content of the `target.yaml` file:
```yaml
- targets:
  - 10.5.4.1:9187
```

The module ID in the Redis key is authoritative. The provisioner creates a
`labels` mapping when needed and sets both `module_id: postgresql1` and
`target_type: postgres`. A conflicting publisher-supplied `module_id` is
overwritten with a warning; a non-mapping `labels` value rejects that target
field. The resulting YAML file is named
`prometheus.d/provision_<module_id>_<name>.yml`.

#### metrics-alert-rules-changed event

A module instance can publish alerting rules in the following Redis hash:

```text
module/<module_id>/metrics_alert_rules
```

The hash contains:

- key `<name>`, a stable identifier containing only letters, numbers, `.`,
  `_`, or `-`;
- value `<yaml_config>`, either a complete Prometheus `groups:` document or a
  single alerting rule.

##### Choosing a payload format

Prometheus ultimately loads every payload as a complete rule file containing
one or more groups. Modules can publish either that complete structure or let
the `metrics` module create a group around one alert.

| Payload format | Recommended use |
| --- | --- |
| Complete Prometheus file (`groups:`) | Multiple related groups or alerts |
| Single alert rule (`alert` and `expr`) | One independently managed alert |

A complete rule file can contain multiple groups, each with one or more alerts.
Authored group names are local identifiers; the provisioner rewrites them into
the reserved `ns8:` namespace.

Complete rule-file format:

```yaml
groups:
- name: postgresql.rules
  rules:
  - alert: PostgresqlDown
    expr: up{target_type="postgres"} == 0
    for: 5m
    labels:
      severity: critical
      service: postgresql
    annotations:
      summary_en: "PostgreSQL is down ({{ $labels.module_id }})"
      summary_it: "PostgreSQL non raggiungibile ({{ $labels.module_id }})"
      description_en: "The PostgreSQL service is not reachable."
      description_it: "Il servizio PostgreSQL non è raggiungibile."
```

Single-rule format:

```yaml
alert: PostgresqlDown
expr: up{target_type="postgres"} == 0
for: 5m
labels:
  severity: critical
  service: postgresql
annotations:
  summary_en: "PostgreSQL is down ({{ $labels.module_id }})"
  summary_it: "PostgreSQL non raggiungibile ({{ $labels.module_id }})"
  description_en: "The PostgreSQL service is not reachable."
  description_it: "Il servizio PostgreSQL non è raggiungibile."
```

The provisioner derives the module identity from the Redis owner. For a field
named `postgres` under `module/postgresql1/metrics_alert_rules`, the full-file
example above becomes:

```yaml
groups:
- name: ns8:postgresql1:postgres:postgresql.rules
  rules:
  - alert: PostgresqlDown
    expr: up{module_id="postgresql1", target_type="postgres"} == 0
    labels:
      severity: critical
      service: postgresql
      module_id: postgresql1
```

A single-rule payload uses `ns8:<module_id>:<name>`. Each authored group in a
full-file payload uses
`ns8:<module_id>:<name>:<local_group_name>`. Full payloads must use unique,
non-empty local names and cannot author names beginning with the reserved
`ns8:` prefix. The provisioner rejects duplicate local or effective group names
instead of adding order-dependent suffixes, so names stay stable when groups
are reordered.

Every instant-vector and range-vector selector in a module rule is rewritten
with the exact publishing `module_id`, using Prometheus's PromQL parser. Broader
or conflicting matchers are replaced with a warning. Expressions without a
selector are rejected because they cannot be proven to use module-owned
metrics. The provisioner also injects the authoritative `module_id` into each
alert's static labels, preserving identity when an aggregation removes input
labels. Other labels are preserved.

This strict scoping applies only to `metrics_alert_rules`. Built-in rules and
metrics-local `module/<metrics_id>/custom_alerts` keep their authored
expressions and labels, allowing node-wide and cluster-wide alerts.

Publish a rule and signal its event:

    redis-cli -x hset module/postgresql1/metrics_alert_rules postgres <alerts.yml
    redis-cli publish module/metrics1/event/metrics-alert-rules-changed '{}'

Replace `metrics1` with the cluster's metrics module ID when it differs.

Each Redis hash field produces exactly one file named
`rules.d/provision_<module_id>_<name>.yml`. A complete payload can therefore
place multiple groups or alerts in that file, while a single-rule payload
produces one generated group containing one alert. Delete the Redis field or
hash and signal the event again to remove its generated files. Modules must
also remove their rules during uninstall, disable, or restore operations.

The event provisions all rules, reloads active Prometheus instances with
`SIGHUP`, verifies the reload, and falls back to a restart on failure. It does
not start an inactive Prometheus service.

Malformed YAML, unsupported schemas, recording rules, and rules rejected by
`promtool` are skipped. Invalid file names, duplicate/reserved group names,
unscopable expressions, and invalid label mappings are also rejected. An
invalid update does not replace its previous valid file or prevent other valid
fields from being installed. Missing or unsupported `severity` labels,
incomplete bilingual annotations, and references to unknown metrics generate
warnings without rejecting the rule. Metric checks fail open if the Prometheus
parser or metric-name APIs are unavailable.

The effective module-alert identity is `(alertname, module_id)`. The same alert
name from two module instances is expected; a duplicate identity within one
instance generates a warning but remains loadable. Use a module/application
prefix such as `PostgresqlDown` to make alert names readable.

Module-provided alerts follow the existing default Alertmanager route. They are
forwarded to Nethesis portals and Mimir when subscription credentials are
available. Critical alerts are also sent by email when mail notifications are
configured. Alertmanager groups by `alertname`, `node`, and `module_id`, and
inhibits warnings only when both `alertname` and `module_id` match. For unknown
alert names, `alert-proxy` includes a non-empty module ID in its generic external
ID: `<alertname>:<module_id>:node:<node_id>`.


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
