"""Load ``racers.yaml`` and build the racers for the active profile.

Profiles mix models on the Snapdragon NPU (``runtime: qnn``), simulated racers and the
human player — switching is a one-line config change.
"""

from __future__ import annotations

import random
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

from san_francisco.deciders import (
    Decider,
    HumanDecider,
    PromptBudget,
    QnnDecider,
    SimulatedDecider,
)
from san_francisco.errors import ConfigError
from san_francisco.laya import LayaSettings
from san_francisco.maze import Maze
from san_francisco.models import CityMap, GameSpec, RacerSpec, RaceSettings
from san_francisco.qnn_host import QnnHostSettings, get_host

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "racers.yaml"
DEFAULT_GAME = "sf"  # what a request without a game gets


class AppConfig(BaseModel):
    """Parsed ``racers.yaml``."""

    race: RaceSettings
    qnn: QnnHostSettings | None = None
    laya: LayaSettings | None = None
    active_profile: str
    profiles: dict[str, list[RacerSpec]]
    games: dict[str, GameSpec] = Field(
        default_factory=lambda: {DEFAULT_GAME: GameSpec(title="San Francisco")}
    )
    # Street maps loaded from each game's ``maps``, not written in the YAML.
    cities: dict[str, list[CityMap]] = Field(default_factory=dict, exclude=True)

    def racers(self, profile: str | None = None) -> list[RacerSpec]:
        """Racer specs for ``profile`` (default: the active one)."""
        name = profile or self.active_profile
        if name not in self.profiles:
            raise ConfigError(f"Unknown profile {name!r}; choose from {sorted(self.profiles)}")
        return self.profiles[name]

    def game(self, name: str | None) -> str:
        """``name`` if it is a known game, else the default one.

        Raises:
            ConfigError: If ``name`` is not a game in ``racers.yaml``.
        """
        game = name or DEFAULT_GAME
        if game not in self.games:
            raise ConfigError(f"Unknown game {game!r}; choose from {sorted(self.games)}")
        return game

    def city(self, game: str, area: int) -> CityMap | None:
        """The street map of ``game``'s ``area`` (wrapping around), or None for a hedge maze."""
        maps = self.cities.get(game, [])
        return maps[area % len(maps)] if maps else None


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Read and validate the YAML config.

    Raises:
        ConfigError: If the file is missing or does not match the schema.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        config = AppConfig.model_validate(raw)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"Invalid config {path}: {exc}") from exc
    config.cities = {
        name: [load_city(path.parent / street_map) for street_map in game.maps]
        for name, game in config.games.items()
    }
    return config


def load_city(path: Path) -> CityMap:
    """Read a street map made by ``scripts/fetch_map.py``.

    Raises:
        ConfigError: If the file is missing or invalid.

    """
    try:
        return CityMap.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        hint = "make one with scripts/fetch_map.py"
        raise ConfigError(f"Street map not found: {path} ({hint})") from exc
    except ValidationError as exc:
        raise ConfigError(f"Invalid street map {path}: {exc}") from exc


def build_decider(
    spec: RacerSpec, maze: Maze, seed: int, qnn: QnnHostSettings | None = None
) -> Decider:
    """Create the decider that will drive ``spec``'s car.

    Args:
        spec: Racer definition from the config.
        maze: Current track (simulated racers use it to judge moves).
        seed: Race seed; combined with the racer id so racers behave independently.
        qnn: NPU host settings, required for ``runtime: qnn`` racers.

    Raises:
        ConfigError: If the racer's runtime is not available yet or misconfigured.
    """
    if spec.runtime == "simulated":
        if spec.sim is None:
            raise ConfigError(f"Racer {spec.id!r} is simulated but has no 'sim' profile")
        return SimulatedDecider(spec.sim, maze, random.Random(f"{seed}:{spec.id}"))
    if spec.runtime == "human":
        return HumanDecider()
    key = spec.qnn_model
    if qnn is None or key is None or key not in qnn.models:
        raise ConfigError(
            f"Racer {spec.id!r} runs on the NPU: set 'qnn_model' to a 'qnn.models' key"
        )
    model = qnn.models[key]
    if key not in _budgets:
        _budgets[key] = PromptBudget(model.max_seq_len)
    return QnnDecider(spec, get_host(qnn, key), _budgets[key], model.prompt_suffix)


# Prompt-size estimates learned per host, kept across races like the host itself.
_budgets: dict[str, PromptBudget] = {}
