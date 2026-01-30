from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
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
    # Optional hex color (e.g. "#ff0000") used for UI chips. Empty means "no custom color".
    color: str = ""

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
    # Task scheduling mode for templates (unassigned tasks):
    # - "" or "repeat": use repeat_days
    # - "weekly": every Monday (repeat_days is forced to [0] for due calculations)
    # - "monthly": every 1st of the month
    schedule_mode: str = ""
    # If set, this task is an instance spawned from the unassigned repeat template task with this id.
    repeat_template_id: Optional[str] = None
    # Backwards compat: previous versions supported only a single child
    repeat_child_id: Optional[str] = None
    # New: allow multiple children for auto-assign on repeat days
    repeat_child_ids: list[str] = field(default_factory=list)
    # If true, carry the task forward to the next day until approved
    persist_until_completed: bool = False
    # If true, child can mark task done immediately (skip "start" step)
    quick_complete: bool = False
    # If true, task is automatically approved when completed (skip parent approval)
    skip_approval: bool = False
    # Categories (ids)
    categories: list[str] = field(default_factory=list)
    # Flag indicating task was carried over from previous day (for visual indication)
    carried_over: bool = False
    # Timestamp (milliseconds since epoch) when child marked task as completed
    completed_ts: Optional[int] = None
    # Early completion bonus: if completed at least N days before due date, award extra points
    # early_bonus_enabled allows toggling without losing configured values
    early_bonus_enabled: bool = False
    early_bonus_days: int = 0
    early_bonus_points: int = 0

    # If true, multiple children can be assigned the same task template and the first
    # child to start/complete it will "claim" it; other children's copies are marked as taken.
    fastest_wins: bool = False
    # Set on assigned copies spawned from an unassigned template with fastest_wins enabled.
    # Used to identify sibling copies (same template) to remove when claimed.
    fastest_wins_template_id: Optional[str] = None

    # If set, this fastest-wins task has been claimed by another child (or self).
    # Other children's copies remain visible but cannot be started.
    fastest_wins_claimed_by_child_id: Optional[str] = None
    fastest_wins_claimed_by_child_name: Optional[str] = None
    # Timestamp (milliseconds since epoch) when a fastest-wins task was claimed.
    # For non-winning siblings, this represents when the task was "lost/taken".
    fastest_wins_claimed_ts: Optional[int] = None

    # If true, unfinished task carried to next day is marked as overdue (red).
    mark_overdue: bool = True

class KidsChoresStore:
    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.children: List[Child] = []
        self.tasks: List[Task] = []
        self.categories: List[Category] = []
        self.items: List["ShopItem"] = []
        self.purchases: List["Purchase"] = []
        # Global UI settings (shared across users/devices via HA storage)
        self.ui_colors: Dict[str, str] = {}
        self.enable_points: bool = True
        self.confetti_enabled: bool = True

    async def async_load(self):
        data = await self._store.async_load()
        if not data:
            return
        self.children = [Child(**c) for c in data.get("children", [])]
        self.categories = [Category(**c) for c in data.get("categories", [])]
        # Migrate tasks: if early bonus was configured before the explicit toggle existed,
        # enable it automatically so behavior remains unchanged.
        raw_tasks = list(data.get("tasks", []) or [])
        migrated: list[Task] = []
        for t in raw_tasks:
            try:
                if isinstance(t, dict) and "early_bonus_enabled" not in t:
                    eb_days = int(t.get("early_bonus_days", 0) or 0)
                    eb_points = int(t.get("early_bonus_points", 0) or 0)
                    t["early_bonus_enabled"] = bool(eb_days > 0 and eb_points > 0)
            except Exception:
                # Best-effort migration; fall back to dataclass defaults
                pass
            migrated.append(Task(**t))
        self.tasks = migrated
        # Optional keys for backwards compatibility
        self.items = [ShopItem(**i) for i in data.get("items", [])]
        self.purchases = [Purchase(**p) for p in data.get("purchases", [])]
        try:
            raw_colors = data.get("ui_colors") or {}
            self.ui_colors = {str(k): str(v) for k, v in raw_colors.items() if v is not None}
        except Exception:
            self.ui_colors = {}

        try:
            self.enable_points = bool(data.get("enable_points", True))
        except Exception:
            self.enable_points = True

        try:
            self.confetti_enabled = bool(data.get("confetti_enabled", True))
        except Exception:
            self.confetti_enabled = True

    async def async_save(self):
        await self._store.async_save({
            "version": STORAGE_VERSION,
            "children": [asdict(c) for c in self.children],
            "tasks": [asdict(t) for t in self.tasks],
            "categories": [asdict(c) for c in self.categories],
            "items": [asdict(i) for i in self.items],
            "purchases": [asdict(p) for p in self.purchases],
            "ui_colors": dict(self.ui_colors or {}),
            "enable_points": bool(getattr(self, "enable_points", True)),
            "confetti_enabled": bool(getattr(self, "confetti_enabled", True)),
        })

    async def set_ui_colors(
        self,
        start_task_bg: Optional[str] = None,
        complete_task_bg: Optional[str] = None,
        kid_points_bg: Optional[str] = None,
        start_task_text: Optional[str] = None,
        complete_task_text: Optional[str] = None,
        kid_points_text: Optional[str] = None,
        task_done_bg: Optional[str] = None,
        task_done_text: Optional[str] = None,
        task_points_bg: Optional[str] = None,
        task_points_text: Optional[str] = None,
        kid_task_title_size: Optional[str] = None,
        kid_task_points_size: Optional[str] = None,
        kid_task_button_size: Optional[str] = None,
        enable_points: Optional[bool] = None,
        confetti_enabled: Optional[bool] = None,
    ) -> Dict[str, str]:
        """Set global UI colors. Empty string clears a value."""
        def _set(key: str, value: Optional[str]):
            if value is None:
                return
            v = str(value).strip()
            if not v:
                # clear
                self.ui_colors.pop(key, None)
            else:
                self.ui_colors[key] = v

        _set("start_task_bg", start_task_bg)
        _set("complete_task_bg", complete_task_bg)
        _set("kid_points_bg", kid_points_bg)
        _set("start_task_text", start_task_text)
        _set("complete_task_text", complete_task_text)
        _set("kid_points_text", kid_points_text)
        _set("task_done_bg", task_done_bg)
        _set("task_done_text", task_done_text)
        _set("task_points_bg", task_points_bg)
        _set("task_points_text", task_points_text)

        # Kid card font sizes (CSS values, typically px)
        _set("kid_task_title_size", kid_task_title_size)
        _set("kid_task_points_size", kid_task_points_size)
        _set("kid_task_button_size", kid_task_button_size)

        if enable_points is not None:
            self.enable_points = bool(enable_points)

        if confetti_enabled is not None:
            self.confetti_enabled = bool(confetti_enabled)
        await self.async_save()
        return dict(self.ui_colors)

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
    async def add_task(
        self,
        title: str,
        points: int,
        description: str = "",
        due: Optional[str] = None,
        assigned_to: Optional[str] = None,
        repeat_days: Optional[list[int] | list[str]] = None,
        repeat_child_id: Optional[str] = None,
        repeat_child_ids: Optional[list[str]] = None,
        repeat_template_id: Optional[str] = None,
        icon: Optional[str] = None,
        persist_until_completed: Optional[bool] = None,
        quick_complete: Optional[bool] = None,
        skip_approval: Optional[bool] = None,
        categories: Optional[list[str]] = None,
        early_bonus_enabled: Optional[bool] = None,
        early_bonus_days: Optional[int] = None,
        early_bonus_points: Optional[int] = None,
        fastest_wins: Optional[bool] = None,
        fastest_wins_template_id: Optional[str] = None,
        schedule_mode: Optional[str] = None,
        mark_overdue: Optional[bool] = None,
    ) -> Task:
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

        t = Task(
            id=tid,
            title=title.strip(),
            points=int(points),
            description=description.strip(),
            due=due,
            icon=(icon or "").strip(),
            repeat_template_id=(repeat_template_id or None),
        )
        from datetime import datetime, timezone
        t.created = datetime.now(timezone.utc).isoformat()

        if early_bonus_enabled is not None:
            t.early_bonus_enabled = bool(early_bonus_enabled)
        if early_bonus_days is not None:
            try:
                t.early_bonus_days = max(0, int(early_bonus_days))
            except Exception:
                t.early_bonus_days = 0
        if early_bonus_points is not None:
            try:
                t.early_bonus_points = max(0, int(early_bonus_points))
            except Exception:
                t.early_bonus_points = 0

        if fastest_wins is not None:
            t.fastest_wins = bool(fastest_wins)
        if fastest_wins_template_id is not None:
            t.fastest_wins_template_id = str(fastest_wins_template_id).strip() or None
        if mark_overdue is not None:
            t.mark_overdue = bool(mark_overdue)
        # Backwards compat: if caller sets days+points but doesn't pass explicit toggle, auto-enable.
        if early_bonus_enabled is None:
            try:
                t.early_bonus_enabled = bool(int(getattr(t, "early_bonus_days", 0) or 0) > 0 and int(getattr(t, "early_bonus_points", 0) or 0) > 0)
            except Exception:
                t.early_bonus_enabled = False
        if persist_until_completed is not None:
            t.persist_until_completed = bool(persist_until_completed)
        if quick_complete is not None:
            t.quick_complete = bool(quick_complete)
        if skip_approval is not None:
            t.skip_approval = bool(skip_approval)
        # optional assignment at creation
        if assigned_to:
            # validate child
            self._get_child(assigned_to)
            t.assigned_to = assigned_to
            t.status = STATUS_ASSIGNED
        # scheduling mode
        try:
            mode = str(schedule_mode or "").strip().lower()
        except Exception:
            mode = ""
        if mode not in ("", "repeat", "weekly", "monthly"):
            mode = ""
        t.schedule_mode = mode

        # optional schedule/repeat config
        if t.schedule_mode == "weekly":
            t.repeat_days = [0]
        elif t.schedule_mode == "monthly":
            t.repeat_days = []
        else:
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

        # Enforce mutual exclusion: weekly/monthly templates cannot carry unfinished.
        try:
            is_template = not (getattr(t, "assigned_to", None) and str(getattr(t, "assigned_to", "")).strip())
        except Exception:
            is_template = False
        if is_template and t.schedule_mode in ("weekly", "monthly"):
            t.persist_until_completed = False
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

        # If this is an unassigned repeat template with early-bonus enabled, create upcoming
        # assigned instance(s) immediately, using repeat_days as the deadline.
        try:
            await self._maybe_spawn_repeat_bonus_instances(t)
        except Exception:
            pass

        await self.async_save()
        return t

    def _repeat_bonus_active(self, t: Task) -> bool:
        try:
            return bool(getattr(t, "early_bonus_enabled", False)) and int(getattr(t, "early_bonus_days", 0) or 0) > 0 and int(getattr(t, "early_bonus_points", 0) or 0) > 0
        except Exception:
            return False

    def _repeat_targets_for_template(self, t: Task) -> list[str]:
        targets: list[str] = []
        try:
            for cid in (getattr(t, "repeat_child_ids", []) or []):
                if cid and cid not in targets:
                    targets.append(cid)
        except Exception:
            pass
        try:
            cid = getattr(t, "repeat_child_id", None)
            if cid and cid not in targets:
                targets.append(cid)
        except Exception:
            pass
        return targets

    def _active_repeat_instance_exists(self, template_id: str, child_id: str) -> bool:
        for x in self.tasks:
            if x.assigned_to != child_id:
                continue
            if getattr(x, "repeat_template_id", None) != template_id:
                continue
            if x.status in (STATUS_ASSIGNED, STATUS_IN_PROGRESS, STATUS_AWAITING):
                return True
        return False

    def _next_repeat_due_iso(self, base_date, repeat_days: list[int], include_today: bool = True) -> Optional[str]:
        try:
            from datetime import timedelta
            if not repeat_days:
                return None
            wd = int(base_date.weekday())
            best = None
            for d in sorted(set(int(x) for x in repeat_days if 0 <= int(x) <= 6)):
                delta = (d - wd) % 7
                if not include_today and delta == 0:
                    delta = 7
                if best is None or delta < best:
                    best = delta
            if best is None:
                return None
            return (base_date + timedelta(days=int(best))).isoformat()
        except Exception:
            return None

    def _next_monthly_due_iso(self, base_date, include_today: bool = True) -> Optional[str]:
        try:
            from datetime import date, timedelta

            if not isinstance(base_date, date):
                return None

            # base_date is a date (local). We return ISO date string for next 1st.
            if include_today and base_date.day == 1:
                return base_date.isoformat()

            year = int(base_date.year)
            month = int(base_date.month)
            # Jump to first of next month
            if month == 12:
                year += 1
                month = 1
            else:
                month += 1
            return date(year, month, 1).isoformat()
        except Exception:
            return None

    async def _maybe_spawn_repeat_bonus_instances(self, template: Task):
        """For repeat templates with early-bonus enabled, ensure each target child has one upcoming instance.

        Deadline is derived from template.repeat_days (next occurrence), not from template.due.
        """
        # Only for unassigned templates
        if template.assigned_to:
            return
        # Determine schedule for template
        try:
            mode = str(getattr(template, "schedule_mode", "") or "").strip().lower()
        except Exception:
            mode = ""

        # Backwards compat: if mode is empty but repeat_days exists, treat as repeat.
        has_repeat_days = bool(getattr(template, "repeat_days", None))
        if mode in ("", "repeat") and not has_repeat_days:
            return
        if not self._repeat_bonus_active(template):
            return
        targets = self._repeat_targets_for_template(template)
        if not targets:
            return

        from homeassistant.util import dt as dt_util
        from datetime import datetime, timezone
        today = dt_util.now().date()  # local
        if mode == "monthly":
            due_iso = self._next_monthly_due_iso(today, include_today=True)
        else:
            # repeat + weekly both use repeat-days based next occurrence
            rdays = list(getattr(template, "repeat_days", []) or [])
            if mode == "weekly":
                rdays = [0]
            due_iso = self._next_repeat_due_iso(today, rdays, include_today=True)
        if not due_iso:
            return

        for cid in targets:
            try:
                self._get_child(cid)
            except Exception:
                continue
            if self._active_repeat_instance_exists(template.id, cid):
                continue

            inst = Task(
                id=str(uuid4()),
                title=template.title,
                points=int(template.points),
                assigned_to=cid,
                status=STATUS_ASSIGNED,
                description=getattr(template, "description", "") or "",
                created=datetime.now(timezone.utc).isoformat(),
                due=due_iso,
                icon=getattr(template, "icon", "") or "",
                repeat_template_id=template.id,
                persist_until_completed=True,
                quick_complete=bool(getattr(template, "quick_complete", False)),
                skip_approval=bool(getattr(template, "skip_approval", False)),
                categories=list(getattr(template, "categories", []) or []),
                mark_overdue=bool(getattr(template, "mark_overdue", True)),
            )
            inst.early_bonus_enabled = bool(getattr(template, "early_bonus_enabled", False))
            inst.early_bonus_days = int(getattr(template, "early_bonus_days", 0) or 0)
            inst.early_bonus_points = int(getattr(template, "early_bonus_points", 0) or 0)
            self.tasks.append(inst)

    async def assign_task(self, task_id: str, child_id: str):
        t = self._get_task(task_id)
        # validate child exists
        self._get_child(child_id)
        # Treat empty string or None as unassigned
        if not t.assigned_to:
            # Treat unassigned task as a template: spawn a new assigned copy
            # Keep the original in the unassigned list so it can be reused
            repeat_template_id: Optional[str] = None
            try:
                mode = str(getattr(t, "schedule_mode", "") or "").strip().lower()
            except Exception:
                mode = ""
            try:
                if mode in ("weekly", "monthly", "repeat") or getattr(t, "repeat_days", None):
                    # If the template is scheduled, link spawned copy back to the template
                    # so updates to the template can be propagated to active assigned instances.
                    repeat_template_id = t.id
            except Exception:
                repeat_template_id = None
            await self.add_task(
                title=t.title,
                points=t.points,
                description=t.description,
                due=t.due,
                assigned_to=child_id,
                repeat_template_id=repeat_template_id,
                icon=t.icon,
                persist_until_completed=getattr(t, "persist_until_completed", False),
                quick_complete=getattr(t, "quick_complete", False),
                skip_approval=getattr(t, "skip_approval", False),
                categories=list(getattr(t, "categories", []) or []),
                early_bonus_enabled=getattr(t, "early_bonus_enabled", False),
                early_bonus_days=getattr(t, "early_bonus_days", 0),
                early_bonus_points=getattr(t, "early_bonus_points", 0),
                fastest_wins=bool(getattr(t, "fastest_wins", False)),
                fastest_wins_template_id=(t.id if bool(getattr(t, "fastest_wins", False)) else None),
                schedule_mode=getattr(t, "schedule_mode", None),
                mark_overdue=getattr(t, "mark_overdue", True),
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

        def _local_created_date(task: Task):
            from homeassistant.util import dt as dt_util
            from datetime import datetime

            created_raw = getattr(task, "created", None)
            if not created_raw:
                return None
            try:
                dt = dt_util.parse_datetime(str(created_raw))
                if dt is None:
                    dt = datetime.fromisoformat(str(created_raw))
                return dt_util.as_local(dt).date()
            except Exception:
                return None

        def _claim_fastest_wins_if_needed(task: Task, next_status: str) -> bool:
            # Claim when moving away from 'assigned' (start or one-tap completion).
            if getattr(task, "status", None) != STATUS_ASSIGNED:
                return False
            if next_status not in (STATUS_IN_PROGRESS, STATUS_AWAITING):
                return False
            if not bool(getattr(task, "fastest_wins", False)):
                return False
            day = _local_created_date(task)
            if day is None:
                return False

            tpl_id = getattr(task, "fastest_wins_template_id", None)
            # Fallback grouping for tasks created as separate copies (e.g. repeat/multi-assign flows)
            # where no template id was recorded.
            sig = None
            if not tpl_id:
                sig = (
                    str(getattr(task, "title", "") or "").strip().lower(),
                    int(getattr(task, "points", 0) or 0),
                    str(getattr(task, "due", "") or "").strip(),
                )

            siblings: list[Task] = []
            for other in self.tasks:
                if other.id == task.id:
                    continue
                if _local_created_date(other) != day:
                    continue
                # Only consider assigned copies (templates are unassigned)
                if not getattr(other, "assigned_to", None):
                    continue
                if not bool(getattr(other, "fastest_wins", False)):
                    continue
                if tpl_id:
                    if getattr(other, "fastest_wins_template_id", None) != tpl_id:
                        continue
                else:
                    # Only group with other non-template-linked copies that match signature.
                    if getattr(other, "fastest_wins_template_id", None):
                        continue
                    other_sig = (
                        str(getattr(other, "title", "") or "").strip().lower(),
                        int(getattr(other, "points", 0) or 0),
                        str(getattr(other, "due", "") or "").strip(),
                    )
                    if other_sig != sig:
                        continue
                siblings.append(other)

            # Determine if the task has already been claimed by someone else.
            existing_claim_id: Optional[str] = None
            existing_claim_name: Optional[str] = None
            existing_claim_ts: Optional[int] = None
            for o in siblings:
                cid = getattr(o, "fastest_wins_claimed_by_child_id", None) or None
                cname = getattr(o, "fastest_wins_claimed_by_child_name", None) or None
                cts = getattr(o, "fastest_wins_claimed_ts", None)
                if cts and not existing_claim_ts:
                    try:
                        existing_claim_ts = int(cts)
                    except Exception:
                        existing_claim_ts = None
                if cid:
                    existing_claim_id = cid
                    existing_claim_name = cname
                    break
                # Backwards-compat: if a sibling already progressed (older versions), treat it as claimed.
                if getattr(o, "status", None) != STATUS_ASSIGNED and getattr(o, "assigned_to", None):
                    existing_claim_id = getattr(o, "assigned_to", None)
                    try:
                        existing_claim_name = self._get_child(existing_claim_id).name
                    except Exception:
                        existing_claim_name = None
                    break

            # If already claimed by another child, mark this task and block the transition.
            my_child_id = getattr(task, "assigned_to", None)
            if existing_claim_id and my_child_id and existing_claim_id != my_child_id:
                task.fastest_wins_claimed_by_child_id = existing_claim_id
                task.fastest_wins_claimed_by_child_name = existing_claim_name
                task.fastest_wins_claimed_ts = existing_claim_ts
                return True

            # Not claimed yet -> this child claims it; mark siblings as taken.
            if not my_child_id:
                return False
            try:
                my_child_name = self._get_child(my_child_id).name
            except Exception:
                my_child_name = None
            task.fastest_wins_claimed_by_child_id = my_child_id
            task.fastest_wins_claimed_by_child_name = my_child_name
            claim_ts = existing_claim_ts
            if not claim_ts:
                claim_ts = int(dt_util.utcnow().timestamp() * 1000)
            task.fastest_wins_claimed_ts = claim_ts
            for o in siblings:
                o.fastest_wins_claimed_by_child_id = my_child_id
                o.fastest_wins_claimed_by_child_name = my_child_name
                o.fastest_wins_claimed_ts = claim_ts
            return False
        # Store completion timestamp if provided
        if completed_ts is not None:
            t.completed_ts = completed_ts

        # Fastest-wins: when a child starts or completes, mark other children's copies as taken
        # and block late claimers.
        blocked = _claim_fastest_wins_if_needed(t, status)
        if blocked:
            await self.async_save()
            raise ValueError("task_already_claimed")

        # If the task is configured to skip approval, auto-approve when it would
        # normally be sent for parent approval.
        if status == STATUS_AWAITING and getattr(t, "skip_approval", False):
            # Set status to awaiting first (approve_task allows other states too,
            # but this keeps the flow consistent with UI expectations).
            t.status = STATUS_AWAITING
            await self.approve_task(task_id)
            return

        t.status = status
        # Clear timestamp if moving away from awaiting_approval and caller didn't
        # provide a completion timestamp.
        if completed_ts is None and status != STATUS_AWAITING:
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
        bonus = 0
        try:
            eb_enabled = bool(getattr(t, "early_bonus_enabled", False))
            eb_days = int(getattr(t, "early_bonus_days", 0) or 0)
            eb_points = int(getattr(t, "early_bonus_points", 0) or 0)
            due_raw = getattr(t, "due", None)
            comp_ts = getattr(t, "completed_ts", None)

            if eb_enabled and eb_days > 0 and eb_points > 0 and due_raw and comp_ts:
                from datetime import timedelta
                from homeassistant.util import dt as dt_util

                # Parse due as datetime or date (YYYY-MM-DD)
                due_dt = dt_util.parse_datetime(str(due_raw))
                due_date = None
                if due_dt is not None:
                    due_date = dt_util.as_local(due_dt).date()
                else:
                    due_d = dt_util.parse_date(str(due_raw))
                    if due_d is not None:
                        due_date = due_d

                if due_date is not None:
                    completed_dt = dt_util.as_local(dt_util.utc_from_timestamp(int(comp_ts) / 1000.0))
                    completed_date = completed_dt.date()
                    threshold_date = due_date - timedelta(days=eb_days)
                    if completed_date <= threshold_date:
                        bonus = eb_points
        except Exception:
            bonus = 0

        child.points += int(t.points) + int(bonus)

        # If this task was spawned from a repeat template, create the next upcoming instance
        # right away (so the child card shows the next deadline without waiting for midnight).
        try:
            tpl_id = getattr(t, "repeat_template_id", None)
            if tpl_id and t.assigned_to:
                template = None
                for x in self.tasks:
                    if x.id == tpl_id and (not x.assigned_to):
                        template = x
                        break
                if template and getattr(template, "repeat_days", None) and self._repeat_bonus_active(template):
                    from homeassistant.util import dt as dt_util
                    from datetime import datetime as _dt, timezone as _tz
                    # Advance based on the instance deadline (t.due), not "today", so multi-weekday
                    # schedules chain correctly.
                    base = dt_util.now().date()
                    try:
                        due_raw = getattr(t, "due", None)
                        if due_raw:
                            due_dt = dt_util.parse_datetime(str(due_raw))
                            if due_dt is not None:
                                base = dt_util.as_local(due_dt).date()
                            else:
                                due_d = dt_util.parse_date(str(due_raw))
                                if due_d is not None:
                                    base = due_d
                    except Exception:
                        base = dt_util.now().date()
                    next_due = self._next_repeat_due_iso(base, list(template.repeat_days), include_today=False)
                    if next_due and not self._active_repeat_instance_exists(template.id, t.assigned_to):
                        inst = Task(
                            id=str(uuid4()),
                            title=template.title,
                            points=int(template.points),
                            assigned_to=t.assigned_to,
                            status=STATUS_ASSIGNED,
                            description=getattr(template, "description", "") or "",
                            created=_dt.now(_tz.utc).isoformat(),
                            due=next_due,
                            icon=getattr(template, "icon", "") or "",
                            repeat_template_id=template.id,
                            persist_until_completed=True,
                            quick_complete=bool(getattr(template, "quick_complete", False)),
                            skip_approval=bool(getattr(template, "skip_approval", False)),
                            categories=list(getattr(template, "categories", []) or []),
                            mark_overdue=bool(getattr(template, "mark_overdue", True)),
                        )
                        inst.early_bonus_enabled = bool(getattr(template, "early_bonus_enabled", False))
                        inst.early_bonus_days = int(getattr(template, "early_bonus_days", 0) or 0)
                        inst.early_bonus_points = int(getattr(template, "early_bonus_points", 0) or 0)
                        self.tasks.append(inst)
        except Exception:
            pass
        await self.async_save()

    async def delete_task(self, task_id: str):
        self.tasks = [t for t in self.tasks if t.id != task_id]
        await self.async_save()

    async def set_task_repeat(
        self,
        task_id: str,
        repeat_days: Optional[list[int] | list[str]] = None,
        repeat_child_id: Optional[str] = None,
        repeat_child_ids: Optional[list[str]] = None,
        schedule_mode: Optional[str] = None,
    ):
        t = self._get_task(task_id)

        # Normalize schedule mode
        if schedule_mode is not None:
            try:
                mode = str(schedule_mode or "").strip().lower()
            except Exception:
                mode = ""
            if mode not in ("", "repeat", "weekly", "monthly"):
                mode = ""
            t.schedule_mode = mode

        def _norm_days(days):
            if not days:
                return []
            key = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
            out = []
            for d in days:
                if isinstance(d, int):
                    if 0 <= d <= 6:
                        out.append(int(d))
                else:
                    dd = str(d).strip().lower()[:3]
                    if dd in key:
                        out.append(key[dd])
            return sorted(set(out))

        # weekly/monthly override repeat_days
        try:
            mode = str(getattr(t, "schedule_mode", "") or "").strip().lower()
        except Exception:
            mode = ""
        if mode == "weekly":
            t.repeat_days = [0]
        elif mode == "monthly":
            t.repeat_days = []
        else:
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

        # Enforce mutual exclusion: weekly/monthly templates cannot carry unfinished.
        try:
            is_template = (t.assigned_to is None) or (str(t.assigned_to).strip() == "")
        except Exception:
            is_template = False
        if is_template and mode in ("weekly", "monthly"):
            t.persist_until_completed = False

        # If this is a template and early-bonus repeat is active, ensure instances exist.
        try:
            await self._maybe_spawn_repeat_bonus_instances(t)
        except Exception:
            pass
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
        early_bonus_enabled: Optional[bool] = None,
        early_bonus_days: Optional[int] = None,
        early_bonus_points: Optional[int] = None,
        icon: Optional[str] = None,
        persist_until_completed: Optional[bool] = None,
        quick_complete: Optional[bool] = None,
        skip_approval: Optional[bool] = None,
        categories: Optional[list[str]] = None,
        fastest_wins: Optional[bool] = None,
        mark_overdue: Optional[bool] = None,
    ):
        """Update core editable fields on a task.

        Note: Repeat settings are managed via set_task_repeat.
        """
        t = self._get_task(task_id)
        is_template = False
        try:
            is_template = (t.assigned_to is None) or (str(t.assigned_to).strip() == "")
        except Exception:
            is_template = False
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
        if early_bonus_enabled is not None:
            t.early_bonus_enabled = bool(early_bonus_enabled)
        if early_bonus_days is not None:
            try:
                t.early_bonus_days = max(0, int(early_bonus_days))
            except Exception:
                t.early_bonus_days = getattr(t, "early_bonus_days", 0) or 0
        if early_bonus_points is not None:
            try:
                t.early_bonus_points = max(0, int(early_bonus_points))
            except Exception:
                t.early_bonus_points = getattr(t, "early_bonus_points", 0) or 0
        if icon is not None:
            t.icon = str(icon).strip()
        if persist_until_completed is not None:
            t.persist_until_completed = bool(persist_until_completed)
        if quick_complete is not None:
            t.quick_complete = bool(quick_complete)
        if skip_approval is not None:
            t.skip_approval = bool(skip_approval)
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

        if fastest_wins is not None:
            t.fastest_wins = bool(fastest_wins)
        if mark_overdue is not None:
            t.mark_overdue = bool(mark_overdue)

        # Keep already spawned repeat instances in sync with the template.
        # This addresses the UX expectation that editing a task under "Tasks" updates the
        # already assigned tasks that were created from it.
        if is_template:
            try:
                active_statuses = {STATUS_ASSIGNED, STATUS_IN_PROGRESS, STATUS_AWAITING, STATUS_REJECTED}
                for inst in self.tasks:
                    if not getattr(inst, "assigned_to", None):
                        continue
                    if getattr(inst, "repeat_template_id", None) != t.id:
                        continue
                    if getattr(inst, "status", None) not in active_statuses:
                        # Keep approved history immutable
                        continue

                    inst.title = t.title
                    inst.points = int(t.points)
                    inst.description = getattr(t, "description", "") or ""
                    inst.icon = getattr(t, "icon", "") or ""
                    inst.categories = list(getattr(t, "categories", []) or [])
                    inst.early_bonus_enabled = bool(getattr(t, "early_bonus_enabled", False))
                    inst.early_bonus_days = int(getattr(t, "early_bonus_days", 0) or 0)
                    inst.early_bonus_points = int(getattr(t, "early_bonus_points", 0) or 0)
                    inst.persist_until_completed = bool(getattr(t, "persist_until_completed", False))
                    inst.quick_complete = bool(getattr(t, "quick_complete", False))
                    inst.skip_approval = bool(getattr(t, "skip_approval", False))
                    inst.mark_overdue = bool(getattr(t, "mark_overdue", True))
            except Exception:
                pass

        # If this is a template and early-bonus repeat is active, ensure instances exist.
        try:
            await self._maybe_spawn_repeat_bonus_instances(t)
        except Exception:
            pass
        await self.async_save()

    async def daily_rollover(self):
        """Midnight housekeeping: start fresh each day.

                - Remove tasks from previous days unless explicitly configured to carry.
        - Then create today's repeated tasks based on the repeat templates captured
          from the existing tasks before cleanup.
        """
        from homeassistant.util import dt as dt_util
        from datetime import datetime

        now = dt_util.now()  # aware, local
        today = now.date()
        weekday = now.weekday()  # 0=Mon..6=Sun

        # Capture scheduled templates BEFORE cleanup so we don't lose the plan
        templates = []
        for t in self.tasks:
            try:
                mode = str(getattr(t, "schedule_mode", "") or "").strip().lower()
            except Exception:
                mode = ""
            # Backwards compat: if no mode but repeat_days exists, treat as repeat.
            is_scheduled = bool(getattr(t, "repeat_days", None)) or (mode in ("weekly", "monthly", "repeat"))
            if not is_scheduled:
                continue

            # targets can be multiple children
            targets = list(getattr(t, "repeat_child_ids", []) or [])
            if not targets and getattr(t, "repeat_child_id", None):
                targets = [t.repeat_child_id]
            templates.append({
                "id": t.id,
                "title": t.title,
                "points": t.points,
                "description": t.description,
                "repeat_days": list(getattr(t, "repeat_days", []) or []),
                "schedule_mode": mode,
                "icon": t.icon,
                "due": getattr(t, "due", None),
                "early_bonus_enabled": getattr(t, "early_bonus_enabled", False),
                "early_bonus_days": getattr(t, "early_bonus_days", 0),
                "early_bonus_points": getattr(t, "early_bonus_points", 0),
                "persist_until_completed": getattr(t, "persist_until_completed", False),
                "quick_complete": getattr(t, "quick_complete", False),
                "skip_approval": getattr(t, "skip_approval", False),
                "categories": list(getattr(t, "categories", []) or []),
                "targets": [x for x in targets if x],
                "mark_overdue": getattr(t, "mark_overdue", True),
            })

        def _local_created_date(task: Task):
            created_raw = getattr(task, "created", None)
            if not created_raw:
                return None
            try:
                created_dt = dt_util.parse_datetime(str(created_raw))
                if created_dt is None:
                    created_dt = datetime.fromisoformat(str(created_raw))
                return dt_util.as_local(created_dt).date()
            except Exception:
                return None

        # 1) Roll/clean older tasks with rules:
        #    - NEVER remove unassigned template tasks (assigned_to is empty)
        #    - Only carry tasks forward when persist_until_completed is true and task is not approved.
        kept: list[Task] = []
        for t in self.tasks:
            is_template = not (getattr(t, "assigned_to", None) and str(getattr(t, "assigned_to", "")).strip())
            if is_template:
                kept.append(t)
                continue

            created_date = _local_created_date(t)
            # If created is missing/invalid, treat it as "old" so it doesn't stick around forever.
            is_older = (created_date is None) or (created_date < today)
            if is_older:
                if bool(getattr(t, "persist_until_completed", False)) and getattr(t, "status", None) != STATUS_APPROVED:
                    from datetime import datetime as _dt, timezone as _tz
                    t.created = _dt.now(_tz.utc).isoformat()
                    t.carried_over = True
                    kept.append(t)
                else:
                    continue
            else:
                kept.append(t)
        self.tasks = kept

        # 2) Auto-create today's repeated tasks from captured templates
        # Prefer using repeat_template_id to detect existing active instances (more robust than title/date).
        def _active_instance_exists(template_id: str, child_id: str) -> bool:
            try:
                if template_id and self._active_repeat_instance_exists(template_id, child_id):
                    return True
            except Exception:
                pass
            return False

        for tpl in templates:
            rdays = tpl.get("repeat_days") or []
            try:
                mode = str(tpl.get("schedule_mode") or "").strip().lower()
            except Exception:
                mode = ""

            # Backwards compat: if no mode but repeat_days exists, treat as repeat.
            if mode in ("", "repeat"):
                if not rdays:
                    continue
            elif mode == "weekly":
                rdays = [0]
            elif mode == "monthly":
                rdays = []
            else:
                # unknown -> ignore
                continue
            targets = tpl.get("targets") or []

            is_bonus_repeat = bool(tpl.get("early_bonus_enabled")) and int(tpl.get("early_bonus_days", 0) or 0) > 0 and int(tpl.get("early_bonus_points", 0) or 0) > 0
            if is_bonus_repeat:
                # Ignore any fixed date in tpl['due']; deadline is derived from schedule.
                tpl_id = str(tpl.get("id") or "")
                if mode == "monthly":
                    due_iso = self._next_monthly_due_iso(today, include_today=True)
                else:
                    due_iso = self._next_repeat_due_iso(today, list(rdays), include_today=True)
                if tpl_id and due_iso:
                    for target in targets:
                        if not target:
                            continue
                        if self._active_repeat_instance_exists(tpl_id, target):
                            continue
                        await self.add_task(
                            title=tpl["title"],
                            points=tpl["points"],
                            description=tpl["description"],
                            assigned_to=target,
                            icon=tpl.get("icon") or "",
                            due=due_iso,
                            repeat_template_id=tpl_id,
                            early_bonus_enabled=True,
                            early_bonus_days=int(tpl.get("early_bonus_days", 0) or 0),
                            early_bonus_points=int(tpl.get("early_bonus_points", 0) or 0),
                            persist_until_completed=True,
                            quick_complete=tpl.get("quick_complete", False),
                            skip_approval=tpl.get("skip_approval", False),
                            categories=list(tpl.get("categories") or []),
                            mark_overdue=tpl.get("mark_overdue", True),
                        )
                continue

            # Scheduled behavior: create on the scheduled boundary.
            should_spawn = False
            if mode in ("", "repeat"):
                should_spawn = weekday in (rdays or [])
            elif mode == "weekly":
                should_spawn = weekday == 0
            elif mode == "monthly":
                should_spawn = int(today.day) == 1

            if should_spawn:
                tpl_id = str(tpl.get("id") or "")
                for target in targets:
                    if not target:
                        continue
                    if _active_instance_exists(tpl_id, target):
                        continue
                    # Fallback de-dupe (in case older data didn't set repeat_template_id)
                    try:
                        if any(
                            (x.assigned_to == target and x.title == tpl.get("title") and _local_created_date(x) == today)
                            for x in self.tasks
                        ):
                            continue
                    except Exception:
                        pass

                    await self.add_task(
                        title=tpl["title"],
                        points=tpl["points"],
                        description=tpl["description"],
                        assigned_to=target,
                        icon=tpl.get("icon") or "",
                        due=tpl.get("due"),
                        repeat_template_id=tpl_id or None,
                        early_bonus_enabled=tpl.get("early_bonus_enabled"),
                        early_bonus_days=tpl.get("early_bonus_days"),
                        early_bonus_points=tpl.get("early_bonus_points"),
                        persist_until_completed=(tpl.get("persist_until_completed", False) if mode in ("", "repeat") else False),
                        quick_complete=tpl.get("quick_complete", False),
                        skip_approval=tpl.get("skip_approval", False),
                        categories=list(tpl.get("categories") or []),
                        mark_overdue=tpl.get("mark_overdue", True),
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
    async def add_category(self, name: str, color: str = "") -> Category:
        cid = str(uuid4())
        cat = Category(id=cid, name=str(name).strip(), color=self._normalize_hex_color(color))
        self.categories.append(cat)
        await self.async_save()
        return cat

    async def rename_category(self, category_id: str, new_name: str) -> Category:
        cat = self._get_category(category_id)
        cat.name = str(new_name).strip()
        await self.async_save()
        return cat

    def _normalize_hex_color(self, value: str) -> str:
        v = str(value or "").strip().lower()
        if not v:
            return ""
        if not v.startswith("#"):
            v = "#" + v
        # Expand shorthand #rgb -> #rrggbb
        m3 = re.fullmatch(r"#([0-9a-f]{3})", v)
        if m3:
            r, g, b = m3.group(1)
            return f"#{r}{r}{g}{g}{b}{b}"
        if re.fullmatch(r"#[0-9a-f]{6}", v):
            return v
        raise ValueError("invalid_color")

    async def set_category_color(self, category_id: str, color: str) -> Category:
        cat = self._get_category(category_id)
        cat.color = self._normalize_hex_color(color)
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
