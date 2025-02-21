# ns8-metrics

This module implements the metrics engine for NethServer 8.
The module is rootless and runs as a non-privileged user.

It is composed by the following services:

- [Prometheus](https://prometheus.io/)
- [Alertmanager](https://prometheus.io/docs/alerting/alertmanager/)

Behavior:

- there is only one instance of the module per node
- the only active instance is running on the leader node
- it automatically monitors all cluster nodes
- if a leader node becomes a worker, the module is automatically stopped on the worker node (TODO)
- Prometheus listens on well-known port 9091 (standard port is 9090, but it has been changed to avoid conflicts with Cockpit)
- Alertmanager listens on well-known port 9093


## Install

The module is automatically installed by the cluster initialization script.

## Configure

Launch `configure-module`, by setting the following parameters:
- `prometheus_path`: path to access prometheus web UI, if left blank prometheus will be not exposed
- `grafana_path`: path to access grafana web UI, if left blank grafana will be stopped; if enabled default credentials are `admin`/`admin`
- `lets_encrypt`: boolean, if set to true traefik will request a valid Let's Encrypt certificate
- `alert_mail`: email address to receive alerts

Example:

    api-cli run module/metrics1/configure-module --data '{"prometheus_path": "prometheus", "grafana_path": "grafana", "lets_encrypt": false, "alert_mail": "alert@nethserver.org"}'

## Testing

Test the module using the `test-module.sh` script:


    ./test-module.sh <NODE_ADDR> ghcr.io/nethserver/metrics:latest

The tests are made using [Robot Framework](https://robotframework.org/)
