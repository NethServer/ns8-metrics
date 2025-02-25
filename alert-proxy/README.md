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