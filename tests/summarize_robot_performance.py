#!/usr/bin/env python3

#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Summarize Robot timings and work counts without wall-clock pass thresholds.

Usage: python3 tests/summarize_robot_performance.py path/to/output.xml [...]
Optionally pass --podman-events events.jsonl for a single run. Capture container
events over that run's time window with `podman events --format json` under the
metrics module user. Service-container starts are excluded from helper counts.
"""

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def summarize(path, events=None):
    root = ET.parse(path).getroot()
    suite = next(node for node in root.iter("suite")
                 if node.get("name") == "Module Alert Rules")
    # Robot also serializes keywords in untaken branches and after RETURN.
    # Count commands that ran, including failed attempts, rather than stubs.
    keywords = [node for node in suite.iter("kw")
                if node.find("status").get("status") != "NOT RUN"]
    tests = suite.findall("test")
    result = {
        "output": str(Path(path).resolve()),
        "suite_seconds": float(suite.find("status").get("elapsed")),
        "cleanup_seconds": sum(float(node.find("status").get("elapsed"))
                               for node in keywords
                               if node.get("name") == "Reset Module Rule Fixtures"),
        "ssh_commands": sum(node.get("name") == "Execute Command"
                            and node.get("owner") == "SSHLibrary"
                            for node in keywords),
        "provisioning_events": sum(node.get("name") in {
            "Signal Module Rule Change", "Signal Metrics Target Change",
        } for node in keywords),
        "resets": sum(node.get("name") == "Reset Module Rule Fixtures"
                      for node in keywords),
        "helper_startups": None,
        "tests": {node.get("name"): {
            "status": node.find("status").get("status"),
            "seconds": float(node.find("status").get("elapsed")),
        } for node in tests},
    }
    if events:
        records = [json.loads(line) for line in Path(events).read_text().splitlines()]
        result["helper_startups"] = sum(
            event.get("Type") == "container" and event.get("Status") == "start"
            and event.get("Name") != "prometheus"
            and event.get("Image") == "quay.io/prometheus/prometheus:v3.5.3"
            for event in records
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outputs", nargs="+")
    parser.add_argument("--podman-events")
    arguments = parser.parse_args()
    if arguments.podman_events and len(arguments.outputs) != 1:
        parser.error("--podman-events requires exactly one Robot output")
    print(json.dumps([summarize(path, arguments.podman_events)
                      for path in arguments.outputs], indent=2))


if __name__ == "__main__":
    main()
