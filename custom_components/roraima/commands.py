"""Commands from Roraima: only shared parts, only their own services, only announced fields (law 3).

A command that Roraima sends names a shared device, one of its announced parts, an action and its
parameters. The action must be a service of the entity's own domain that Home Assistant offers for
that entity and that the part announced; the parameters must be fields the part announced for that
action, each of its kind and in its limits. Everything else is refused, even when it comes from
Roraima. The service is called on the part's own entity and nothing else, so a compromised server
can move at most the parts the seller shared. A command is done when Home Assistant completes the
service in time; the answer carries the state of after.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Mapping
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import Context, HomeAssistant, State

from .actions import async_descriptions, check_params, part_actions
from .announce import PartActions, state_text
from .selection import shareable_rows

_LOGGER = logging.getLogger(__name__)

# Seconds for the service call; the contract wants the resp within 10.
COMMAND_BUDGET = 9.0


@dataclass(frozen=True, slots=True)
class Target:
    """An accepted command: the entity of this moment, a service of its domain and its data."""

    entity_id: str
    domain: str
    service: str
    data: dict[str, Any]


async def async_resolve(
    hass: HomeAssistant,
    selected: Collection[str],
    announced: Mapping[str, Mapping[str, PartActions]],
    cmd: Mapping[str, Any],
) -> Target | str:
    """Return what a cmd may do, or the contract code that refuses it."""
    device, part, action = cmd.get("device"), cmd.get("part"), cmd.get("action")
    if not isinstance(device, str) or device not in selected or device not in announced:
        return "not_shared"
    if not isinstance(part, str) or part not in announced[device]:
        return "part_unknown"
    actions = announced[device][part]
    if not isinstance(action, str) or action not in actions:
        return "action_not_allowed"
    descriptions = await async_descriptions(hass)
    row = shareable_rows(hass).get(device)
    if row is None:
        return "not_shared"
    entry = row.parts.get(part)
    if entry is None:
        return "part_unknown"
    if entry.domain != part.split(".", 1)[0]:
        return "domain_not_allowed"
    # The actions of this moment, worked out as for the announce: a feature the entity lost, or
    # a part that became a service part, takes its actions away at once.
    current = part_actions(descriptions, entry, hass.states.get(entry.entity_id))
    if action not in current:
        return "action_not_allowed"
    fields = {key: field for key, field in current[action].items() if key in actions[action]}
    data = check_params(fields, cmd.get("params"))
    if data is None:
        return "params_invalid"
    return Target(entry.entity_id, entry.domain, action, data)


def _answer(error: str | None, state: State | None) -> dict[str, Any]:
    answer: dict[str, Any] = {"ok": error is None}
    if error is not None:
        answer["error"] = error
    answer["state"] = state_text(state)
    return answer


async def async_run(hass: HomeAssistant, target: Target) -> dict[str, Any]:
    """Call the service on the part's entity alone: the resp without its id."""
    entity_id = target.entity_id
    before = hass.states.get(entity_id)
    if before is None or before.state == STATE_UNAVAILABLE:
        return _answer("unavailable", before)
    try:
        async with asyncio.timeout(COMMAND_BUDGET):
            await hass.services.async_call(
                target.domain,
                target.service,
                # The entity goes last: no field can ever name another target.
                {**target.data, ATTR_ENTITY_ID: entity_id},
                blocking=True,
                context=Context(),
            )
    except TimeoutError:
        _LOGGER.warning(
            "%s.%s on %s did not finish in %s s", target.domain, target.service, entity_id, COMMAND_BUDGET
        )
        return _answer("ha_error", hass.states.get(entity_id))
    except Exception as err:  # noqa: BLE001 - any failure of the service is answered, never lost
        _LOGGER.warning(
            "%s.%s on %s failed: %s", target.domain, target.service, entity_id, type(err).__name__
        )
        return _answer("ha_error", hass.states.get(entity_id))
    return _answer(None, hass.states.get(entity_id))
