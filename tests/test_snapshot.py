"""Snapshot aller Entitäten: Registry-Einträge (unique_id, device_class, state_class, Einheit) und Zustände.

Ändert sich ein Name, eine Einheit, eine unique_id oder ein Attribut, schlägt der Test fehl. Gewollte Änderungen
mit `pytest tests/test_snapshot.py --snapshot-update` übernehmen und den Unterschied in `snapshots/` prüfen.
"""

from __future__ import annotations

from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from syrupy.assertion import SnapshotAssertion

from custom_components.wetter.const import CONF_LOCAL_TEMPERATURE, CONF_POLLEN_UV, DOMAIN

from .conftest import LAT, LON
from .test_netatmo import _area, _netatmo

# fixed id, so unique_ids and the snapshot stay the same in every run
ENTRY_ID = "01TESTENTRY0000000000000000"


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """HA serializer: random registry ids and device ids are masked, states written as dicts."""
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture
async def loaded(hass: HomeAssistant, brightsky: AiohttpClientMocker) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wetter",
        entry_id=ENTRY_ID,
        unique_id=f"{LAT:.3f},{LON:.3f}",
        data={CONF_LATITUDE: LAT, CONF_LONGITUDE: LON},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


@pytest.mark.parametrize("platform", [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.WEATHER])
async def test_entities(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    platform: Platform,
) -> None:
    entries = sorted(
        (e for e in er.async_entries_for_config_entry(entity_registry, ENTRY_ID) if e.domain == platform),
        key=lambda e: e.entity_id,
    )
    assert entries
    for entry in entries:
        assert entry == snapshot(name=f"{entry.entity_id}-entry")
        assert entry.disabled_by is None
        state = hass.states.get(entry.entity_id)
        assert state is not None, entry.entity_id
        assert state == snapshot(name=f"{entry.entity_id}-state")


async def test_registry_rules(hass: HomeAssistant, loaded: MockConfigEntry, entity_registry: er.EntityRegistry) -> None:
    """Grundregeln unabhängig vom Snapshot: eindeutige unique_ids je Eintrag, Messwerte mit state_class."""
    entries = er.async_entries_for_config_entry(entity_registry, ENTRY_ID)
    unique_ids = [e.unique_id for e in entries]
    assert len(unique_ids) == len(set(unique_ids))
    assert all(uid.startswith(f"{ENTRY_ID}_") for uid in unique_ids)
    for entry in entries:
        if entry.domain != Platform.SENSOR or entry.entity_category is not None:
            continue
        state = hass.states.get(entry.entity_id)
        assert state is not None
        if entry.unit_of_measurement is not None:
            assert (entry.capabilities or {}).get("state_class") is not None, f"{entry.entity_id} ohne state_class"


async def test_with_netatmo_area(
    hass: HomeAssistant, brightsky: AiohttpClientMocker, entity_registry: er.EntityRegistry, snapshot: SnapshotAssertion
) -> None:
    """Mit Netatmo-Bereich: Regler „Nachbarschaft Radius“ und die Nachbarschafts-Diagnose mit Werten."""
    _netatmo(hass, {"Umgebung": {**_area(LAT, LON, d=0.03), "mode": "avg"}})
    hass.states.async_set("sensor.umgebung_temperature", "12.3")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wetter",
        entry_id=ENTRY_ID,
        data={CONF_LATITUDE: LAT, CONF_LONGITUDE: LON},
        options={CONF_LOCAL_TEMPERATURE: "sensor.umgebung_temperature", CONF_POLLEN_UV: False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    ids = sorted(e.entity_id for e in er.async_entries_for_config_entry(entity_registry, ENTRY_ID))
    assert not [i for i in ids if "pollen" in i or "uv_index" in i], "Pollen/UV aus"
    for entity_id in (
        "number.wetter_nachbarschaft_radius",
        "sensor.wetter_nachbarschaftsbereich",
        "sensor.wetter_quelle",
    ):
        reg = entity_registry.async_get(entity_id)
        assert reg == snapshot(name=f"{entity_id}-entry")
        assert hass.states.get(entity_id) == snapshot(name=f"{entity_id}-state")
