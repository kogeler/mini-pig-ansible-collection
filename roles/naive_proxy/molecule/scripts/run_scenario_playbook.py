#!/usr/bin/env python3
"""Run a role-local playbook against an already converged Molecule scenario."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from molecule.config import Config
from molecule.exceptions import MoleculeError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-config", required=True, type=Path)
    parser.add_argument("--scenario-config", required=True, type=Path)
    parser.add_argument("--playbook", required=True, type=Path)
    args = parser.parse_args()

    base_config = args.base_config.resolve(strict=True)
    scenario_config = args.scenario_config.resolve(strict=True)
    playbook = args.playbook.resolve(strict=True)

    config = Config(
        str(scenario_config),
        args={
            "base_config": [str(base_config)],
            "debug": False,
            "env_file": None,
        },
        command_args={
            "command_borders": True,
            "subcommand": "side_effect",
        },
    )

    if not config.state.created or not config.state.converged:
        print(
            f"scenario {config.scenario.name!r} is not converged; "
            "run its Make converge target first",
            file=sys.stderr,
        )
        return 2

    if config.provisioner is None:
        print(f"scenario {config.scenario.name!r} has no provisioner", file=sys.stderr)
        return 2

    inventory = Path(config.provisioner.inventory_file)
    ansible_config = Path(config.provisioner.config_file)
    missing = [path for path in (inventory, ansible_config) if not path.is_file()]
    if missing:
        print(
            "scenario state is incomplete; missing "
            + ", ".join(str(path) for path in missing),
            file=sys.stderr,
        )
        return 2

    config.action = "side_effect"
    config.provisioner.converge(playbook=str(playbook))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, MoleculeError) as error:
        print(f"scenario playbook failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
