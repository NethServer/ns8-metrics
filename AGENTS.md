# Agent instructions

## Build and test commands

- Build the module image with `./build-images.sh`. The script assembles the `metrics` image from `imageroot/` and `ui/`. Override the destination with `REPOBASE=...` and the tag with `IMAGETAG=...`.
- Run the full integration suite with `./test-module.sh <LEADER_NODE> <IMAGE_URL>`. This runs Robot Framework tests against a live NS8 node inside the disposable `rftest` container.
- Run a single Robot suite with `./test-module.sh <LEADER_NODE> <IMAGE_URL> tests/10__check_services.robot`.
- Run a single Robot test case with `./test-module.sh <LEADER_NODE> <IMAGE_URL> --test "Check if grafana is running"`.
- There is no repository-defined lint command.

## High-level architecture

- This repository builds the NS8 module image (`build-images.sh`). It bundles `imageroot/` plus the static `ui/` assets and declares Prometheus, Alertmanager, and Grafana as dependent images.
- The module is cluster-scoped: only one instance should exist, and it runs on the leader node while scraping all cluster nodes.
- Runtime behavior is split by responsibility:
  - `imageroot/actions/`: NS8 agent actions such as `create-module`, `configure-module`, `get-configuration`, and `restore-configuration`
  - `imageroot/bin/`: provisioning helpers invoked by actions, events, and systemd units
  - `imageroot/events/`: handlers that react to cluster changes (`metrics-target-changed`, `metrics-datasource-changed`, `subscription-changed`, `smarthost-changed`, `vpn-changed`)
  - `imageroot/systemd/user/*.service`: rootless Podman services for Prometheus, Alertmanager, and Grafana
- Prometheus startup always runs `provision-prometheus`. That script rewrites `prometheus.yml`, node scrape targets under `prometheus.d/`, built-in and custom alert rules under `rules.d/`, `alertmanager.yml`, and the alert templates file under `templates.d/` from Redis-backed state.
- Grafana startup always runs `provision-grafana`. That script rewrites local datasources, wires Loki and Alertmanager, copies bundled dashboards from `imageroot/etc/dashboards`, and materializes module-provided datasources/dashboards from Redis into `datasources/` and `dashboards/modules/`.
- `configure-module` is the control plane entrypoint for exposed paths and mail settings. It persists `module/<MODULE_ID>/settings`, updates Traefik routes for Prometheus and Grafana, and then `actions/configure-module/80services` restarts or disables services as needed. Grafana only runs when `grafana_path` is configured.
- Alert flow: Prometheus sends every alert to the local Alertmanager (`127.0.0.1:9093`) and, for `nsent` subscriptions, to the my Mimir alertmanager derived from `collect_url` in `cluster/subscription`. `provision-prometheus` writes both targets; `subscription-changed` restarts Prometheus.

## Key conventions

- Treat the module state directory as generated output. Files such as `prometheus.yml`, `alertmanager.yml`, `prometheus.d/*`, `rules.d/*`, `datasources/*`, `dashboards/*`, and `templates.d/*` are regenerated from Redis-backed state and service startup hooks; do not rely on manual edits surviving reprovisioning.
- Cross-module integration happens through Redis hashes, not checked-in config files. Other modules publish scrape targets, Grafana datasources, and dashboards under `module/<module_id>/metrics_targets`, `module/<module_id>/metrics_datasources`, and `module/<module_id>/metrics_dashboards`; this module rebuilds `provision_*` files from those keys.
- Built-in alert rules are generated programmatically in `imageroot/actions/create-module/30alerts`, not stored as static YAML. When changing built-in alerts, preserve the established bilingual annotation pattern (`summary_en`, `summary_it`, `description_en`, `description_it`).
- External access for Prometheus and Grafana is expected to go through Traefik with forward auth. Grafana provisioning enforces `disable_login_form=true` and `auth.basic.enabled=false`, so authentication changes belong in the NS8 action/proxy flow rather than Grafana UI settings.
- Service units run rootless Podman containers on host networking but bind the application endpoints to localhost (`127.0.0.1`). Public exposure is added separately through Traefik routes configured by the module actions.
- Robot tests assume the default module instance is `metrics1`, exercise the NS8 APIs over SSH on a real node, and use the `SCENARIO` variable to distinguish install vs update coverage.

Also:

- The configuration UI for this NS8 module is implemented in NethServer/ns8-core repository on GitHub
- **Branch names**: never use "/" in branch names. Use only chars allowed by container registry tags, like "-" and alphanumeric chars. This is a requirement for container image uploads.
- **Commits**: Use conventional commit style. Short title line, 50 chars max. Briefly explain commit rationale in one/two paragraphs, wrap body text at column 72.
