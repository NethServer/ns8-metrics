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

Prometheus sends every alert to the local Alertmanager, which sends mail
and feeds the cluster alert list.

Enterprise (`nsent`) clusters also send alerts to the my Mimir
alertmanager. Its URL and credentials are read from the
`cluster/subscription` key in Redis: the URL is derived from
`collect_url`, and login uses `system_id` and `auth_token`. No extra
configuration is needed.

Community (`nscom`) clusters do not forward alerts. Alerts sent straight to
the local Alertmanager, like `test-alert`, are not forwarded.

### Customize alert rules

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

Custom rules are not scoped to a module: they evaluate all series. To
alert on a single module, add a `module_id` matcher to the expression,
for example:

```yaml
expr: absent(ns8_container_info{module_id="ldapproxy1",unit="ldapproxy.service"})
```

See [Per-module node series](#per-module-node-series) for the series
available for every module.

Before loading the rule, check it with `promtool`. A rule with valid YAML
but an invalid expression prevents Prometheus from starting, stopping
all alerts, including the built-in ones:

    { printf 'groups:\n- name: check\n  rules:\n  -\n'; grep -v '^---$' myalert1.yml | sed 's/^/    /'; } | \
      runagent -m metrics1 podman exec -i prometheus promtool check rules

Load the configuration into Redis by reading it from the file
`myalert1.yml`:

    redis-cli -x hset module/metrics1/custom_alerts myalert1 <myalert1.yml
    runagent -m metrics1 systemctl --user restart prometheus

The restart command succeeds even if Prometheus fails to start, so check
that the service is still active after a few seconds:

    runagent -m metrics1 systemctl --user is-active prometheus

If the service is `failed`, the module log reports the rule that
Prometheus could not parse. Remove that rule and start Prometheus again:

    redis-cli hdel module/metrics1/custom_alerts myalert1
    runagent -m metrics1 systemctl --user reset-failed prometheus
    runagent -m metrics1 systemctl --user restart prometheus

To remove the custom alert, run the following command and restart
Prometheus:

    redis-cli hdel module/metrics1/custom_alerts myalert1
    runagent -m metrics1 systemctl --user restart prometheus

If the alert of a removed rule was firing, it is not resolved: it expires
after about 4 minutes, both in Alertmanager and, when alert forwarding is
enabled, in my.nethesis.it.

Rules with invalid YAML are skipped: if the rule does not appear to be
loaded, inspect the module log on the Logs page, searching for
`Skipped custom rule`.

Custom rules are not included in any backup: neither the module data
backup nor the cluster configuration backup saves them. After a disaster
recovery, load them again. They are preserved only when the leader node
is switched.

Modules ship their own rules in a different way, see the
[metrics-alert-rules-changed event](#metrics-alert-rules-changed-event).
A module that provides its own rules publishes them again when it is
restored, so they don't need a backup.


### Customize alert mail template

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
module/<publisher_id>/metrics_targets
```

The Redis hash contains:

- field `<target_type>`, a stable name identifying the target type;
- value `<yaml_config>`, a Prometheus `file_sd_config` YAML list.

The publisher ID from the Redis key is authoritative. For every target,
provisioning:

- creates the `labels` mapping when it is absent;
- sets `module_id` to `<publisher_id>`;
- sets `target_type` to the Redis field name;
- preserves every other label.

Provider-supplied `module_id` and `target_type` values cannot override this
identity. Conflicting values are replaced and reported with their Redis key,
field, and item position.

Each Redis field is validated independently. A malformed field is skipped
without preventing valid fields from the same or other publishers from being
materialized.

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
prometheus.d/provision_<publisher_id>_<target_type>.yml
```

After adding, updating, or removing a target, the publisher must emit the
`metrics-target-changed` event.

#### metrics-alert-rules-changed event

A module can ship its own alert rules. Prometheus loads them from:

```text
module/<publisher_id>/metrics_alert_rules
```

The Redis hash contains:

- field `<rule_set_name>`, a stable name for the rule set, made of ASCII
  letters, digits, `.`, `_` and `-`;
- value `<yaml_config>`, either a single alert rule or a Prometheus rule
  file with a `groups` list.

Only alert rules are supported: recording rules are rejected. Group names
must not start with the reserved `ns8:` prefix.

The publisher ID from the Redis key is authoritative. For every rule,
provisioning:

- sets the `module_id` matcher on every selector of `expr`, so the rule
  sees only the metrics of the publisher;
- sets the `module_id` label of the alert to `<publisher_id>`;
- prefixes group names with `ns8:<publisher_id>:<rule_set_name>`.

Conflicting `module_id` values are replaced and reported in the log. An
expression without any selector, like `vector(1)`, is rejected.

A warning is also logged when a rule has no `severity` label (use
`warning` or `critical`) or lacks one of the `summary_en`, `summary_it`,
`description_en` and `description_it` annotations.

Each rule set is checked with `promtool` on its own. A broken rule set
never blocks the others, and its last working version stays active. Rule
sets that did not change are not checked again. Rules of removed modules
are deleted.

The generated rules are saved as:

```text
rules.d/provision_<publisher_id>_<rule_set_name>.yml
```

For example, publish a rule that fires when the only container of
`openldap1` stops:

```sh
redis-cli HSET module/openldap1/metrics_alert_rules openldap-down '{alert: OpenldapDown, expr: "absent(ns8_container_info{unit=\"openldap.service\"})", for: 5m, labels: {severity: critical}, annotations: {summary_en: "OpenLDAP is down", summary_it: "OpenLDAP non è attivo", description_en: "The OpenLDAP container is not running", description_it: "Il container di OpenLDAP non è in esecuzione"}}'
redis-cli PUBLISH module/openldap1/event/metrics-alert-rules-changed '{}'
```

After adding, updating, or removing a rule set, the publisher must emit
the `metrics-alert-rules-changed` event. Prometheus reloads its rules
without a restart. The rules are reloaded also when a module is removed.
If a rule does not appear in Prometheus, search the module log for
warnings about `metrics_alert_rules`.

See [Per-module node series](#per-module-node-series) for the series a
module can use to alert on its own containers.

#### Provider alert identity

Alerts carrying `module_id` are grouped by Alertmanager using `alertname`,
`node`, and `module_id`. This keeps same-name alerts from different module
instances in separate notification groups.

Critical alerts inhibit warning alerts only when both `alertname` and
`module_id` match. Alerts without `module_id`, such as alerts generated from
cluster-node targets, retain their previous grouping and inhibition behavior.

Generic downstream identifiers are also scoped by a non-empty `module_id`,
preventing a resolved alert from one module instance from clearing a same-name
alert that is still firing for another instance. Explicit legacy mappings
retain their existing identifiers.

#### Per-module node series

Since core 3.22.0, the node exporter of every node also publishes
per-module series, such as `ns8_container_*` and `ns8_module_*_bytes`.
They identify the module with the `module` label, for example:

```text
ns8_container_info{module="nethvoice-proxy1",container="kamailio",unit="kamailio.service",node="1",target_type="node"} 1
```

Prometheus copies `module` to `module_id` on these series, so they can be
matched by `module_id` like the series of module targets. The copy applies
only to node targets, only when the series has no `module_id` yet, and only
to module IDs: series of core containers (`module="core"`) and of
unattributed containers (`module="unknown"`) get no `module_id`.

Alerts on these series now carry `module_id`, so they follow the grouping
and inhibition rules described above for provider alerts.

For example, a module can detect that one of its containers is not running
while the others are:

```promql
count by (module_id, node) (ns8_container_info{module_id="nethvoice-proxy1"})
  unless on (module_id, node)
count by (module_id, node) (ns8_container_info{module_id="nethvoice-proxy1",unit="kamailio.service"})
```

that none of its containers is running:

```promql
absent(ns8_container_info{module_id="nethvoice-proxy1",unit="kamailio.service"})
```

The `absent()` result has only the labels of its equality matchers: it has
no `node`, and it fires also when the node itself is unreachable.

or that a container keeps restarting:

```promql
changes(ns8_container_start_time_seconds{module_id="nethvoice-proxy1",container="kamailio"}[15m]) > 2
```

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

### Extra Grafana container options

The `PODMAN_RUN_OPTS` environment variable adds options to the `podman
run` command of the Grafana container. It is empty by default.

The value is split on spaces, so each option must not contain spaces.
Setting the variable replaces its previous value: put all the required
options in a single value.

#### Disable telemetry

The checks for Grafana and plugin updates on `grafana.com` are
disabled by default.

Grafana uses only the plugins bundled in its image, so plugin versions
follow the module release. The module builds its own Grafana image
(`grafana/Containerfile`) that adds the Logs Drilldown and Metrics
Drilldown apps, at pinned versions, to the data sources bundled by
Grafana:

- the plugin preinstall is disabled: Grafana does not download plugins
  from `grafana.com` at startup. Other apps, like Traces Drilldown, are
  not available;
- plugin installation from the Grafana plugin catalog is disabled;
- when the module is updated, any plugin found in the Grafana data
  volume is removed.

Renovate updates the Grafana base image and the app versions in
`grafana/Containerfile`, in a single "Grafana image" pull request.

After building the image, `build-images.sh` starts Grafana in a
temporary container (`grafana/check-plugins.sh`) and checks that every
bundled plugin is loaded with a valid signature made by Grafana Labs.
The build fails if a package was altered, or if an app does not support
the Grafana version of the image (`grafanaDependency` in the app
`plugin.json`); the relevant Grafana log lines are printed. The image
build also checks that each downloaded app declares the expected
version.

To install plugins again, add `-e GF_PLUGINS_PREINSTALL_DISABLED=false`
(preinstall) and/or `-e GF_PLUGINS_PLUGIN_ADMIN_ENABLED=true` (catalog)
to `PODMAN_RUN_OPTS`. Plugins are then installed into the Grafana data
volume, updated independently from the module, and kept on module
updates.

Grafana still periodically sends anonymous usage statistics to
`stats.grafana.org`. If the requests are blocked (e.g. by a DNS filter
like Pi-hole) they may generate many DNS queries.

To disable the usage reporting, pass the corresponding Grafana setting
as environment variable:

```bash
runagent -m metrics1 python3 -c 'import agent ; agent.set_env("PODMAN_RUN_OPTS", "-e GF_ANALYTICS_REPORTING_ENABLED=false")'
runagent -m metrics1 systemctl --user restart grafana.service
```

To revert to the defaults, remove the variable:

```bash
runagent -m metrics1 python3 -c 'import agent ; agent.unset_env("PODMAN_RUN_OPTS")'
runagent -m metrics1 systemctl --user restart grafana.service
```

## Testing

Test the module using the `test-module.sh` script:

    ./test-module.sh <NODE_ADDR> ghcr.io/nethserver/metrics:latest

The tests are made using [Robot Framework](https://robotframework.org/)
