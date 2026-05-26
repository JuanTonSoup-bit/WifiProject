"""Config loader with validation for the CSI detection system."""

from __future__ import annotations

import logging
import os
from typing import Any

import yaml

logger = logging.getLogger(__name__)

REQUIRED_KEYS = [
    ("network", "pc_ip"),
    ("network", "udp_port"),
    ("dsp", "window_size"),
    ("detection", "motion_threshold"),
]


class ConfigError(ValueError):
    pass


def load_config(path: str = "config.yaml") -> dict:
    """Load and validate YAML config. Raises ConfigError on missing required keys."""
    if not os.path.exists(path):
        raise ConfigError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        try:
            cfg = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(cfg, dict):
        raise ConfigError(f"Config must be a YAML mapping, got: {type(cfg)}")

    for section, key in REQUIRED_KEYS:
        if get_nested(cfg, section, key) is None:
            raise ConfigError(f"Missing required config key: {section}.{key}")

    logger.debug("Loaded config from %s", path)
    return cfg


def get_nested(cfg: dict, *keys: str, default: Any = None) -> Any:
    """Safe nested dict access. Returns default if any key is missing."""
    node = cfg
    for k in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(k)
        if node is None:
            return default
    return node
