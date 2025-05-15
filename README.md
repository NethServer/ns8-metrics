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
- `prometheus_path`: path to access Prometheus web UI, if left blank Prometheus will be not exposed
- `grafana_path`: path to access Grafana web UI, if left blank grafana will be stopped; if enabled default credentials are `admin`/`admin`
- `mail_to`: list of email addresses to receive alerts, this requires that mail notifications are enabled at cluster level
- `mail_from`: email address used to send alerts, if left blank the default value is `alertmanager@<node_fqdn>`
- `mail_template`: name of the template to use to send alerts, if left blank the default template is used

Example:

    api-cli run module/metrics1/configure-module --data '{"prometheus_path": "prometheus", "grafana_path": "grafana", "mail_to": ["alert@nethserver.org"], "mail_from": "no-reply@nethserver.org", "mail_template": ""}'

You can send a test alert to verify the mail configuration:

    runagent -m metrics1 test-alert

Configuration files are saved inside the state directory. The most important files and directory are:

- prometheus.yml: Prometheus configuration
  - prometheus.d: directory containing node configuration files
  - rules.d: directory containing custom alert rules
- alertmanager.yml: Alertmanager configuration
  - templates.d: directory containing custom alert templates
- local.yml: Grafana configuration, if enabled

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
api-cli run module/metrics1/configure-module --data '{"prometheus_path": "prometheus", "grafana_path": "grafana", "mail_from": "no-reply@nethserver.org", "mail_to": ["alert@nethserver.org"], "mail_template": "myalert"}'
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

Example of a target configuration for the `postgresql1` module in JSON format:
```
cat target.yaml | redis-cli -x hset module/postgresql1/metrics_targets postgres
```

Content of the `target.yaml` file:
```yaml
- targets:
  - 10.5.4.1:9187
  labels:
    module_id: postresql1
```

The configuration will be saved on a YAML file inside the `prometheus.d` directory, named like `provision_<module_id>_<name>.json`.


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
