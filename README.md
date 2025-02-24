# ns8-metrics

This module implements the metrics engine for NethServer 8.
The module is rootless and runs as a non-privileged user.

It is composed by the following services:

- [Prometheus](https://prometheus.io/)
- [Alertmanager](https://prometheus.io/docs/alerting/alertmanager/)
- [alert-proxy](alert-proxy/README.md)

Behavior:

- there is only one instance of the module per node
- the only active instance is running on the leader node
- it automatically monitors all cluster nodes
- if a leader node becomes a worker, the module is automatically stopped on the worker node
- Prometheus listens on well-known port 9091 (standard port is 9090, but it has been changed to avoid conflicts with Cockpit)
- Alertmanager listens on well-known port 9093

## Install

The module is automatically installed by the cluster initialization script.

## Configure

Launch `configure-module`, by setting the following parameters:
- `prometheus_path`: path to access prometheus web UI, if left blank prometheus will be not exposed
- `grafana_path`: path to access grafana web UI, if left blank grafana will be stopped; if enabled default credentials are `admin`/`admin`
- `lets_encrypt`: boolean, if set to true traefik will request a valid Let's Encrypt certificate
- `mail_to`: list of email addresses to receive alerts, this requires that mail notifications are enabled at cluster level
- `mail_from`: email address used to send alerts, if left blank the default value is `alertmanager@<node_fqdn>`

Example:

    api-cli run module/metrics1/configure-module --data '{"prometheus_path": "prometheus", "grafana_path": "grafana", "lets_encrypt": false, "mail_to": ["alert@nethserver.org"]}'

You can send a test alert to verify the mail configuration:

    runagent -m metrics1 test-alert

### Customimze alert rules

You can add custom alert rules by creating a custom rule file in the `rules.d` directory.
A very curated list of rules can be found at [Awesome Prometheus alerts](https://samber.github.io/awesome-prometheus-alerts/).

To add a rule, enter the module, then create a rule file and reload the module:
```
runagent -m metrics1
cat > rules.d/custom.rules <<EOF
<your rules here>
EOF
```

Restart the service:
```
systemctl --user restart alertmanager
```

### Customize alert mail template

You can change the mail template used to send alerts by creating a custom template in the `templates.d` directory.

First, create a template.
Make sure to not change `custom_mail_subject` and `custom_mail_html` names, as they are used by the module to render the mail.
Execute the following commands:
```
runagent -m metrics1

cat > templates.d/mail.tmpl <<EOF
{{ define "custom_mail_subject" }}Alert on {{ range .Alerts.Firing }}{{ .Labels.instance }} {{ end }}{{ end }}
{{ define "custom_mail_html" }}
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
EOF

exit
```

Then, configure the module to use the new template:
```
api-cli run module/metrics1/configure-module --data '{"prometheus_path": "prometheus", "grafana_path": "grafana", "lets_encrypt": false, "mail_from": "no-reply@nethserver.org", "mail_to": ["alert@nethserver.org"], "mail_template": "custom_mail_html"}'
```

You can test the template rendering using the following command:
```
runagent -m metrics1
podman exec -ti alertmanager amtool template render --template.glob='/etc/alertmanager/templates/*.tmpl' --template.text='{{ template "custom_mail_html" . }}'
podman exec -ti alertmanager amtool template render --template.glob='/etc/alertmanager/templates/*.tmpl' --template.text='{{ template "custom_mail_subject" . }}'
```

## Testing

Test the module using the `test-module.sh` script:

    ./test-module.sh <NODE_ADDR> ghcr.io/nethserver/metrics:latest

The tests are made using [Robot Framework](https://robotframework.org/)
