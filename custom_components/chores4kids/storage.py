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
class Category:
    id: str
    name: str

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
    # Backwards compat: previous versions supported only a single child
    repeat_child_id: Optional[str] = None
    # New: allow multiple children for auto-assign on repeat days
    repeat_child_ids: list[str] = field(default_factory=list)
    # If true, carry the task forward to the next day until approved
    persist_until_completed: bool = False
    # Categories (ids)
    categories: list[str] = field(default_factory=list)
    # Flag indicating task was carried over from previous day (for visual indication)
    carried_over: bool = False
    # Timestamp (milliseconds since epoch) when child marked task as completed
    completed_ts: Optional[int] = None

class KidsChoresStore:
    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.children: List[Child] = []
        self.tasks: List[Task] = []
        self.categories: List[Category] = []
        self.items: List["ShopItem"] = []
        self.purchases: List["Purchase"] = []

    async def async_load(self):
        data = await self._store.async_load()
        if not data:
            return
        self.children = [Child(**c) for c in data.get("children", [])]
        self.categories = [Category(**c) for c in data.get("categories", [])]
        self.tasks = [Task(**t) for t in data.get("tasks", [])]
        # Optional keys for backwards compatibility
        self.items = [ShopItem(**i) for i in data.get("items", [])]
        self.purchases = [Purchase(**p) for p in data.get("purchases", [])]

    async def async_save(self):
        await self._store.async_save({
            "version": STORAGE_VERSION,
            "children": [asdict(c) for c in self.children],
            "tasks": [asdict(t) for t in self.tasks],
            "categories": [asdict(c) for c in self.categories],
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
    async def add_task(self, title: str, points: int, description: str = "", due: Optional[str] = None, assigned_to: Optional[str] = None, repeat_days: Optional[list[int]|list[str]] = None, repeat_child_id: Optional[str] = None, repeat_child_ids: Optional[list[str]] = None, icon: Optional[str] = None, persist_until_completed: Optional[bool] = None, categories: Optional[list[str]] = None) -> Task:
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
        if persist_until_completed is not None:
            t.persist_until_completed = bool(persist_until_completed)
        # optional assignment at creation
        if assigned_to:
            # validate child
            self._get_child(assigned_to)
            t.assigned_to = assigned_to
            t.status = STATUS_ASSIGNED
        # optional repeat config
        t.repeat_days = _norm_days(repeat_days)
        # Support multiple repeat children (and legacy single field)
        ids: list[str] = []
        try:
            if repeat_child_ids:
                for cid in repeat_child_ids:
                    if not cid:
                        continue
                    self._get_child(cid)
                    if cid not in ids:
                        ids.append(cid)
        except Exception:
            # if any invalid id, ignore it (but keep others)
            pass
        if repeat_child_id:
            # keep legacy field but also mirror into list if absent
            try:
                self._get_child(repeat_child_id)
                t.repeat_child_id = repeat_child_id
                if repeat_child_id not in ids:
                    ids.append(repeat_child_id)
            except Exception:
                pass
        t.repeat_child_ids = ids
        # categories (validate against known list, ignore unknown)
        try:
            cat_ids: list[str] = []
            for cid in (categories or []):
                if any(c.id == cid for c in self.categories):
                    if cid not in cat_ids:
                        cat_ids.append(cid)
            t.categories = cat_ids
        except Exception:
            t.categories = []

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
                persist_until_completed=getattr(t, "persist_until_completed", False),
                categories=list(getattr(t, "categories", []) or []),
            )
            # add_task persists; nothing else to do
            return
        # If the task is already assigned, reassign it to the new child
        t.assigned_to = child_id
        t.status = STATUS_ASSIGNED
        await self.async_save()

    async def set_task_status(self, task_id: str, status: str, completed_ts: Optional[int] = None):
        if status not in STATUSES:
            raise ValueError("invalid_status")
        t = self._get_task(task_id)
        t.status = status
        # Store completion timestamp if provided
        if completed_ts is not None:
            t.completed_ts = completed_ts
        # Clear timestamp if moving away from awaiting_approval
        elif status != STATUS_AWAITING:
            t.completed_ts = None
        # If a task is sent "back" to assigned, consider it (re)assigned today
        # so it appears as a current task for the child, regardless of original day.
        if status == STATUS_ASSIGNED:
            from datetime import datetime, timezone
            t.created = datetime.now(timezone.utc).isoformat()
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
        # Clear carried_over flag when task is approved
        t.carried_over = False
        # Keep completed_ts for historical record (don't clear it)
        child.points += int(t.points)
        await self.async_save()

    async def delete_task(self, task_id: str):
        self.tasks = [t for t in self.tasks if t.id != task_id]
        await self.async_save()

    async def set_task_repeat(self, task_id: str, repeat_days: Optional[list[int]|list[str]] = None, repeat_child_id: Optional[str] = None, repeat_child_ids: Optional[list[str]] = None):
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
        # normalize multi child ids
        ids: list[str] = []
        try:
            for cid in (repeat_child_ids or []):
                if not cid:
                    continue
                self._get_child(cid)
                if cid not in ids:
                    ids.append(cid)
        except Exception:
            pass
        # keep legacy single field for backward compat
        if repeat_child_id:
            try:
                self._get_child(repeat_child_id)
                if repeat_child_id not in ids:
                    ids.append(repeat_child_id)
                t.repeat_child_id = repeat_child_id
            except Exception:
                # clear legacy if invalid
                t.repeat_child_id = None
        else:
            t.repeat_child_id = None
        t.repeat_child_ids = ids
        await self.async_save()

    async def set_task_icon(self, task_id: str, icon: Optional[str] = None):
        t = self._get_task(task_id)
        t.icon = (icon or "").strip()
        await self.async_save()

    async def update_task(
        self,
        task_id: str,
        title: Optional[str] = None,
        points: Optional[int] = None,
        description: Optional[str] = None,
        due: Optional[str] = None,
        icon: Optional[str] = None,
        persist_until_completed: Optional[bool] = None,
        categories: Optional[list[str]] = None,
    ):
        """Update core editable fields on a task.

        Note: Repeat settings are managed via set_task_repeat.
        """
        t = self._get_task(task_id)
        if title is not None:
            t.title = str(title).strip()
        if points is not None:
            try:
                t.points = int(points)
            except Exception:
                # ignore invalid, keep previous value
                pass
        if description is not None:
            t.description = str(description).strip()
        if due is not None:
            t.due = str(due).strip() or None
        if icon is not None:
            t.icon = str(icon).strip()
        if persist_until_completed is not None:
            t.persist_until_completed = bool(persist_until_completed)
        if categories is not None:
            # set categories to validated list
            new_ids: list[str] = []
            try:
                for cid in (categories or []):
                    if any(c.id == cid for c in self.categories):
                        if cid not in new_ids:
                            new_ids.append(cid)
            except Exception:
                new_ids = []
            t.categories = new_ids
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
                # targets can be multiple children
                targets = list(getattr(t, "repeat_child_ids", []) or [])
                if not targets and getattr(t, "repeat_child_id", None):
                    targets = [t.repeat_child_id]
                templates.append({
                    "title": t.title,
                    "points": t.points,
                    "description": t.description,
                    "repeat_days": list(t.repeat_days),
                    "icon": t.icon,
                    "persist_until_completed": getattr(t, "persist_until_completed", False),
                    "categories": list(getattr(t, "categories", []) or []),
                    "targets": [x for x in targets if x],
                })

        # 1) Roll/clean older tasks with rules:
        #    - NEVER remove unassigned template tasks (assigned_to is empty)
        #    - KEEP tasks that are awaiting approval so parents can approve next day;
        #      also refresh their created date to today so repeat planner won't duplicate.
        #    - If persist_until_completed is true and task is not approved, KEEP and refresh created to today.
        kept: list[Task] = []
        for t in self.tasks:
            try:
                created = dt_util.parse_datetime(t.created)
                if created is None:
                    created = datetime.fromisoformat(t.created)
                created_local = dt_util.as_local(created)
                if created_local.date() < today and (t.assigned_to is not None and t.assigned_to != ""):
                    carry = False
                    if t.status == STATUS_AWAITING:
                        carry = True
                    elif getattr(t, "persist_until_completed", False) and t.status != STATUS_APPROVED:
                        carry = True
                    if carry:
                        # refresh created to today so exists_today() sees it
                        from datetime import datetime as _dt, timezone as _tz
                        t.created = _dt.now(_tz.utc).isoformat()
                        # Mark as carried over for frontend visual indication
                        t.carried_over = True
                    else:
                        continue  # drop non-persistent older tasks; keep templates forever
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
                targets = tpl.get("targets") or []
                for target in targets:
                    if target and not exists_today(tpl["title"], target):
                        await self.add_task(
                            title=tpl["title"],
                            points=tpl["points"],
                            description=tpl["description"],
                            assigned_to=target,
                            icon=tpl.get("icon") or "",
                            persist_until_completed=tpl.get("persist_until_completed", False),
                            categories=list(tpl.get("categories") or [])
                        )

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

    def _get_category(self, category_id: str) -> Category:
        for cat in self.categories:
            if cat.id == category_id:
                return cat
        raise ValueError("category_not_found")

    # --- Categories ---
    async def add_category(self, name: str) -> Category:
        cid = str(uuid4())
        cat = Category(id=cid, name=str(name).strip())
        self.categories.append(cat)
        await self.async_save()
        return cat

    async def rename_category(self, category_id: str, new_name: str) -> Category:
        cat = self._get_category(category_id)
        cat.name = str(new_name).strip()
        await self.async_save()
        return cat

    async def delete_category(self, category_id: str):
        # remove from tasks and from list
        self.categories = [c for c in self.categories if c.id != category_id]
        for t in self.tasks:
            try:
                if getattr(t, "categories", None):
                    t.categories = [cid for cid in t.categories if cid != category_id]
            except Exception:
                pass
        await self.async_save()

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
