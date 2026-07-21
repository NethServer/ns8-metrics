#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

import os
import re
import subprocess
import sys
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
        if result.returncode in {125, 126, 127} or result.returncode < 0:
            detail = output or f"podman exited with status {result.returncode}"
            raise RuleInfrastructureError(detail)
        if result.returncode != 0:
            detail = output or f"promtool exited with status {result.returncode}"
            raise RuleValidationError(detail)
        return output


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
