#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Bulk SSH transport only; fixtures and expectations belong in Robot."""

import base64
import hashlib
import json
import pathlib
import subprocess
import sys
import urllib.request


def redis_batch(client, operation, entries):
    pipeline = client.pipeline(transaction=False)
    for entry in entries:
        key, field = (base64.b64decode(value) for value in entry[:2])
        if operation == "exists":
            pipeline.hexists(key, field)
        elif operation == "write":
            pipeline.hset(key, field, base64.b64decode(entry[2]))
        elif operation == "delete":
            pipeline.hdel(key, field)
            pipeline.hexists(key, field)
        else:
            raise ValueError(operation)
    results = pipeline.execute(raise_on_error=False)
    # Return every result, including errors, so cleanup can acknowledge only
    # confirmed deletions. A lost SSH response leaves all registrations pending.
    return [str(value) if isinstance(value, Exception) else value for value in results]


def snapshot(url):
    directories = {}
    files = {}
    for directory in ("prometheus.d", "rules.d"):
        paths = sorted(pathlib.Path(directory).iterdir())
        directories[directory] = [path.name for path in paths]
        for path in paths:
            if path.is_file():
                content = path.read_bytes()
                files[str(path)] = {
                    "content": content.decode(),
                    "checksum": hashlib.sha256(content).hexdigest(),
                }
    config = pathlib.Path("alertmanager.yml").read_text()
    files["alertmanager.yml"] = {"content": config}
    active = subprocess.run(
        ["systemctl", "--user", "--quiet", "is-active", "prometheus.service"],
        check=False,
    ).returncode
    if active not in (0, 3):
        raise RuntimeError(f"cannot read Prometheus service state: {active}")
    rules = None
    if active == 0:
        with urllib.request.urlopen(url + "/api/v1/rules", timeout=10) as response:
            rules = json.load(response)
    return {"directories": directories, "files": files, "rules": rules}


def format_expressions(expressions):
    return [subprocess.run(
        ["podman", "exec", "prometheus", "/bin/promtool", "--experimental",
         "promql", "format", "--", expression],
        check=True, stdout=subprocess.PIPE, text=True,
    ).stdout.strip() for expression in expressions]


def main():
    operation, encoded = sys.argv[1:]
    values = json.loads(base64.b64decode(encoded))
    if operation == "snapshot":
        result = snapshot(values)
    elif operation == "format":
        result = format_expressions(values)
    else:
        import agent
        result = redis_batch(agent.redis_connect(privileged=True), operation, values)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
