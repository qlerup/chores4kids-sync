from __future__ import annotations
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, SIGNAL_CHILDREN_UPDATED, SIGNAL_DATA_UPDATED
from .storage import KidsChoresStore

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    store: KidsChoresStore = hass.data[DOMAIN]["store"]

    entities: dict[str, KidsChoresPointsSensor] = {}
    all_tasks_sensor: Chores4KidsAllTasksSensor | None = None
    shop_sensor: Chores4KidsShopSensor | None = None

    async def _cleanup_removed_entities(removed_ids: set[str]):
        registry = er.async_get(hass)
        dev_registry = dr.async_get(hass)
        for rid in removed_ids:
            ent = entities.pop(rid, None)
            if ent is None:
                continue
            # Remove entity from state machine
            await ent.async_remove()
            # Remove from entity registry to avoid leftover 'unavailable' restored entities
            reg_entry = registry.async_get(ent.entity_id)
            device_id = reg_entry.device_id if reg_entry else None
            if reg_entry:
                registry.async_remove(ent.entity_id)
            # Remove device if empty
            if device_id:
                device = dev_registry.async_get(device_id)
                if device:
                    # if no entities left on device, remove it
                    if not [e for e in registry.entities.values() if e.device_id == device_id]:
                        dev_registry.async_remove_device(device_id)

    @callback
    def _sync_entities():
        # Add missing children sensors
        for ch in store.children:
            key = ch.id
            if key not in entities:
                ent = KidsChoresPointsSensor(store, ch.id)
                entities[key] = ent
                async_add_entities([ent])
        # Ensure global tasks sensor exists
        nonlocal all_tasks_sensor
        if all_tasks_sensor is None:
            all_tasks_sensor = Chores4KidsAllTasksSensor(store)
            async_add_entities([all_tasks_sensor])
        # Ensure shop sensor exists
        nonlocal shop_sensor
        if shop_sensor is None:
            shop_sensor = Chores4KidsShopSensor(store)
            async_add_entities([shop_sensor])
        # Remove sensors for deleted children (runtime removal + registry/device cleanup)
        current_ids = {c.id for c in store.children}
        removed_ids = set(entities.keys()) - current_ids
        if removed_ids:
            hass.async_create_task(_cleanup_removed_entities(removed_ids))

        # Purge orphan registry entries from older versions (slug-based unique_ids)
        registry = er.async_get(hass)
        reg_entries = er.async_entries_for_config_entry(registry, entry.entry_id)
        for e in reg_entries:
            if e.platform != "sensor":
                continue
            uid = e.unique_id or ""
            if uid.startswith("chores4kids_points_"):
                suffix = uid.replace("chores4kids_points_", "")
                if suffix not in current_ids:
                    # remove entity and its device
                    device_id = e.device_id
                    registry.async_remove(e.entity_id)
                    if device_id:
                        dev_registry = dr.async_get(hass)
                        device = dev_registry.async_get(device_id)
                        if device and not [x for x in registry.entities.values() if x.device_id == device_id]:
                            dev_registry.async_remove_device(device_id)

    @callback
    def _handle_children_updated():
        _sync_entities()

    @callback
    def _handle_data_updated():
        for ent in entities.values():
            ent.async_schedule_update_ha_state(True)
        if all_tasks_sensor is not None:
            all_tasks_sensor.async_schedule_update_ha_state(True)
        if shop_sensor is not None:
            shop_sensor.async_schedule_update_ha_state(True)

    _sync_entities()

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_CHILDREN_UPDATED, _handle_children_updated))
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DATA_UPDATED, _handle_data_updated))

class KidsChoresPointsSensor(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, store: KidsChoresStore, child_id: str):
        self._store = store
        self._child_id = child_id
        ch = self._child
        # Use stable child id for unique_id so renames don't create orphan entities
        self._attr_unique_id = f"chores4kids_points_{ch.id}"
        self._attr_name = f"Chores4Kids Points {ch.name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"child_{ch.id}")},
            name=f"Chores4Kids – {ch.name}",
            manufacturer="Chores4Kids",
            model="Virtual Child",
        )

    @property
    def _child(self):
        # simple resolver; raise if missing
        for c in self._store.children:
            if c.id == self._child_id:
                return c
        # fallback dummy to avoid crashes if removed
        return type("X", (), {"id": self._child_id, "name": "(deleted)", "slug": "deleted", "points": 0})

    @property
    def native_value(self):
        return self._child.points

    @property
    def extra_state_attributes(self):
        ch = self._child
        tasks = [t for t in self._store.tasks if t.assigned_to == ch.id]
        counts = {
            "assigned_count": sum(1 for t in tasks if t.status == "assigned"),
            "in_progress_count": sum(1 for t in tasks if t.status == "in_progress"),
            "awaiting_approval_count": sum(1 for t in tasks if t.status == "awaiting_approval"),
            "approved_count": sum(1 for t in tasks if t.status == "approved"),
            "rejected_count": sum(1 for t in tasks if t.status == "rejected"),
        }
        # keep tasks lightweight
        tasks_min = [{
            "id": t.id,
            "title": t.title,
            "points": t.points,
            "status": t.status,
            "due": t.due,
            "icon": getattr(t, "icon", None),
        } for t in tasks]
        return {
            "child_id": ch.id,
            "name": ch.name,
            "slug": ch.slug,
            "pending_count": counts["awaiting_approval_count"],
            "tasks": tasks_min,
            **counts,
        }


class Chores4KidsAllTasksSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Chores4Kids Tasks"
    _attr_unique_id = "chores4kids_tasks_all"

    def __init__(self, store: KidsChoresStore):
        self._store = store
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "tasks")},
            name="Chores4Kids – Tasks",
            manufacturer="Chores4Kids",
            model="Task Index",
        )

    @property
    def native_value(self):
        return len(self._store.tasks)

    @property
    def extra_state_attributes(self):
        def child_name(cid: str | None):
            if not cid:
                return None
            for c in self._store.children:
                if c.id == cid:
                    return c.name
            return None
        tasks = [{
            "id": t.id,
            "title": t.title,
            "points": t.points,
            "status": t.status,
            "due": t.due,
            "assigned_to": t.assigned_to,
            "assigned_to_name": child_name(t.assigned_to),
            "created": getattr(t, "created", None),
            "icon": getattr(t, "icon", None),
            "repeat_days": t.repeat_days,
            "repeat_child_id": t.repeat_child_id,
        } for t in self._store.tasks]
        return {"tasks": tasks}


class Chores4KidsShopSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Chores4Kids Shop"
    _attr_unique_id = "chores4kids_shop"

    def __init__(self, store: KidsChoresStore):
        self._store = store
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "shop")},
            name="Chores4Kids – Shop",
            manufacturer="Chores4Kids",
            model="Point Shop",
        )

    @property
    def native_value(self):
        return len([i for i in self._store.items if i.active])

    @property
    def extra_state_attributes(self):
        # denormalize child name on purchases
        def child_name(cid: str | None):
            if not cid:
                return None
            for c in self._store.children:
                if c.id == cid:
                    return c.name
            return None
        items = [{
            "id": i.id,
            "title": i.title,
            "price": i.price,
            "icon": i.icon,
            "image": getattr(i, 'image', ''),
            "active": i.active,
            "actions": getattr(i, 'actions', []),
        } for i in self._store.items]
        purchases = [{
            "id": p.id,
            "child_id": p.child_id,
            "child_name": p.child_name or child_name(p.child_id),
            "item_id": p.item_id,
            "title": p.title,
            "price": p.price,
            "icon": p.icon,
            "image": getattr(p, 'image', ''),
            "ts": p.ts,
        } for p in self._store.purchases]
        return {"items": items, "purchases": purchases}
