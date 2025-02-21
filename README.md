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
- `prometheus_path`: path to access prometheus web UI
- `lets_encrypt`: boolean, if set to true traefik will request a valid Let's Encrypt certificate

Example:

    api-cli run module/grafana1/configure-module --data '{"prometheys_path": "lets_encrypt": false}'

## Service Discovery



## Testing

Test the module using the `test-module.sh` script:


    ./test-module.sh <NODE_ADDR> ghcr.io/nethserver/metrics:latest

The tests are made using [Robot Framework](https://robotframework.org/)
