#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml


RULES_DIR = Path("rules.d")
GENERATED_RULE_PREFIX = "provision_"
SAFE_RULE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
RECOMMENDED_SEVERITIES = {"warning", "critical"}
REQUIRED_ANNOTATIONS = {
    "summary_en",
    "summary_it",
    "description_en",
    "description_it",
}


class RuleValidationError(ValueError):
    """An alert rule cannot be safely installed."""


class PrometheusAPIError(RuntimeError):
    """The advisory Prometheus API check cannot be completed."""


@dataclass(frozen=True)
class AlertRuleSource:
    module_id: str
    name: str
    payload: str
    redis_key: str
    payload_error: str = ""

    @property
    def filename(self):
        return generated_rule_filename(self.module_id, self.name)

    @property
    def is_module_rule(self):
        return self.redis_key.endswith("/metrics_alert_rules")


def _as_text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _decode_alert_rule_source(module_id, raw_name, raw_payload, redis_key):
    name = _as_text(raw_name)
    try:
        payload = _as_text(raw_payload)
    except (UnicodeError, ValueError) as ex:
        return AlertRuleSource(
            module_id,
            name,
            "",
            redis_key,
            f"payload is not valid UTF-8: {ex}",
        )
    return AlertRuleSource(module_id, name, payload, redis_key)


def generated_rule_filename(module_id, name):
    """Return a generated file name, rejecting unsafe Redis components."""
    for label, value in (("module id", module_id), ("rule name", name)):
        if not value or value in {".", ".."} or not SAFE_RULE_NAME.fullmatch(value):
            raise RuleValidationError(
                f"invalid {label} {value!r}; use only letters, numbers, '.', '_', and '-'"
            )

    filename = f"{GENERATED_RULE_PREFIX}{module_id}_{name}.yml"
    if len(os.fsencode(filename)) > 255:
        raise RuleValidationError("generated rule file name is too long")
    return filename


def _source_sort_key(source):
    # Prefer the supported cross-module contract if a metrics-local custom
    # field would otherwise map to the same generated file.
    is_custom = source.redis_key.endswith("/custom_alerts")
    return is_custom, source.redis_key, source.name


def discover_alert_rule_sources(redis_client, report_errors=True):
    """Read local and module-provided rules and resolve output collisions."""
    raw_sources = []

    module_keys = []
    for raw_key in redis_client.scan_iter("module/*/metrics_alert_rules"):
        try:
            module_keys.append(_as_text(raw_key))
        except (UnicodeError, ValueError) as ex:
            if report_errors:
                print(f"Skipped malformed alert-rule Redis key: {ex}", file=sys.stderr)
    module_keys.sort()
    for redis_key in module_keys:
        parts = redis_key.split('/')
        if len(parts) != 3 or parts[0] != "module" or parts[2] != "metrics_alert_rules":
            if report_errors:
                print(f"Skipped malformed alert-rule Redis key {redis_key!r}", file=sys.stderr)
            continue
        module_id = parts[1]
        values = redis_client.hgetall(redis_key) or {}
        for raw_name, raw_payload in values.items():
            try:
                source = _decode_alert_rule_source(
                    module_id, raw_name, raw_payload, redis_key
                )
            except (UnicodeError, ValueError) as ex:
                if report_errors:
                    print(f"Skipped alert rule for module {module_id}: {ex}", file=sys.stderr)
                continue
            raw_sources.append(source)

    module_id = os.environ["MODULE_ID"]
    custom_key = f"module/{module_id}/custom_alerts"
    for raw_name, raw_payload in (redis_client.hgetall(custom_key) or {}).items():
        try:
            source = _decode_alert_rule_source(
                module_id, raw_name, raw_payload, custom_key
            )
        except (UnicodeError, ValueError) as ex:
            if report_errors:
                print(f"Skipped custom alert rule for module {module_id}: {ex}", file=sys.stderr)
            continue
        raw_sources.append(source)

    sources = []
    output_owners = {}
    for source in sorted(raw_sources, key=_source_sort_key):
        try:
            filename = source.filename
        except RuleValidationError as ex:
            if report_errors:
                print(
                    f"Skipped alert rule {source.name!r} for module "
                    f"{source.module_id}: {ex}",
                    file=sys.stderr,
                )
            continue

        previous = output_owners.get(filename)
        if previous is not None:
            if report_errors:
                print(
                    f"Skipped alert rule {source.name!r} for module "
                    f"{source.module_id}: generated file {filename} conflicts with "
                    f"{previous.redis_key} field {previous.name!r}",
                    file=sys.stderr,
                )
            continue
        output_owners[filename] = source
        sources.append(source)

    return sources


def _validate_rule_schema(document):
    groups = document.get("groups")
    if not isinstance(groups, list):
        raise RuleValidationError("'groups' must be a list")

    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise RuleValidationError(f"group {group_index} must be a mapping")
        if not isinstance(group.get("name"), str) or not group["name"].strip():
            raise RuleValidationError(f"group {group_index} must have a non-empty name")
        if not isinstance(group.get("rules"), list):
            raise RuleValidationError(
                f"group {group.get('name', group_index)!r} must have a rules list"
            )
        if "labels" in group and not isinstance(group["labels"], dict):
            raise RuleValidationError(
                f"group {group['name']!r} labels must be a mapping"
            )

        for rule_index, rule in enumerate(group["rules"]):
            location = f"group {group['name']!r}, rule {rule_index}"
            if not isinstance(rule, dict):
                raise RuleValidationError(f"{location} must be a mapping")
            if "record" in rule:
                raise RuleValidationError(f"{location} is a recording rule; only alerts are supported")
            if not isinstance(rule.get("alert"), str) or not rule["alert"].strip():
                raise RuleValidationError(f"{location} must have a non-empty 'alert' field")
            if not isinstance(rule.get("expr"), str) or not rule["expr"].strip():
                raise RuleValidationError(f"{location} must have a non-empty 'expr' field")
            if "labels" in rule and not isinstance(rule["labels"], dict):
                raise RuleValidationError(f"{location} labels must be a mapping")
            if "annotations" in rule and not isinstance(rule["annotations"], dict):
                raise RuleValidationError(f"{location} annotations must be a mapping")


def _warn_module_id_override(source, location, previous):
    print(
        f"Warning: {location} from {source.redis_key} field {source.name!r} "
        f"has module_id {previous!r}; using authoritative value "
        f"{source.module_id!r}",
        file=sys.stderr,
    )


def _scope_module_rule(document, source):
    for group in document["groups"]:
        group_labels = group.get("labels")
        if group_labels is not None:
            previous = group_labels.get("module_id")
            if previous is not None and previous != source.module_id:
                _warn_module_id_override(
                    source, f"group {group['name']!r}", previous
                )
            group_labels["module_id"] = source.module_id

        for rule in group["rules"]:
            rule["expr"] = scope_promql_expression(
                rule["expr"], source.module_id, source
            )
            labels = rule.setdefault("labels", {})
            previous = labels.get("module_id")
            if previous is not None and previous != source.module_id:
                _warn_module_id_override(
                    source, f"alert {rule['alert']!r}", previous
                )
            labels["module_id"] = source.module_id


def normalize_alert_rule(payload, module_id, name, scoped=False, redis_key=None):
    try:
        data = yaml.safe_load(payload)
    # PyYAML can propagate built-in exceptions for malformed implicit values
    # and excessively nested input. Contain every parser failure here so one
    # publisher cannot abort the complete provisioning run.
    except Exception as ex:
        raise RuleValidationError(f"invalid YAML: {ex}") from ex

    if not isinstance(data, dict):
        raise RuleValidationError("payload must be a mapping")

    single_rule = "groups" not in data
    if not single_rule:
        document = data
    elif "record" in data:
        raise RuleValidationError("recording rules are not supported")
    elif "alert" in data or "expr" in data:
        document = {
            "groups": [
                {
                    "name": (
                        f"ns8:{module_id}:{name}"
                        if scoped
                        else f"{module_id}.{name}"
                    ),
                    "rules": [data],
                }
            ]
        }
    else:
        raise RuleValidationError(
            "payload must be a Prometheus groups document or a single alert rule"
        )

    _validate_rule_schema(document)
    if not single_rule:
        local_names = set()
        for group in document["groups"]:
            local_name = group["name"].strip()
            if local_name.startswith("ns8:"):
                raise RuleValidationError(
                    f"group name {local_name!r} uses the reserved 'ns8:' prefix"
                )
            if local_name in local_names:
                raise RuleValidationError(
                    f"duplicate local group name {local_name!r}"
                )
            local_names.add(local_name)

    source = AlertRuleSource(
        module_id,
        name,
        payload,
        redis_key or f"module/{module_id}/metrics_alert_rules",
    )
    if scoped:
        if not single_rule:
            for group in document["groups"]:
                local_name = group["name"].strip()
                group["name"] = f"ns8:{module_id}:{name}:{local_name}"
        _scope_module_rule(document, source)
    return document


def _iter_alert_rules(document):
    for group in document.get("groups", []):
        if not isinstance(group, dict):
            continue
        for rule in group.get("rules", []):
            if isinstance(rule, dict) and "alert" in rule:
                yield rule


def warn_incomplete_rules(document, source):
    for rule in _iter_alert_rules(document):
        alert_name = rule["alert"]
        labels = rule.get("labels", {})
        severity = labels.get("severity")
        if not isinstance(severity, str) or severity not in RECOMMENDED_SEVERITIES:
            if severity is None:
                detail = "has no severity label"
            else:
                detail = f"has unsupported severity {severity!r}"
            print(
                f"Warning: alert {alert_name!r} from module {source.module_id}, "
                f"rule {source.name!r}, {detail}; recommended values are "
                "'warning' and 'critical'",
                file=sys.stderr,
            )

        annotations = rule.get("annotations", {})
        missing = sorted(REQUIRED_ANNOTATIONS.difference(annotations))
        if missing:
            print(
                f"Warning: alert {alert_name!r} from module {source.module_id}, "
                f"rule {source.name!r}, is missing bilingual annotations: "
                f"{', '.join(missing)}",
                file=sys.stderr,
            )


def _run_promtool_command(arguments, candidate_directory=None):
    image = os.environ.get("PROMETHEUS_IMAGE")
    if not image:
        raise RuleValidationError("PROMETHEUS_IMAGE is not set")

    command = [
        "/usr/bin/podman",
        "run",
        "--rm",
        "--network=none",
    ]
    if candidate_directory is not None:
        command.append(
            f"--volume={Path(candidate_directory).resolve()}:/rules:ro,z"
        )
    command.extend(["--entrypoint=/bin/promtool", image, *arguments])
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as ex:
        raise RuleValidationError(f"cannot execute promtool: {ex}") from ex

    if result.returncode != 0:
        output = result.stdout.strip() or f"promtool exited with status {result.returncode}"
        raise RuleValidationError(output)
    return result.stdout.strip()


def _run_promtool(candidate_directory, candidate_name):
    _run_promtool_command(
        ["check", "rules", f"/rules/{candidate_name}"],
        candidate_directory,
    )


def scope_promql_expression(expression, module_id, source=None):
    """Force the publishing module ID onto every PromQL selector."""
    formatted = _run_promtool_command(
        ["--experimental", "promql", "format", expression]
    )
    without_module_id = _run_promtool_command(
        [
            "--experimental",
            "promql",
            "label-matchers",
            "delete",
            formatted,
            "module_id",
        ]
    )
    scoped = _run_promtool_command(
        [
            "--experimental",
            "promql",
            "label-matchers",
            "set",
            without_module_id,
            "module_id",
            module_id,
        ]
    )
    if scoped == without_module_id:
        raise RuleValidationError(
            "expression has no vector or range selector to scope by module_id"
        )
    if source is not None and without_module_id != formatted and scoped != formatted:
        print(
            f"Warning: expression from {source.redis_key} field "
            f"{source.name!r} supplied a broader or conflicting module_id "
            f"matcher; using authoritative value {module_id!r}",
            file=sys.stderr,
        )
    return scoped


def _prepare_alert_rules(sources):
    prepared = {}
    errors = {}
    group_owners = {}

    for source in sources:
        filename = source.filename
        try:
            if source.payload_error:
                raise RuleValidationError(source.payload_error)
            document = normalize_alert_rule(
                source.payload,
                source.module_id,
                source.name,
                scoped=source.is_module_rule,
                redis_key=source.redis_key,
            )
            warn_incomplete_rules(document, source)
        except Exception as ex:
            errors[filename] = ex
            continue

        prepared[filename] = document
        for group in document["groups"]:
            group_name = group["name"]
            previous = group_owners.get(group_name)
            if previous is None:
                group_owners[group_name] = source
                continue

            error = RuleValidationError(
                f"effective group name {group_name!r} conflicts with "
                f"{previous.redis_key} field {previous.name!r}"
            )
            errors[filename] = error
            errors[previous.filename] = error

    return prepared, errors


def _install_alert_rule(source, document, preparation_error=None):
    target = RULES_DIR / source.filename
    retained = target.is_file()

    try:
        if preparation_error is not None:
            raise preparation_error

        with tempfile.TemporaryDirectory(prefix=".promtool-", dir=".") as candidate_dir:
            os.chmod(candidate_dir, 0o755)
            candidate_name = source.filename
            candidate = Path(candidate_dir) / candidate_name
            with candidate.open("w", encoding="utf-8") as fp:
                yaml.safe_dump(document, fp, sort_keys=False, allow_unicode=True)
            os.chmod(candidate, 0o644)
            _run_promtool(candidate_dir, candidate_name)
            os.replace(candidate, target)
    # Rule payloads are untrusted, and PyYAML can surface built-in exceptions
    # from both parsing and serialization. Keep every source failure inside
    # this per-rule boundary so provisioning can continue with other rules.
    except Exception as ex:
        disposition = "previous valid file retained" if retained else "no rule file installed"
        print(
            f"Skipped alert rule {source.name!r} for module {source.module_id}: "
            f"{ex}; {disposition}",
            file=sys.stderr,
        )
        return False

    return True


def _clean_up_generated_alert_rules(desired_filenames):
    for path in RULES_DIR.glob(f"{GENERATED_RULE_PREFIX}*.yml"):
        if path.is_file() and path.name not in desired_filenames:
            path.unlink()

    # Migrate the old aggregate custom-alert file to per-entry generated files.
    old_custom_file = RULES_DIR / "custom.yml"
    if old_custom_file.is_file():
        old_custom_file.unlink()


def warn_duplicate_alert_identities():
    occurrences = {}
    for path in sorted(RULES_DIR.glob("*.yml")):
        try:
            with path.open(encoding="utf-8") as fp:
                document = yaml.safe_load(fp)
        except (OSError, yaml.YAMLError) as ex:
            print(f"Warning: cannot inspect alert names in {path}: {ex}", file=sys.stderr)
            continue
        if not isinstance(document, dict):
            continue
        for rule in _iter_alert_rules(document):
            name = rule.get("alert")
            if isinstance(name, str):
                labels = rule.get("labels", {})
                module_id = labels.get("module_id", "")
                identity = (name, str(module_id) if module_id is not None else "")
                occurrences.setdefault(identity, []).append(path.name)

    for (alert_name, module_id), paths in sorted(occurrences.items()):
        if len(paths) > 1:
            module_detail = f", module_id {module_id!r}" if module_id else ""
            print(
                f"Warning: duplicate alert identity ({alert_name!r}"
                f"{module_detail}) in "
                f"{', '.join(paths)}; module alerts should use a unique "
                "module/application prefix",
                file=sys.stderr,
            )


def provision(redis_client):
    sources = discover_alert_rule_sources(redis_client)
    desired_filenames = {source.filename for source in sources}
    prepared, errors = _prepare_alert_rules(sources)
    for source in sources:
        _install_alert_rule(
            source,
            prepared.get(source.filename),
            errors.get(source.filename),
        )
    _clean_up_generated_alert_rules(desired_filenames)
    warn_duplicate_alert_identities()


def _prometheus_api_base():
    path = os.environ.get("PROMETHEUS_PATH", "").strip("/")
    if path:
        path = "/" + urllib.parse.quote(path, safe="/")
    return f"http://127.0.0.1:9091{path}"


def _prometheus_api_get(endpoint, parameters=None):
    url = _prometheus_api_base() + endpoint
    if parameters:
        url += "?" + urllib.parse.urlencode(parameters)
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError) as ex:
        raise PrometheusAPIError(str(ex)) from ex

    if not isinstance(payload, dict) or payload.get("status") != "success":
        error = payload.get("error", "unexpected API response") if isinstance(payload, dict) else "unexpected API response"
        raise PrometheusAPIError(str(error))
    return payload.get("data")


def _call_name(node):
    function = node.get("func")
    if isinstance(function, dict):
        return function.get("name")
    if isinstance(function, str):
        return function
    return None


def collect_static_metric_names(ast):
    """Collect concrete vector selector names from a PromQL parser AST."""
    names = set()

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return

        node_type = str(node.get("type", "")).lower()
        if node_type == "call" and _call_name(node) in {"absent", "absent_over_time"}:
            return
        if node_type in {"vectorselector", "matrixselector"}:
            metric_name = node.get("name")
            if not isinstance(metric_name, str) or not metric_name:
                # A selector such as `{__name__="node_cpu_seconds_total"}`
                # has no `name` in Prometheus's parser AST. It is still
                # static when one exact __name__ value can be derived.
                exact_names = {
                    matcher.get("value")
                    for matcher in node.get("matchers", [])
                    if isinstance(matcher, dict)
                    and matcher.get("name") == "__name__"
                    and matcher.get("type") == "="
                    and isinstance(matcher.get("value"), str)
                    and matcher.get("value")
                }
                if len(exact_names) != 1:
                    return
                metric_name = exact_names.pop()
            names.add(metric_name)
            return

        for value in node.values():
            visit(value)

    visit(ast)
    return names


def _active_generated_rules(redis_client):
    for source in discover_alert_rule_sources(redis_client, report_errors=False):
        path = RULES_DIR / source.filename
        if not path.is_file():
            continue
        try:
            with path.open(encoding="utf-8") as fp:
                document = yaml.safe_load(fp)
        except (OSError, yaml.YAMLError) as ex:
            print(
                f"Deferred metric check for module {source.module_id}, rule "
                f"{source.name!r}: cannot read installed rule file: {ex}",
                file=sys.stderr,
            )
            continue
        if not isinstance(document, dict):
            continue
        for rule in _iter_alert_rules(document):
            alert_name = rule.get("alert")
            expression = rule.get("expr")
            if isinstance(alert_name, str) and isinstance(expression, str):
                yield source, alert_name, expression


def check_metric_references(redis_client):
    """Warn about unknown metrics, while failing open on every API error."""
    references = set()
    for source, alert_name, expression in _active_generated_rules(redis_client):
        try:
            ast = _prometheus_api_get("/api/v1/parse_query", {"query": expression})
        except PrometheusAPIError as ex:
            print(
                "Deferred metric reference check: Prometheus query parser is "
                f"unavailable: {ex}",
                file=sys.stderr,
            )
            return
        for metric_name in collect_static_metric_names(ast):
            references.add((source.module_id, source.name, alert_name, metric_name))

    if not references:
        print("Skipped metric reference check: no static metric names found", file=sys.stderr)
        return

    try:
        known_data = _prometheus_api_get("/api/v1/label/__name__/values")
    except PrometheusAPIError as ex:
        print(
            f"Deferred metric reference check: metric names are unavailable: {ex}",
            file=sys.stderr,
        )
        return

    if not isinstance(known_data, list) or not known_data:
        print(
            "Deferred metric reference check: Prometheus has no known metric names yet",
            file=sys.stderr,
        )
        return
    known_metrics = {name for name in known_data if isinstance(name, str)}

    for module_id, rule_name, alert_name, metric_name in sorted(references):
        if metric_name not in known_metrics:
            print(
                f"Warning: module {module_id}, rule {rule_name!r}, alert "
                f"{alert_name!r} references unknown metric {metric_name!r}",
                file=sys.stderr,
            )
