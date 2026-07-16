# Parent & Child Devices (grounded)

A Hubitat device can **own** other devices. The owner is a **parent**, the owned are **children**,
and the set is one physical thing (or one cloud account) projected as several hub devices — a
power strip's outlets, a Hue bridge's bulbs, a dual switch's two loads.

Sources: Hubitat's [Parent/Child Drivers](https://docs2.hubitat.com/en/developer/driver/parent-child-drivers)
doc and its `genericComponentParentDemo.groovy` / `genericComponentDimmer.groovy` examples, plus the
`/hub2/devicesList` tree verified live on **2.5.1.128** (C-8 Pro) against a hub of 151 top-level
devices and one parent owning 5 children.

## The parent is a device OR an app

- **Parent device** — a driver creates children for a multi-endpoint node: power strips with
  per-outlet control, dual switches/outlets, fan+light controllers. Children display **indented
  under the parent** in the Devices list.
- **Parent app** — an app creates the devices. Philips Hue Integration and Lutron Integrator work
  this way (each Hue bulb is a child of the integration app), as do CoCoHue and HubiThings Replica
  (`rules/device-lifecycle.md`). These are **harder to see**: the device is not indented anywhere,
  and its only tell is a **"Parent app"** row in the Device Details table on the device page's
  Device Info tab (`/device/fullJson/<id>` exposes it as `parentApp` — `reference/endpoints.md`).

The distinction matters because the two are reported differently, not because they behave
differently: both make the device a child.

## Reading the tree — `GET /hub2/devicesList`

Returns `{suggestBackup, devices:[...]}`, and `devices` is a **tree, not a flat list**. Each entry
is `{key, data, children, parent, child}`:

- `key` — `DEV-<id>`
- `parent` — bool: this device **is** a parent. It is not a pointer to one.
- `child` — bool: this device **is** a child.
- `children[]` — the owned entries, same shape, nested.

**Children appear only nested — never at the top level.** The grounded hub returns 151 top-level
entries and 5 children reachable only by walking `children[]` (156 devices in all). Iterating
`devices[]` without recursing silently misses every child device.

Verified child entry: `parent:false, child:true`, driver type `Zooz Power Strip Outlet Component`,
DNI `1A-CH1`…`1A-CH5` under parent DNI `1A`. Deriving a child DNI from the parent's is the common
pattern, **not a requirement** — the docs state authors may use any convention, and Hubitat's own
example keys on the parent's `device.id` instead (`"${device.id}-${type}"`).

`data.source` is `System` | `User` | `Linked`. `Linked` marks a **hub-mesh device owned by another
hub** — orthogonal to parent/child (2 on the grounded hub).

## Why it bites

- **A child has no radio of its own.** The parent holds the node and fans commands out, so there is
  nothing to exclude or repair at the radio level for a child (`rules/zwave-zigbee-mesh.md`).
- **Delete the parent and the children go with it.** `/device/fullJson/<id>` lists child devices, so
  the removal blast radius includes them (`rules/device-lifecycle.md`).
- **Children are excluded from Settings → Swap Device.** Verified on 2.5.1.128: all 151 top-level
  devices were offered, all 5 children absent. The page states it — *"Most child devices are not
  swappable and are not listed here. Attempting to swap them will break related integrations."*
  ("Most": AirPlay, Bluetooth, HomeKit Controller, Tuya, and Wiz are allowed as exceptions.) The
  live check covered device-parented children; app-parented exclusion is the doc's claim, untested
  here. See `skills/device-migration/SKILL.md`.
- **Re-adding a parent mints new child ids**, stranding every reference — the same trap as any
  replacement.

## Writing a parent/child pair

- Create from an app **or** a driver: `addChildDevice(namespace, typeName, deviceNetworkId, properties)`.
  Hubitat's example: `addChildDevice("hubitat", "Generic Component Switch", "${device.id}-Switch",
  [name: "...", isComponent: true])`. Built-in component drivers use namespace **`hubitat`**; a
  custom child driver uses its own.
- Prefer the built-in **"Generic Component …"** drivers when one matches your capabilities — fewer
  custom drivers for users to install.
- **Commands travel child → parent**: the child's `Xyz()` calls `componentXyz(cd)` on the parent,
  passing the child device as the **first parameter** (`componentOn(cd)`, `componentSetLevel(cd,
  level, transitionTime)`). The parent does all the Z-Wave/Zigbee/Matter/LAN talking.
- **Events travel parent → child**: the parent calls `parse()` on the child with a **List of Event
  maps** — `getChildDevice(cd.deviceNetworkId).parse([[name:"switch", value:"on",
  descriptionText:"..."]])`. Component drivers turn those into events. This is the convention, not
  `sendEvent` on the parent (`rules/state-vs-attributes.md`).
- Address children with `getChildDevice(dni)` / `getChildDevices()`.
- **Parent/child is the only way two devices see each other.** Two ordinary devices on the hub
  cannot call each other at all; a parent may call **any** method on its child (including reading
  attributes), and the child any method on the parent. A parent app likewise gets full method
  access, unlike an app holding a device from an `input` (`rules/app-lifecycle.md`).
