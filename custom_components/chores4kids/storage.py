from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from uuid import uuid4
import os
import asyncio
import unicodedata
import re

from .const import STORAGE_KEY, STORAGE_VERSION

STATUS_ASSIGNED = "assigned"
STATUS_IN_PROGRESS = "in_progress"
STATUS_AWAITING = "awaiting_approval"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

STATUSES = {STATUS_ASSIGNED, STATUS_IN_PROGRESS, STATUS_AWAITING, STATUS_APPROVED, STATUS_REJECTED}


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_")
    return value.lower() or "child"

@dataclass
class Child:
    id: str
    name: str
    points: int = 0
    slug: str = ""

@dataclass
class Task:
    id: str
    title: str
    points: int
    assigned_to: Optional[str] = None
    status: str = STATUS_ASSIGNED
    description: str = ""
    created: str = ""
    due: Optional[str] = None
    approved_at: Optional[str] = None
    icon: str = ""
    # Weekly repetition: 0=Mon .. 6=Sun (local time). If set, a fresh task is auto-created and assigned on those days.
    repeat_days: list[int] = field(default_factory=list)
    repeat_child_id: Optional[str] = None

class KidsChoresStore:
    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.children: List[Child] = []
        self.tasks: List[Task] = []
        self.items: List["ShopItem"] = []
        self.purchases: List["Purchase"] = []

    async def async_load(self):
        data = await self._store.async_load()
        if not data:
            return
        self.children = [Child(**c) for c in data.get("children", [])]
        self.tasks = [Task(**t) for t in data.get("tasks", [])]
        # Optional keys for backwards compatibility
        self.items = [ShopItem(**i) for i in data.get("items", [])]
        self.purchases = [Purchase(**p) for p in data.get("purchases", [])]

    async def async_save(self):
        await self._store.async_save({
            "version": STORAGE_VERSION,
            "children": [asdict(c) for c in self.children],
            "tasks": [asdict(t) for t in self.tasks],
            "items": [asdict(i) for i in self.items],
            "purchases": [asdict(p) for p in self.purchases],
        })

    # --- Children ---
    async def add_child(self, name: str) -> Child:
        cid = str(uuid4())
        ch = Child(id=cid, name=name.strip(), points=0, slug=slugify(name))
        self.children.append(ch)
        await self.async_save()
        return ch

    async def rename_child(self, child_id: str, new_name: str):
        for c in self.children:
            if c.id == child_id:
                c.name = new_name.strip()
                c.slug = slugify(c.name)
                await self.async_save()
                return c
        raise ValueError("child_not_found")

    async def remove_child(self, child_id: str):
        self.children = [c for c in self.children if c.id != child_id]
        # Orphan tasks: keep but unassign
        for t in self.tasks:
            if t.assigned_to == child_id:
                t.assigned_to = None
        await self.async_save()

    # --- Tasks ---
    async def add_task(self, title: str, points: int, description: str = "", due: Optional[str] = None, assigned_to: Optional[str] = None, repeat_days: Optional[list[int]|list[str]] = None, repeat_child_id: Optional[str] = None, icon: Optional[str] = None) -> Task:
        tid = str(uuid4())
        # normalize repeat days to list[int]
        def _norm_days(days):
            if not days:
                return []
            key = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
            out = []
            for d in days:
                if isinstance(d, int):
                    if 0 <= d <= 6: out.append(int(d))
                else:
                    dd = str(d).strip().lower()[:3]
                    if dd in key: out.append(key[dd])
            return sorted(set(out))

        t = Task(id=tid, title=title.strip(), points=int(points), description=description.strip(), due=due, icon=(icon or "").strip())
        from datetime import datetime, timezone
        t.created = datetime.now(timezone.utc).isoformat()
        # optional assignment at creation
        if assigned_to:
            # validate child
            self._get_child(assigned_to)
            t.assigned_to = assigned_to
            t.status = STATUS_ASSIGNED
        # optional repeat config
        t.repeat_days = _norm_days(repeat_days)
        if repeat_child_id:
            self._get_child(repeat_child_id)
            t.repeat_child_id = repeat_child_id
        self.tasks.append(t)
        await self.async_save()
        return t

    async def assign_task(self, task_id: str, child_id: str):
        t = self._get_task(task_id)
        # validate child exists
        self._get_child(child_id)
        # Treat empty string or None as unassigned
        if not t.assigned_to:
            # Treat unassigned task as a template: spawn a new assigned copy
            # Keep the original in the unassigned list so it can be reused
            await self.add_task(
                title=t.title,
                points=t.points,
                description=t.description,
                due=t.due,
                assigned_to=child_id,
                icon=t.icon,
            )
            # add_task persists; nothing else to do
            return
        # If the task is already assigned, reassign it to the new child
        t.assigned_to = child_id
        t.status = STATUS_ASSIGNED
        await self.async_save()

    async def set_task_status(self, task_id: str, status: str):
        if status not in STATUSES:
            raise ValueError("invalid_status")
        t = self._get_task(task_id)
        t.status = status
        await self.async_save()

    async def approve_task(self, task_id: str):
        from datetime import datetime, timezone
        t = self._get_task(task_id)
        if not t.assigned_to:
            raise ValueError("task_not_assigned")
        child = self._get_child(t.assigned_to)
        if t.status != STATUS_AWAITING:
            # allow approving from other states but normalize
            pass
        t.status = STATUS_APPROVED
        t.approved_at = datetime.now(timezone.utc).isoformat()
        child.points += int(t.points)
        await self.async_save()

    async def delete_task(self, task_id: str):
        self.tasks = [t for t in self.tasks if t.id != task_id]
        await self.async_save()

    async def set_task_repeat(self, task_id: str, repeat_days: Optional[list[int]|list[str]] = None, repeat_child_id: Optional[str] = None):
        t = self._get_task(task_id)
        def _norm_days(days):
            if not days:
                return []
            key = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
            out = []
            for d in days:
                if isinstance(d, int):
                    if 0 <= d <= 6: out.append(int(d))
                else:
                    dd = str(d).strip().lower()[:3]
                    if dd in key: out.append(key[dd])
            return sorted(set(out))
        t.repeat_days = _norm_days(repeat_days)
        if repeat_child_id:
            self._get_child(repeat_child_id)
            t.repeat_child_id = repeat_child_id
        else:
            t.repeat_child_id = None
        await self.async_save()

    async def set_task_icon(self, task_id: str, icon: Optional[str] = None):
        t = self._get_task(task_id)
        t.icon = (icon or "").strip()
        await self.async_save()

    async def daily_rollover(self):
        """Midnight housekeeping: start fresh each day.

        - Remove ALL tasks from previous days (approved, awaiting, unfinished, etc.).
        - Then create today's repeated tasks based on the repeat templates captured
          from the existing tasks before cleanup.
        """
        from homeassistant.util import dt as dt_util
        from datetime import datetime

        now = dt_util.now()  # aware, local
        today = now.date()
        weekday = now.weekday()  # 0=Mon..6=Sun

        # Capture repeat templates BEFORE cleanup so we don't lose the plan
        templates = []
        for t in self.tasks:
            if t.repeat_days:
                templates.append({
                    "title": t.title,
                    "points": t.points,
                    "description": t.description,
                    "repeat_days": list(t.repeat_days),
                    "icon": t.icon,
                    "target": t.repeat_child_id or t.assigned_to or None,
                })

        # 1) Remove all tasks from previous days (but NEVER remove unassigned template tasks)
        kept: list[Task] = []
        for t in self.tasks:
            try:
                created = dt_util.parse_datetime(t.created)
                if created is None:
                    created = datetime.fromisoformat(t.created)
                created_local = dt_util.as_local(created)
                if created_local.date() < today and (t.assigned_to is not None and t.assigned_to != ""):
                    continue  # drop any assigned task older than today; keep templates forever
            except Exception:
                pass
            kept.append(t)
        self.tasks = kept

        # 2) Auto-create today's repeated tasks from captured templates
        def exists_today(title: str, child_id: str) -> bool:
            for t in self.tasks:
                if t.assigned_to == child_id and t.title == title:
                    try:
                        cd = dt_util.as_local(dt_util.parse_datetime(t.created)).date()
                    except Exception:
                        try:
                            cd = datetime.fromisoformat(t.created).date()
                        except Exception:
                            continue
                    if cd == today:
                        return True
            return False

        for tpl in templates:
            if tpl["repeat_days"] and weekday in tpl["repeat_days"]:
                target = tpl["target"]
                if target and not exists_today(tpl["title"], target):
                    await self.add_task(title=tpl["title"], points=tpl["points"], description=tpl["description"], assigned_to=target, icon=tpl.get("icon") or "")

        await self.async_save()

    async def reset_points(self, child_id: Optional[str] = None):
        if child_id:
            c = self._get_child(child_id)
            c.points = 0
        else:
            for c in self.children:
                c.points = 0
        await self.async_save()

    async def add_points(self, child_id: str, points: int):
        c = self._get_child(child_id)
        c.points += int(points)
        await self.async_save()

    # --- Shop API ---
    async def add_shop_item(self, title: str, price: int, icon: Optional[str] = None, image: Optional[str] = None, active: bool = True, actions: Optional[List[Dict[str, Any]]] = None):
        sid = str(uuid4())
        it = ShopItem(id=sid, title=str(title).strip(), price=int(price), icon=(icon or "").strip(), image=(image or "").strip(), active=bool(active))
        try:
            it.actions = self._normalize_actions(actions or [])
        except Exception:
            it.actions = []
        self.items.append(it)
        await self.async_save()
        return it

    async def update_shop_item(self, item_id: str, title: Optional[str] = None, price: Optional[int] = None, icon: Optional[str] = None, image: Optional[str] = None, active: Optional[bool] = None, actions: Optional[List[Dict[str, Any]]] = None):
        it = self._get_item(item_id)
        if title is not None:
            it.title = str(title).strip()
        if price is not None:
            it.price = int(price)
        if icon is not None:
            it.icon = str(icon).strip()
        if image is not None:
            it.image = str(image).strip()
        if active is not None:
            it.active = bool(active)
        if actions is not None:
            try:
                it.actions = self._normalize_actions(actions)
            except Exception:
                it.actions = []
        await self.async_save()
        return it

    async def delete_shop_item(self, item_id: str):
        # capture the item and its image before removing
        try:
            it = self._get_item(item_id)
        except Exception:
            it = None
        img = (getattr(it, "image", "") or "").strip() if it else ""

        self.items = [i for i in self.items if i.id != item_id]
        await self.async_save()

        # Best-effort cleanup of orphaned images stored under /local/chores4kids/
        try:
            if img and img.startswith("/local/"):
                # only delete if not used by another item or in purchases history
                used_by_item = any((getattr(x, "image", "") or "").strip() == img for x in self.items)
                used_by_purchase = any((getattr(p, "image", "") or "").strip() == img for p in self.purchases)
                if not used_by_item and not used_by_purchase:
                    rel = img[len("/local/"):].lstrip("/")  # e.g. chores4kids/xyz.jpg
                    abs_path = self.hass.config.path("www", *rel.split("/"))
                    def _rm():
                        if os.path.exists(abs_path):
                            try:
                                os.remove(abs_path)
                            except Exception:
                                pass
                    await self.hass.async_add_executor_job(_rm)
        except Exception:
            # Never fail deletion because of cleanup
            pass

    async def buy_shop_item(self, child_id: str, item_id: str):
        child = self._get_child(child_id)
        it = self._get_item(item_id)
        price = int(it.price)
        if child.points < price:
            raise ValueError("insufficient_points")
        child.points -= price
        from datetime import datetime, timezone
        pur = Purchase(
            id=str(uuid4()), child_id=child.id, item_id=it.id,
            title=it.title, price=price, icon=it.icon, image=getattr(it, 'image', ''),
            ts=datetime.now(timezone.utc).isoformat(), child_name=child.name
        )
        self.purchases.append(pur)
        await self.async_save()
        # Execute any configured actions asynchronously (non-blocking)
        try:
            actions = getattr(it, "actions", []) or []
            if actions:
                self.hass.async_create_task(self._run_actions(list(actions)))
        except Exception:
            pass
        return pur

    async def clear_shop_history(self, child_id: Optional[str] = None):
        """Clear purchase history. If child_id is provided, clear only entries for that child."""
        if child_id:
            # Validate child exists; raises if missing
            self._get_child(child_id)
            self.purchases = [p for p in self.purchases if p.child_id != child_id]
        else:
            self.purchases = []
        await self.async_save()

    # Helpers
    def _get_child(self, child_id: str) -> Child:
        for c in self.children:
            if c.id == child_id:
                return c
        raise ValueError("child_not_found")

    def _get_task(self, task_id: str) -> Task:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise ValueError("task_not_found")

    # shop helpers
    def _get_item(self, item_id: str):
        for i in self.items:
            if i.id == item_id:
                return i
        raise ValueError("item_not_found")

    # ---- Shop action engine ----
    def _normalize_actions(self, actions: Optional[List[Dict[str, Any]]]):
        """Normalize incoming action steps into a compact, safe representation.

        Supported formats in input:
        - {type:'delay', seconds: N}
        - {type:'entity_service', entity_id:'switch.xyz', op:'turn_on'}
        - {type:'service', domain:'switch', service:'turn_on', entity_id:'switch.xyz', data:{}}
        """
        out: List[Dict[str, Any]] = []
        if not actions:
            return out
        for step in actions:
            try:
                t = str(step.get("type") or step.get("kind") or "").lower()
                if t == "delay":
                    sec = int(step.get("seconds") or step.get("secs") or 0)
                    if sec > 0:
                        out.append({"type": "delay", "seconds": sec})
                    continue
                if t in ("entity_service", "service", "call_service"):
                    ent = str(step.get("entity_id") or "").strip()
                    if not ent:
                        continue
                    dom = ent.split(".", 1)[0]
                    op = str(step.get("op") or step.get("service") or "turn_on").strip()
                    data = step.get("data") or {}
                    out.append({
                        "type": "service",
                        "domain": step.get("domain") or dom,
                        "service": op,
                        "entity_id": ent,
                        "data": data,
                    })
            except Exception:
                continue
        return out

    async def _run_actions(self, actions: List[Dict[str, Any]]):
        for step in actions:
            try:
                if step.get("type") == "delay":
                    sec = int(step.get("seconds") or 0)
                    if sec > 0:
                        await asyncio.sleep(sec)
                elif step.get("type") == "service":
                    domain = step.get("domain")
                    service = step.get("service")
                    data = dict(step.get("data") or {})
                    ent = step.get("entity_id")
                    if ent:
                        data.setdefault("entity_id", ent)
                    if domain and service:
                        await self.hass.services.async_call(domain, service, data, blocking=False)
            except Exception:
                # Keep processing remaining steps
                continue

# ---- Point shop ----

@dataclass
class ShopItem:
    id: str
    title: str
    price: int
    icon: str = ""
    image: str = ""
    active: bool = True
    actions: List[Dict[str, Any]] = field(default_factory=list)

@dataclass
class Purchase:
    id: str
    child_id: str
    item_id: str
    title: str
    price: int
    icon: str = ""
    image: str = ""
    ts: str = ""

    # Optional denormalized for convenience (filled when saving)
    child_name: str = ""

# End of storage
