# alert-proxy

`alert-proxy` is a Python-based web service designed to handle alert notifications. 
It receives alerts from alertermanagervia HTTP POST requests, processes them, and sends them to a my.nethesis.it or my.nethserver.com.
It listens on port `9095`.

Requirements
- Python 3.6+
- `aiohttp` library

## Configuration

The service relies on several environment variables for its configuration:
- `NODE_ID`: Identifier for the node.
- `NMON_ALERT_AUTH_TOKEN`: Authentication token for sending alerts.
- `NMON_ALERT_PROVIDER`: Alert provider (`nsent` or `nscom`).
- `NMON_ALERT_SYSTEM_ID`: System identifier for the alerts.
- `NMON_DARTAGNAN_URL`: URL for the Dartagnan service (used if `NMON_ALERT_PROVIDER` is `nscom`).

## Endpoints

- `GET /`: Health check endpoint. Returns "OK" if the service is running.
- `POST /`: Endpoint to receive alerts. Expects a JSON payload with alerts.
  When a POST request is received:
    - it assumes it's an alertmanager alert in JSON format
    - it parses the alert to Nethesis internal format
    - if the machine has a valid subscription, it sends the alert to my.nethesis.it or my.nethserver.com

## Alert format

Raise an alert for /boot disk full:
```
echo '{"receiver": "default-receiver", "status": "firing", "alerts": [{"status": "firing", "labels": {"alertname": "disk_full", "device": "/dev/vda2", "fstype": "xfs", "instance": "10.5.4.1:9100", "job": "providers", "mountpoint": "/boot", "node": "1", "severity": "warning"}, "annotations": {"description": "Disk is almost full (< 10% left)\n  VALUE = 0.009680310021459396\n  LABELS = map[device:/dev/vda2 fstype:xfs instance:10.5.4.1:9100 job:providers mountpoint:/boot node:1]", "summary": "Host out of disk space (instance 10.5.4.1:9100)"}, "startsAt": "2025-02-25T10:55:26.18Z", "endsAt": "0001-01-01T00:00:00Z", "generatorURL": "/prometheus/graph?g0.expr=%28node_filesystem_avail_bytes%7Bfstype%21~%22%5E%28fuse.%2A%7Ctmpfs%7Ccifs%7Cnfs%29%22%7D+%2F+node_filesystem_size_bytes+%3C+0.1+and+on+%28instance%2C+device%2C+mountpoint%29+node_filesystem_readonly+%3D%3D+0%29&g0.tab=1", "fingerprint": "355cfca350bfe294"}], "groupLabels": {"alertname": "disk_full", "node": "1"}, "commonLabels": {"alertname": "disk_full", "device": "/dev/vda2", "fstype": "xfs", "instance": "10.5.4.1:9100", "job": "providers", "mountpoint": "/boot", "node": "1", "severity": "warning"}, "commonAnnotations": {"description": "Disk is almost full (< 10% left)\n  VALUE = 0.009680310021459396\n  LABELS = map[device:/dev/vda2 fstype:xfs instance:10.5.4.1:9100 job:providers mountpoint:/boot node:1]", "summary": "Host out of disk space (instance 10.5.4.1:9100)"}, "externalURL": "http://rl1.leader.cluster0.gs.nethserver.net:9093", "version": "4", "groupKey": "{}/{service=~\".*\"}:{alertname=\"disk_full\", node=\"1\"}", "truncatedAlerts": 0}' | curl -H "Content-Type: application/json"  --data-binary @- http://localhost:9095
```

Raise an alert for SWAP full:
```
echo '{"receiver": "default-receiver", "status": "firing", "alerts": [{"status": "firing", "labels": {"alertname": "swap_full", "instance": "10.5.4.1:9100", "job": "providers", "node": "1", "severity": "warning"}, "annotations": {"description": "Swap is filling up (>80%)\n  VALUE = 93.35677999218444\n  LABELS = map[instance:10.5.4.1:9100 job:providers node:1]", "summary": "Host swap is filling up (instance 10.5.4.1:9100)"}, "startsAt": "2025-02-25T11:38:23.728Z", "endsAt": "0001-01-01T00:00:00Z", "generatorURL": "/prometheus/graph?g0.expr=%28%281+-+%28node_memory_SwapFree_bytes+%2F+node_memory_SwapTotal_bytes%29%29+%2A+100+%3E+80%29&g0.tab=1", "fingerprint": "1023b13cc95d8994"}], "groupLabels": {"alertname": "swap_full", "node": "1"}, "commonLabels": {"alertname": "swap_full", "instance": "10.5.4.1:9100", "job": "providers", "node": "1", "severity": "warning"}, "commonAnnotations": {"description": "Swap is filling up (>80%)\n  VALUE = 93.35677999218444\n  LABELS = map[instance:10.5.4.1:9100 job:providers node:1]", "summary": "Host swap is filling up (instance 10.5.4.1:9100)"}, "externalURL": "http://rl1.leader.cluster0.gs.nethserver.net:9093", "version": "4", "groupKey": "{}/{service=~\".*\"}:{alertname=\"swap_full\", node=\"1\"}", "truncatedAlerts": 0}' | curl -H "Content-Type: application/json"  --data-binary @- http://localhost:9095
```
