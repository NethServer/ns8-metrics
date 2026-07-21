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
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

import yaml


SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]+$")
RECOMMENDED_SEVERITIES = {"warning", "critical"}
REQUIRED_ANNOTATIONS = {
    "summary_en",
    "summary_it",
    "description_en",
    "description_it",
}
PLACEHOLDER_LABEL_BASE = "__ns8_rule_scope"
PLACEHOLDER_VALUE = "1"
OWNERSHIP_PREFIX = "# ns8-metrics-source: "
GENERATED_RULE_PREFIX = "provision_"
GENERATED_RULE_SUFFIX = ".yml"


class RuleValidationError(ValueError):
    """Authored rule content cannot be transformed safely."""


class RuleInfrastructureError(RuntimeError):
    """Rule transformation infrastructure is unavailable or failed."""


@dataclass(frozen=True)
class AlertRuleSource:
    publisher_id: str
    rule_set_name: str
    payload: object
    redis_key: str

    @property
    def context(self):
        return f"{self.redis_key} field {self.rule_set_name!r}"

    @property
    def filename(self):
        return generated_rule_filename(self.publisher_id, self.rule_set_name)


class PromtoolRunner:
    """Run promtool from the declared Prometheus image without networking."""

    def __init__(self, image=None, executor=subprocess.run):
        self.image = image or os.environ.get("PROMETHEUS_IMAGE")
        if not self.image:
            raise RuleInfrastructureError("PROMETHEUS_IMAGE is not set")
        self.executor = executor

    def __call__(self, arguments):
        command = [
            "/usr/bin/podman",
            "run",
            "--rm",
            "--network=none",
            "--entrypoint=/bin/promtool",
            self.image,
            *arguments,
        ]
        return self._execute(command)

    def check_rules(self, candidate_path):
        candidate_path = os.path.abspath(os.fspath(candidate_path))
        command = [
            "/usr/bin/podman",
            "run",
            "--rm",
            "--network=none",
            "--volume",
            f"{candidate_path}:/tmp/ns8-module-rules.yml:ro,z",
            "--entrypoint=/bin/promtool",
            self.image,
            "check",
            "rules",
            "/tmp/ns8-module-rules.yml",
        ]
        return self._execute(command)

    def _execute(self, command):
        try:
            result = self.executor(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                shell=False,
            )
        except OSError as ex:
            raise RuleInfrastructureError(
                f"cannot execute Prometheus tooling: {ex}"
            ) from ex

        output = result.stdout.strip()
        if (
            result.returncode in {125, 126, 127}
            or result.returncode < 0
            or result.returncode >= 128
            or _is_podman_runtime_error(output)
        ):
            detail = output or f"podman exited with status {result.returncode}"
            raise RuleInfrastructureError(detail)
        if result.returncode != 0:
            detail = output or f"promtool exited with status {result.returncode}"
            raise RuleValidationError(detail)
        return output


def _is_podman_runtime_error(output):
    runtime_prefixes = (
        "Error:",
        "Failed to obtain podman configuration:",
        "cannot clone:",
    )
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(runtime_prefixes):
            return True
        if stripped.startswith("time=") and " level=error " in stripped:
            return True
    return False


def generated_rule_filename(publisher_id, rule_set_name):
    for label, value in (
        ("publisher ID", publisher_id),
        ("rule-set name", rule_set_name),
    ):
        if (
            not isinstance(value, str)
            or not value
            or value in {".", ".."}
            or not SAFE_IDENTIFIER.fullmatch(value)
        ):
            raise RuleValidationError(
                f"invalid {label} {value!r}; use only ASCII letters, digits, "
                "'.', '_', and '-'"
            )

    filename = f"provision_{publisher_id}_{rule_set_name}.yml"
    if len(os.fsencode(filename)) > 255:
        raise RuleValidationError("generated rule filename exceeds 255 bytes")
    return filename


def _stderr_warning(message):
    print(message, file=sys.stderr)


def _decode_payload(payload):
    if isinstance(payload, bytes):
        try:
            return payload.decode("utf-8")
        except UnicodeError as ex:
            raise RuleValidationError(f"payload is not valid UTF-8: {ex}") from ex
    if not isinstance(payload, str):
        raise RuleValidationError("payload must be UTF-8 text")
    return payload


def _parse_payload(payload):
    try:
        document = yaml.safe_load(_decode_payload(payload))
    except RuleValidationError:
        raise
    except Exception as ex:
        raise RuleValidationError(f"invalid YAML: {ex}") from ex
    if not isinstance(document, dict):
        raise RuleValidationError("payload must decode to a mapping")
    return document


def _normalize_document(document, source):
    if "groups" in document:
        return document, False
    if "record" in document:
        raise RuleValidationError("recording rules are not supported")
    if "alert" not in document and "expr" not in document:
        raise RuleValidationError(
            "payload must be a groups document or a single alert rule"
        )

    return {
        "groups": [
            {
                "name": f"ns8:{source.publisher_id}:{source.rule_set_name}",
                "rules": [document],
            }
        ]
    }, True


def _validate_schema(document, validate_local_group_names=True):
    groups = document.get("groups")
    if not isinstance(groups, list):
        raise RuleValidationError("'groups' must be a list")

    local_names = set()
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise RuleValidationError(f"group {group_index} must be a mapping")

        name = group.get("name")
        if not isinstance(name, str) or not name.strip():
            raise RuleValidationError(
                f"group {group_index} must have a non-empty string name"
            )
        if validate_local_group_names:
            local_name = name.strip()
            if local_name.startswith("ns8:"):
                raise RuleValidationError(
                    f"group name {local_name!r} uses the reserved 'ns8:' prefix"
                )
            if local_name in local_names:
                raise RuleValidationError(
                    f"duplicate local group name {local_name!r}"
                )
            local_names.add(local_name)

        rules = group.get("rules")
        if not isinstance(rules, list):
            raise RuleValidationError(
                f"group {name!r} must have a rules list"
            )
        if "labels" in group and not isinstance(group["labels"], dict):
            raise RuleValidationError(f"group {name!r} labels must be a mapping")

        for rule_index, rule in enumerate(rules):
            location = f"group {name!r}, rule {rule_index}"
            if not isinstance(rule, dict):
                raise RuleValidationError(f"{location} must be a mapping")
            if "record" in rule:
                raise RuleValidationError(
                    f"{location} is a recording rule; only alerts are supported"
                )
            if not isinstance(rule.get("alert"), str) or not rule["alert"].strip():
                raise RuleValidationError(
                    f"{location} must have a non-empty string 'alert' field"
                )
            if not isinstance(rule.get("expr"), str) or not rule["expr"].strip():
                raise RuleValidationError(
                    f"{location} must have a non-empty string 'expr' field"
                )
            if "labels" in rule and not isinstance(rule["labels"], dict):
                raise RuleValidationError(f"{location} labels must be a mapping")
            if "annotations" in rule and not isinstance(
                rule["annotations"], dict
            ):
                raise RuleValidationError(
                    f"{location} annotations must be a mapping"
                )


def _rewrite_group_names(document, source, single_rule):
    if single_rule:
        return
    for group in document["groups"]:
        local_name = group["name"].strip()
        group["name"] = (
            f"ns8:{source.publisher_id}:{source.rule_set_name}:{local_name}"
        )


def _warn_matcher_override(source, warning):
    warning(
        f"Warning: expression from {source.context} supplied a broader, "
        "partial, negative, regular-expression, mixed, or conflicting "
        f"module_id matcher; using publisher ID {source.publisher_id!r}"
    )


def _select_placeholder_label(formatted, promtool_runner):
    suffix = 0
    while True:
        label_name = (
            PLACEHOLDER_LABEL_BASE
            if suffix == 0
            else f"{PLACEHOLDER_LABEL_BASE}_{suffix}"
        )
        without_candidate = promtool_runner(
            [
                "--experimental",
                "promql",
                "label-matchers",
                "delete",
                formatted,
                label_name,
            ]
        )
        if without_candidate == formatted:
            return label_name
        suffix += 1


def rewrite_promql_expression(expression, source, promtool_runner, warning=None):
    if warning is None:
        warning = _stderr_warning

    formatted = promtool_runner(
        ["--experimental", "promql", "format", expression]
    )
    placeholder_label = _select_placeholder_label(formatted, promtool_runner)
    with_placeholder = promtool_runner(
        [
            "--experimental",
            "promql",
            "label-matchers",
            "set",
            formatted,
            placeholder_label,
            PLACEHOLDER_VALUE,
        ]
    )
    if with_placeholder == formatted:
        raise RuleValidationError(
            "expression has no vector or range selector to scope by module_id"
        )

    without_module_id = promtool_runner(
        [
            "--experimental",
            "promql",
            "label-matchers",
            "delete",
            with_placeholder,
            "module_id",
        ]
    )
    scoped_with_placeholder = promtool_runner(
        [
            "--experimental",
            "promql",
            "label-matchers",
            "set",
            without_module_id,
            "module_id",
            source.publisher_id,
        ]
    )
    scoped = promtool_runner(
        [
            "--experimental",
            "promql",
            "label-matchers",
            "delete",
            scoped_with_placeholder,
            placeholder_label,
        ]
    )

    authored_module_matcher = without_module_id != with_placeholder
    if authored_module_matcher and scoped != formatted:
        _warn_matcher_override(source, warning)
    return scoped


def _warn_label_override(source, location, old_value, warning):
    warning(
        f"Warning: {location} from {source.context} has module_id "
        f"{old_value!r}; using publisher ID {source.publisher_id!r}"
    )


def _rewrite_module_identity(document, source, promtool_runner, warning):
    for group in document["groups"]:
        group_labels = group.get("labels")
        if group_labels is not None:
            if (
                "module_id" in group_labels
                and group_labels["module_id"] != source.publisher_id
            ):
                _warn_label_override(
                    source,
                    f"group {group['name']!r}",
                    group_labels["module_id"],
                    warning,
                )
            group_labels["module_id"] = source.publisher_id

        for rule in group["rules"]:
            rule["expr"] = rewrite_promql_expression(
                rule["expr"], source, promtool_runner, warning
            )
            labels = rule.setdefault("labels", {})
            if "module_id" in labels and labels["module_id"] != source.publisher_id:
                _warn_label_override(
                    source,
                    f"alert {rule['alert']!r}",
                    labels["module_id"],
                    warning,
                )
            labels["module_id"] = source.publisher_id


def _warn_recommended_metadata(document, source, warning):
    for group in document["groups"]:
        for rule in group["rules"]:
            alert_name = rule["alert"]
            labels = rule.get("labels", {})
            severity = labels.get("severity")
            if severity is None:
                warning(
                    f"Warning: alert {alert_name!r} from {source.context} has "
                    "no severity label; recommended values are 'warning' and "
                    "'critical'"
                )
            elif not isinstance(severity, str) or severity not in RECOMMENDED_SEVERITIES:
                warning(
                    f"Warning: alert {alert_name!r} from {source.context} has "
                    f"unsupported severity {severity!r}; recommended values "
                    "are 'warning' and 'critical'"
                )

            annotations = rule.get("annotations", {})
            missing = sorted(REQUIRED_ANNOTATIONS.difference(annotations))
            if missing:
                warning(
                    f"Warning: alert {alert_name!r} from {source.context} is "
                    "missing bilingual annotations: " + ", ".join(missing)
                )


def transform_alert_rule(source, promtool_runner=None, warning=None):
    """Validate and transform one authored alert-rule source."""
    if not isinstance(source, AlertRuleSource):
        raise TypeError("source must be an AlertRuleSource")
    if warning is None:
        warning = _stderr_warning

    try:
        generated_rule_filename(source.publisher_id, source.rule_set_name)
        document = _parse_payload(source.payload)
        document, single_rule = _normalize_document(document, source)
        _validate_schema(document, validate_local_group_names=not single_rule)
        _rewrite_group_names(document, source, single_rule)

        if promtool_runner is None:
            promtool_runner = PromtoolRunner()
        _rewrite_module_identity(document, source, promtool_runner, warning)
        _warn_recommended_metadata(document, source, warning)
        return document
    except RuleInfrastructureError:
        raise
    except RuleValidationError as ex:
        raise RuleValidationError(f"{source.context}: {ex}") from ex


def _redis_sort_key(value):
    if isinstance(value, bytes):
        return (0, value)
    if isinstance(value, str):
        return (1, value.encode("utf-8"))
    return (2, repr(value).encode("utf-8", errors="backslashreplace"))


def _decode_redis_identifier(value, label):
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeError as ex:
            raise RuleValidationError(
                f"{label} is not valid UTF-8: {ex}"
            ) from ex
    raise RuleValidationError(f"{label} must be text")


def discover_alert_rule_sources(redis_client, warning=None):
    """Return provider-published rule fields in deterministic source order."""
    if warning is None:
        warning = _stderr_warning

    try:
        raw_keys = list(
            redis_client.scan_iter("module/*/metrics_alert_rules")
        )
    except Exception as ex:
        raise RuleInfrastructureError(
            f"cannot discover module alert-rule sources in Redis: {ex}"
        ) from ex

    redis_keys = {}
    for raw_key in sorted(raw_keys, key=_redis_sort_key):
        try:
            redis_key = _decode_redis_identifier(raw_key, "Redis key")
        except RuleValidationError as ex:
            warning(f"Skipped module alert-rule hash: {ex}")
            continue
        redis_keys.setdefault(redis_key, raw_key)

    sources = []
    for redis_key in sorted(redis_keys):
        parts = redis_key.split("/")
        if (
            len(parts) != 3
            or parts[0] != "module"
            or parts[2] != "metrics_alert_rules"
        ):
            warning(
                f"Skipped module alert-rule hash {redis_key!r}: "
                "invalid Redis key shape"
            )
            continue

        try:
            fields = redis_client.hgetall(redis_keys[redis_key])
        except Exception as ex:
            raise RuleInfrastructureError(
                f"cannot read module alert-rule hash {redis_key!r}: {ex}"
            ) from ex
        if not isinstance(fields, Mapping):
            raise RuleInfrastructureError(
                f"module alert-rule hash {redis_key!r} did not return a mapping"
            )

        for raw_field, payload in sorted(
            fields.items(), key=lambda item: _redis_sort_key(item[0])
        ):
            try:
                rule_set_name = _decode_redis_identifier(
                    raw_field, "rule-set name"
                )
            except RuleValidationError as ex:
                warning(
                    f"Skipped module alert rule from {redis_key!r}: {ex}"
                )
                continue
            sources.append(
                AlertRuleSource(
                    publisher_id=parts[1],
                    rule_set_name=rule_set_name,
                    payload=payload,
                    redis_key=redis_key,
                )
            )

    return sorted(
        sources,
        key=lambda source: (
            source.publisher_id,
            source.rule_set_name,
            source.redis_key,
        ),
    )


def _source_identity(source):
    return (source.redis_key, source.rule_set_name)


def _ownership_header(source):
    metadata = {
        "field": source.rule_set_name,
        "redis_key": source.redis_key,
    }
    return OWNERSHIP_PREFIX + json.dumps(
        metadata,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"


def _read_rule_owner(path):
    try:
        with open(path, "rb") as stream:
            first_line = stream.readline(4096)
    except OSError as ex:
        raise RuleInfrastructureError(
            f"cannot read generated rule ownership from {path}: {ex}"
        ) from ex

    prefix = OWNERSHIP_PREFIX.encode("ascii")
    if not first_line.startswith(prefix):
        return None
    try:
        metadata = json.loads(first_line[len(prefix):].decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    redis_key = metadata.get("redis_key")
    rule_set_name = metadata.get("field")
    if not isinstance(redis_key, str) or not isinstance(rule_set_name, str):
        return None
    return (redis_key, rule_set_name)


def _serialize_candidate(source, document):
    try:
        payload = yaml.safe_dump(
            document,
            allow_unicode=True,
            sort_keys=False,
        )
    except Exception as ex:
        raise RuleValidationError(
            f"{source.context}: cannot serialize effective rule document: {ex}"
        ) from ex
    return _ownership_header(source) + payload


def _install_candidate(
    source, document, rules_directory, promtool_runner
):
    serialized = _serialize_candidate(source, document)
    destination = os.path.join(rules_directory, source.filename)
    temporary_path = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            dir=rules_directory,
            prefix=f".{source.filename}.",
            suffix=".tmp",
            text=True,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())

        try:
            promtool_runner.check_rules(temporary_path)
        except RuleValidationError as ex:
            raise RuleValidationError(
                f"{source.context}: promtool check rules failed: {ex}"
            ) from ex

        os.replace(temporary_path, destination)
        temporary_path = None
    except (RuleValidationError, RuleInfrastructureError):
        raise
    except OSError as ex:
        raise RuleInfrastructureError(
            f"cannot atomically install {destination}: {ex}"
        ) from ex
    finally:
        if temporary_path is not None:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass
            except OSError as ex:
                raise RuleInfrastructureError(
                    f"cannot remove temporary rule file {temporary_path}: {ex}"
                ) from ex


def _is_generated_rule_name(filename):
    return (
        filename.startswith(GENERATED_RULE_PREFIX)
        and filename.endswith(GENERATED_RULE_SUFFIX)
    )


def _generated_rule_entries(rules_directory):
    try:
        with os.scandir(rules_directory) as entries:
            return sorted(
                (
                    entry
                    for entry in entries
                    if _is_generated_rule_name(entry.name)
                    and (
                        entry.is_file(follow_symlinks=False)
                        or entry.is_symlink()
                    )
                ),
                key=lambda entry: entry.name,
            )
    except OSError as ex:
        raise RuleInfrastructureError(
            f"cannot scan generated rule directory {rules_directory}: {ex}"
        ) from ex


def _clean_up_stale_generated_rules(rules_directory, active_sources):
    for entry in _generated_rule_entries(rules_directory):
        if entry.is_symlink():
            owner = None
        else:
            owner = _read_rule_owner(entry.path)
        source = active_sources.get(owner)
        if source is not None and entry.name == source.filename:
            continue
        try:
            os.remove(entry.path)
        except OSError as ex:
            raise RuleInfrastructureError(
                f"cannot remove stale generated rule {entry.path}: {ex}"
            ) from ex


def _warn_duplicate_installed_identities(rules_directory, warning):
    identities = defaultdict(list)
    for entry in _generated_rule_entries(rules_directory):
        if entry.is_symlink() or _read_rule_owner(entry.path) is None:
            continue
        try:
            with open(entry.path, encoding="utf-8") as stream:
                document = yaml.safe_load(stream)
        except OSError as ex:
            raise RuleInfrastructureError(
                f"cannot inspect installed rule file {entry.path}: {ex}"
            ) from ex
        except (UnicodeError, yaml.YAMLError) as ex:
            warning(
                f"Warning: cannot inspect installed module alert identities "
                f"in {entry.path}: {ex}"
            )
            continue

        if not isinstance(document, dict):
            continue
        groups = document.get("groups")
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            rules = group.get("rules")
            if not isinstance(rules, list):
                continue
            for index, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    continue
                labels = rule.get("labels")
                if not isinstance(labels, dict):
                    continue
                alert_name = rule.get("alert")
                module_id = labels.get("module_id")
                if isinstance(alert_name, str) and isinstance(module_id, str):
                    identities[(alert_name, module_id)].append(
                        f"{entry.name}:{group.get('name', '?')}[{index}]"
                    )

    for (alert_name, module_id), locations in sorted(identities.items()):
        if len(locations) > 1:
            warning(
                "Warning: duplicate installed module alert identity "
                f"(alertname={alert_name!r}, module_id={module_id!r}) at "
                + ", ".join(locations)
            )


def _report_source_failure(source, error, warning):
    detail = str(error)
    context_prefix = f"{source.context}: "
    if detail.startswith(context_prefix):
        detail = detail[len(context_prefix):]
    warning(f"Skipped module alert rule from {source.context}: {detail}")


def provision_module_alert_rules(
    redis_client,
    rules_directory="rules.d",
    promtool_runner=None,
    warning=None,
    transformer=transform_alert_rule,
):
    """Materialize provider-published rules without touching legacy rules."""
    if warning is None:
        warning = _stderr_warning
    try:
        os.makedirs(rules_directory, exist_ok=True)
    except OSError as ex:
        raise RuleInfrastructureError(
            f"cannot create generated rule directory {rules_directory}: {ex}"
        ) from ex

    sources = discover_alert_rule_sources(redis_client, warning)
    active_sources = {}
    sources_by_filename = defaultdict(list)
    for source in sources:
        try:
            filename = source.filename
        except RuleValidationError as ex:
            _report_source_failure(source, ex, warning)
            continue
        active_sources[_source_identity(source)] = source
        sources_by_filename[filename].append(source)

    filename_collisions = set()
    for filename, owners in sorted(sources_by_filename.items()):
        if len(owners) < 2:
            continue
        contexts = ", ".join(owner.context for owner in owners)
        for source in owners:
            filename_collisions.add(_source_identity(source))
            warning(
                f"Skipped module alert rule from {source.context}: output "
                f"filename collision for {filename!r} among {contexts}"
            )

    transformed = []
    runner = promtool_runner
    for source in sources:
        identity = _source_identity(source)
        if identity not in active_sources or identity in filename_collisions:
            continue
        if runner is None:
            runner = PromtoolRunner()
        try:
            document = transformer(source, runner, warning)
        except RuleInfrastructureError:
            raise
        except RuleValidationError as ex:
            _report_source_failure(source, ex, warning)
            continue
        transformed.append((source, document))

    group_owners = defaultdict(list)
    for source, document in transformed:
        for group in document["groups"]:
            group_owners[group["name"]].append(source)

    group_collisions = defaultdict(list)
    for group_name, owners in sorted(group_owners.items()):
        owner_identities = {_source_identity(owner) for owner in owners}
        if len(owner_identities) < 2:
            continue
        for source in owners:
            group_collisions[_source_identity(source)].append(group_name)

    for source, _document in transformed:
        names = group_collisions.get(_source_identity(source))
        if names:
            warning(
                f"Skipped module alert rule from {source.context}: effective "
                "group-name collision for "
                + ", ".join(repr(name) for name in sorted(set(names)))
            )

    for source, document in transformed:
        if _source_identity(source) in group_collisions:
            continue
        try:
            _install_candidate(source, document, rules_directory, runner)
        except RuleInfrastructureError:
            raise
        except RuleValidationError as ex:
            _report_source_failure(source, ex, warning)

    _clean_up_stale_generated_rules(rules_directory, active_sources)
    _warn_duplicate_installed_identities(rules_directory, warning)
