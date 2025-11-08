from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import Platform
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_CHILDREN_UPDATED, SIGNAL_DATA_UPDATED
from .storage import KidsChoresStore

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Chores4Kids integration."""
    store = KidsChoresStore(hass)
    await store.async_load()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["store"] = store

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Services
    async def svc_add_child(call: ServiceCall):
        await store.add_child(call.data["name"])
        async_dispatcher_send(hass, SIGNAL_CHILDREN_UPDATED)
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_rename_child(call: ServiceCall):
        await store.rename_child(call.data["child_id"], call.data["new_name"])
        async_dispatcher_send(hass, SIGNAL_CHILDREN_UPDATED)
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_remove_child(call: ServiceCall):
        await store.remove_child(call.data["child_id"])
        async_dispatcher_send(hass, SIGNAL_CHILDREN_UPDATED)
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_add_task(call: ServiceCall):
        await store.add_task(
            title=call.data["title"],
            points=int(call.data["points"]),
            description=call.data.get("description", ""),
            due=call.data.get("due"),
            assigned_to=call.data.get("child_id"),
            repeat_days=call.data.get("repeat_days"),
            repeat_child_id=call.data.get("repeat_child_id"),
            repeat_child_ids=call.data.get("repeat_child_ids"),
            icon=call.data.get("icon"),
            persist_until_completed=call.data.get("persist_until_completed"),
        )
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_assign_task(call: ServiceCall):
        await store.assign_task(call.data["task_id"], call.data["child_id"])
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_set_task_status(call: ServiceCall):
        await store.set_task_status(call.data["task_id"], call.data["status"])
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_approve_task(call: ServiceCall):
        await store.approve_task(call.data["task_id"])
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_delete_task(call: ServiceCall):
        await store.delete_task(call.data["task_id"])
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_update_task(call: ServiceCall):
        await store.update_task(
            task_id=call.data["task_id"],
            title=call.data.get("title"),
            points=(int(call.data["points"]) if "points" in call.data else None),
            description=call.data.get("description"),
            due=call.data.get("due"),
            icon=call.data.get("icon"),
            persist_until_completed=call.data.get("persist_until_completed"),
        )
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_reset_points(call: ServiceCall):
        await store.reset_points(call.data.get("child_id"))
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_add_points(call: ServiceCall):
        await store.add_points(call.data["child_id"], int(call.data.get("points", 0)))
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_set_task_repeat(call: ServiceCall):
        await store.set_task_repeat(
            call.data["task_id"],
            call.data.get("repeat_days"),
            call.data.get("repeat_child_id"),
            call.data.get("repeat_child_ids"),
        )
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_set_task_icon(call: ServiceCall):
        await store.set_task_icon(call.data["task_id"], call.data.get("icon"))
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    # Shop services
    async def svc_add_shop_item(call: ServiceCall):
        await store.add_shop_item(
            title=call.data["title"],
            price=int(call.data["price"]),
            icon=call.data.get("icon"),
            image=call.data.get("image"),
            active=bool(call.data.get("active", True)),
            actions=call.data.get("actions"),
        )
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_update_shop_item(call: ServiceCall):
        await store.update_shop_item(
            item_id=call.data["item_id"],
            title=call.data.get("title"),
            price=(int(call.data["price"]) if "price" in call.data else None),
            icon=call.data.get("icon"),
            image=call.data.get("image"),
            active=call.data.get("active"),
            actions=call.data.get("actions"),
        )
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_delete_shop_item(call: ServiceCall):
        await store.delete_shop_item(call.data["item_id"])
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_buy_shop_item(call: ServiceCall):
        await store.buy_shop_item(call.data["child_id"], call.data["item_id"])
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async def svc_clear_shop_history(call: ServiceCall):
        # Optional: clear for specific child_id
        await store.clear_shop_history(call.data.get("child_id"))
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    hass.services.async_register(DOMAIN, "add_child", svc_add_child)
    hass.services.async_register(DOMAIN, "rename_child", svc_rename_child)
    hass.services.async_register(DOMAIN, "remove_child", svc_remove_child)
    hass.services.async_register(DOMAIN, "add_task", svc_add_task)
    hass.services.async_register(DOMAIN, "assign_task", svc_assign_task)
    hass.services.async_register(DOMAIN, "set_task_status", svc_set_task_status)
    hass.services.async_register(DOMAIN, "approve_task", svc_approve_task)
    hass.services.async_register(DOMAIN, "delete_task", svc_delete_task)
    hass.services.async_register(DOMAIN, "update_task", svc_update_task)
    hass.services.async_register(DOMAIN, "reset_points", svc_reset_points)
    hass.services.async_register(DOMAIN, "add_points", svc_add_points)
    hass.services.async_register(DOMAIN, "set_task_repeat", svc_set_task_repeat)
    hass.services.async_register(DOMAIN, "set_task_icon", svc_set_task_icon)
    # Shop
    hass.services.async_register(DOMAIN, "add_shop_item", svc_add_shop_item)
    hass.services.async_register(DOMAIN, "update_shop_item", svc_update_shop_item)
    hass.services.async_register(DOMAIN, "delete_shop_item", svc_delete_shop_item)
    hass.services.async_register(DOMAIN, "buy_shop_item", svc_buy_shop_item)
    hass.services.async_register(DOMAIN, "clear_shop_history", svc_clear_shop_history)
    # Backwards/alias
    hass.services.async_register(DOMAIN, "reset_shop_history", svc_clear_shop_history)

    # Upload images for shop items into /config/www/chores4kids
    async def svc_upload_shop_image(call: ServiceCall):
        import os, base64, re
        rel_dir = hass.config.path('www', 'chores4kids')
        os.makedirs(rel_dir, exist_ok=True)
        filename = call.data.get('filename') or 'upload.bin'
        # sanitize filename
        filename = re.sub(r'[^a-zA-Z0-9._-]+', '_', filename)
        data = call.data.get('data') or ''
        if ',' in data:
            data = data.split(',',1)[1]
        try:
            raw = base64.b64decode(data)
        except Exception:
            raise ValueError('invalid_base64')
        path = os.path.join(rel_dir, filename)
        def _write():
            with open(path, 'wb') as f:
                f.write(raw)
        await hass.async_add_executor_job(_write)
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    hass.services.async_register(DOMAIN, 'upload_shop_image', svc_upload_shop_image)

    async def svc_purge_orphans(call: ServiceCall):
        """Fjern forældreløse entiteter/devices fra tidligere versioner."""
        registry = er.async_get(hass)
        dev_registry = dr.async_get(hass)
        child_ids = {c.id for c in store.children}

        removed = []
        # Gå alle entiteter for denne config entry igennem
        reg_entries = er.async_entries_for_config_entry(registry, entry.entry_id)
        for e in reg_entries:
            if e.platform != Platform.SENSOR:
                continue
            uid = e.unique_id or ""
            if uid.startswith("chores4kids_points_"):
                suffix = uid.replace("chores4kids_points_", "")
                # hvis suffix ikke er nuværende child_id, fjern entiteten
                if suffix not in child_ids:
                    device_id = e.device_id
                    registry.async_remove(e.entity_id)
                    removed.append(e.entity_id)
                    if device_id:
                        device = dev_registry.async_get(device_id)
                        if device and not [x for x in registry.entities.values() if x.device_id == device_id]:
                            dev_registry.async_remove_device(device_id)
                    continue

                # Ellers: sørg for at entiteten er knyttet til korrekt device baseret på child_id
                desired_ident = (DOMAIN, f"child_{suffix}")
                desired = dev_registry.async_get_device(identifiers={desired_ident})
                if desired is None:
                    desired = dev_registry.async_get_or_create(
                        config_entry_id=entry.entry_id,
                        identifiers={desired_ident},
                        manufacturer="Chores4Kids",
                        model="Virtual Child",
                        name=f"Chores4Kids – {suffix}",
                    )
                if e.device_id != desired.id:
                    registry.async_update_entity(e.entity_id, device_id=desired.id)

        # Tving sensorer til at opdatere state efter oprydning
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)
        async_dispatcher_send(hass, SIGNAL_CHILDREN_UPDATED)

        # Fjern resterende tomme devices knyttet til denne integration
        for device in list(dev_registry.devices.values()):
            if entry.entry_id not in device.config_entries:
                continue
            has_entities = any(x.device_id == device.id for x in registry.entities.values())
            if not has_entities:
                dev_registry.async_remove_device(device.id)

    hass.services.async_register(DOMAIN, "purge_orphans", svc_purge_orphans)

    # Schedule midnight rollover and run once on startup
    async def _midnight_cb(now):
        await store.daily_rollover()
        async_dispatcher_send(hass, SIGNAL_DATA_UPDATED)

    async_track_time_change(hass, _midnight_cb, hour=0, minute=0, second=0)
    hass.async_create_task(store.daily_rollover())

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.pop(DOMAIN, None)
    return unload_ok
