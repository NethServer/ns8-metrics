#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

import json
import os
import secrets
import tempfile

import agent
import bcrypt
import yaml

CREDENTIALS_FILE = "basic_auth.json"
WEB_CONFIG_FILE = "web-config.yml"
USERNAME = "metrics"


def bcrypt_hash(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12, prefix=b"2b")).decode()


def _read_credentials(path):
    with open(path) as f:
        try:
            return json.load(f)
        except ValueError as ex:
            raise RuntimeError(f"invalid credentials file {path}: delete it to regenerate, then restart "
                               "prometheus, alertmanager and grafana") from ex


def ensure_credentials(state_dir="."):
    """Create the credentials file once and return its content. Core reads
    it too: keep its name and the username and password keys."""
    path = os.path.join(state_dir, CREDENTIALS_FILE)
    try:
        return _read_credentials(path)
    except FileNotFoundError:
        pass
    password = secrets.token_urlsafe(32)
    creds = {"username": USERNAME, "password": password, "hash": bcrypt_hash(password)}
    # mkstemp creates the file with mode 0600
    fd, tmp = tempfile.mkstemp(dir=state_dir)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(creds, f)
        # Publish without overwriting: a concurrent creator may have won.
        os.link(tmp, path)
    except FileExistsError:
        return _read_credentials(path)
    finally:
        os.unlink(tmp)
    return creds


def write_web_config(creds, state_dir="."):
    path = os.path.join(state_dir, WEB_CONFIG_FILE)
    with agent.safe_open(path, "w") as f:
        yaml.safe_dump({"basic_auth_users": {creds["username"]: creds["hash"]}}, f)
    # The Prometheus and Alertmanager container users must read it
    os.chmod(path, 0o644)
