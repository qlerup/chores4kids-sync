# Chores4Kids — Home Assistant Integration (Sync Engine) 🧹👧👦

[![hacs\_badge](https://img.shields.io/badge/HACS-Default-blue.svg)](https://hacs.xyz)

> **Important:** This integration **requires** the matching Lovelace card (UI):
> **➡️ [https://github.com/qlerup/lovelace-chores4kids-card](https://github.com/qlerup/lovelace-chores4kids-card)**
> The card provides the full interface. Without it, you’ll only have entities and services.

<img width="1022" height="335" alt="image" src="https://github.com/user-attachments/assets/8aab466f-bac3-4989-adb2-5dafd10a362d" />


The **Chores4Kids** integration is the data & sync engine. It persists **children**, **tasks**, **points**, and an optional **reward shop** as Home Assistant entities, and exposes services the Lovelace card calls. It’s local‑only and fast.

---

## Installation

### HACS (recommended)

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=qlerup&repository=chores4kids-sync)


1. In HACS → **Integrations** → **⋯ → Custom repositories** → add this repo URL as **Integration**.

```
https://github.com/qlerup/chores4kids-sync
```

2. Install **Chores4Kids Sync**.
3. **Restart** Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration → Chores4Kids** and press **Submit**.

### Manual

1. Copy `custom_components/chores4kids/` into your HA `config/custom_components/`.
2. **Restart** Home Assistant.
3. Add the integration via **Settings → Devices & Services**.

> After installing the integration, install the **Lovelace card**: [https://github.com/qlerup/lovelace-chores4kids-card](https://github.com/qlerup/lovelace-chores4kids-card)

---

## Entities created 🧱

> Names can vary with your language; below are the defaults.

### 1) One sensor per child — **Points**

* **Name:** `Chores4Kids Points {Child Name}`
* **Unique ID:** `chores4kids_points_{child_id}`
* **State:** current points (integer)
* **Attributes:**

  * `child_id`, `child_name`, `slug`
  * `assigned_count`, `in_progress_count`, `awaiting_approval_count`, `approved_count`, `rejected_count`
  * `tasks`: list of minimal task objects for this child: `{id, title, points, status, due, icon}`

### 2) All tasks (collection)

* **Entity:** `sensor.chores4kids_tasks`
* **Name:** `Chores4Kids Tasks`
* **Unique ID:** `chores4kids_tasks_all`
* **State:** number of tasks
* **Attributes:**

  ```json
  {
    "tasks": [
      {
        "id": "t_1",
        "title": "Make bed",
        "points": 5,
        "status": "assigned",      
        "due": "2025-01-01T07:30:00",
        "assigned_to": "c_12345",
        "assigned_to_name": "Emma",
        "created": "2025-01-01T06:50:12+01:00",
        "icon": "mdi:bed",
        "repeat_days": [1,3,5],     
        "repeat_child_id": "c_12345"
      }
    ]
  }
  ```

### 3) Shop (optional)

* **Entity:** `sensor.chores4kids_shop`
* **Name:** `Chores4Kids Shop`
* **Unique ID:** `chores4kids_shop`
* **State:** number of **active** items
* **Attributes:**

  ```json
  {
    "items": [
      {"id":"s_1","title":"Xbox time 30 min","price":20,"icon":"mdi:xbox","image":"/local/chores4kids/xbox.jpg","active":true,
       "actions":[{"type":"service","domain":"switch","service":"turn_on","entity_id":"switch.xbox"},{"type":"delay","seconds":1800},{"type":"service","domain":"switch","service":"turn_off","entity_id":"switch.xbox"}]
      }
    ],
    "purchases": [
      {"id":"p_1","child_id":"c_12345","child_name":"Emma","item_id":"s_1","title":"Xbox time 30 min","price":20,"icon":"mdi:xbox","image":"/local/chores4kids/xbox.jpg","ts":"2025-01-01T12:34:56Z"}
    ]
  }
  ```

---

## Services (domain: `chores4kids`) ⚙️

Below are the services exposed by the integration. The Lovelace card calls these under the hood.

### Children admin

* **`chores4kids.add_child`**

  ```yaml
  name: "Emma"
  ```
* **`chores4kids.rename_child`**

  ```yaml
  child_id: "c_12345"
  new_name: "Emmy"
  ```
* **`chores4kids.remove_child`**

  ```yaml
  child_id: "c_12345"
  ```
* **`chores4kids.reset_points`**

  ```yaml
  # Resets one child if provided; otherwise all children
  child_id: "c_12345"  # optional
  ```
* **`chores4kids.add_points`**

  ```yaml
  child_id: "c_12345"
  points: 5
  ```

### Tasks

* **`chores4kids.add_task`**

  ```yaml
  title: "Make bed"
  points: 5
  description: "Smooth the duvet, tuck pillow"   # optional
  due: "2025-01-01T07:30:00"                     # optional ISO string
  child_id: "c_12345"                            # optional (assign on create)
  repeat_days: [mon, wed, fri]                    # list of ints 0-6 or names mon..sun
  repeat_child_id: "c_12345"                     # optional default assignee for repeats
  icon: "mdi:bed"                                # optional
  ```
* **`chores4kids.assign_task`**

  ```yaml
  task_id: "t_1"
  child_id: "c_12345"           # omit/null to unassign
  ```
* **`chores4kids.set_task_status`**

  ```yaml
  task_id: "t_1"
  status: assigned | in_progress | awaiting_approval | approved | rejected
  ```
* **`chores4kids.approve_task`**

  ```yaml
  task_id: "t_1"
  ```
* **`chores4kids.delete_task`**

  ```yaml
  task_id: "t_1"
  ```
* **`chores4kids.set_task_repeat`**

  ```yaml
  task_id: "t_1"
  repeat_days: [1,3,5]      # or [mon, wed, fri]
  repeat_child_id: "c_12345"
  ```
* **`chores4kids.set_task_icon`**

  ```yaml
  task_id: "t_1"
  icon: "mdi:star"          # empty to clear
  ```

### Shop

* **`chores4kids.add_shop_item`**

  ```yaml
  title: "Xbox time 30 min"
  price: 20
  icon: "mdi:xbox"          # optional
  image: "/local/chores4kids/xbox.jpg"   # optional (see upload service below)
  active: true               # optional (default true)
  actions:                   # optional list of steps
    - type: service
      domain: switch
      service: turn_on
      entity_id: switch.xbox
    - type: delay
      seconds: 1800
    - type: service
      domain: switch
      service: turn_off
      entity_id: switch.xbox
  ```
* **`chores4kids.update_shop_item`**

  ```yaml
  item_id: "s_1"
  title: "Xbox time 30 min"
  price: 20
  icon: "mdi:xbox"
  image: "/local/chores4kids/xbox.jpg"
  active: true
  actions: []
  ```
* **`chores4kids.delete_shop_item`**

  ```yaml
  item_id: "s_1"
  ```
* **`chores4kids.buy_shop_item`**

  ```yaml
  child_id: "c_12345"
  item_id: "s_1"
  ```
* **`chores4kids.upload_shop_image`** — saves a base64 image to `/config/www/chores4kids/` so it can be served as `/local/chores4kids/<filename>`.

  ```yaml
  filename: "xbox.jpg"
  data: "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQ..."  # or raw base64
  ```

### Maintenance

* **`chores4kids.purge_orphans`** — remove orphaned entities/devices from older versions (safe to run after big changes).

  ```yaml
  # no fields
  ```

---

## Daily rollover (midnight) 🌙

Every night at **00:00**, the integration:

1. **Removes old assigned tasks** from previous days (unassigned templates are kept forever).
2. **Creates today’s repeated tasks** from any task that has `repeat_days`. If `repeat_child_id` is set, it’s used; otherwise the task’s current `assigned_to` is used as the target.

This keeps the list fresh each day while preserving your repeat plans.

---

## Using with the Lovelace card (required) 🧩

Install the card: **[https://github.com/qlerup/lovelace-chores4kids-card](https://github.com/qlerup/lovelace-chores4kids-card)**

Minimum configs:

```yaml
# Admin view
type: custom:chores4kids-dev-card
mode: admin

# Kid view
type: custom:chores4kids-dev-card
mode: kid
child: "Emma"
```

The card will discover the entities this integration exposes and call the services above.

---

## Troubleshooting 🧰

* **Card shows no data** → ensure this integration is installed, configured, and HA was restarted.
* **No children listed** → create children in Admin view (or call `chores4kids.add_child`).
* **No tasks** → create one via the card or `chores4kids.add_task`.
* **Shop images** → use `chores4kids.upload_shop_image` then reference `/local/chores4kids/<file>` in the item.
* **Leftover sensors/devices** → run `chores4kids.purge_orphans`.

---

## Privacy & Local‑only

All data stays in your Home Assistant. No cloud, no telemetry. Shop actions run local HA services (`domain.service`) and optional delays.

---

## Releases & HACS

If you distribute releases via GitHub:

* Add `hacs.json` with:

  ```json
  { "name": "Chores4Kids Sync", "render_readme": true, "zip_release": true }
  ```
* Attach a ZIP that contains `custom_components/chores4kids/...` at the root (HACS will fetch it when `zip_release` is true).

---

## License

MIT — see `LICENSE`.
