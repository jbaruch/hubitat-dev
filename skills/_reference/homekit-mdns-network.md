# Hubitat HomeKit across segmented VLANs — the two traffic paths

When a Hubitat hub lives on an isolated IoT VLAN and the Apple Home controllers live on a
trusted Main VLAN, "enable mDNS" alone does **not** make HomeKit work. HomeKit needs **two
distinct paths**, and a prebaked "allow mDNS" firewall exception satisfies only the first
half of the first one. Verified working on a live UniFi deployment: **Apps** Hubitat at
`192.168.30.15` (IoT `192.168.30.0/24`) publishing HomeKit bridges to an Apple Home controller
at `192.168.10.158` (Main `192.168.10.0/24`), across a UniFi gateway. All Hubitat hubs here are
production — this is a live-network procedure, not a lab (`multi-hub-topology` rule; the same
production-safety stance as `device-lifecycle`).

## Path 1 — Discovery (mDNS)

HomeKit bridges advertise `_hap._tcp` over mDNS, which is **link-local** — it does not cross a
VLAN boundary on its own. A reflector/proxy has to carry it:

- Turn on the UniFi **mDNS Proxy** between Main and IoT, and confirm it includes `_hap._tcp`.
- Specific mDNS allowances must precede any broad `IoT → Gateway` deny — a blanket IoT-to-gateway
  block eats the advertisements before the proxy ever sees them.

The firewall exception that actually worked, placed **before** `Block IoT to Gateway`:

| Field | Value |
| --- | --- |
| Source zone | Internal |
| Source | `192.168.30.0/24` (IoT) |
| Source UDP port | `5353` |
| Destination zone | Gateway |
| Destination UDP ports | `5353,32768-60999` |
| Protocol | UDP |
| State | All |

**Destination `5353` is mandatory, not optional.** The high range `32768-60999` covers the
stateful replies to *legacy-unicast* queries the reflector sends — but unsolicited HomeKit/mDNS
advertisements arrive **on 5353**, so a rule allowing only the high range passes the query replies
and silently drops the advertisements. That is the trap the out-of-the-box "allow mDNS" preset
falls into.

Coexistence, verified: `Block IoT to Gateway Management` and `Block IoT to Default Management`
both stay enabled with the mDNS exception above in front of them.

## Path 2 — HAP sessions (TCP)

The advertisement carries the bridge's address and its **HAP TCP port** in the `_hap._tcp` SRV
record. Discovery finding the bridge is not the same as a controller being able to talk to it:

- Apple Home hubs/controllers on Main must be allowed to **initiate TCP** to that advertised port
  on the IoT bridge, and established/related return traffic from IoT must be permitted.
- **Never hard-code the HAP port.** Read it from the SRV record — it differs between bridges and
  changes when the integration is recreated.

`Block IoT to Main (NAS isolation)` is the rule that severs this path. It is currently paused.
Do not re-enable it without first proving all three: Main can open TCP to each bridge's advertised
`_hap._tcp` port, established/related return traffic from IoT is allowed, and HomeKit reconnects
after an integration restart.

## Switch-port / VLAN placement

For a Hubitat on a UniFi access port:

- Native network: **IoT**. Tagged VLANs: **Block All**. No client-level Virtual Network Override.
  A fixed IP is fine.
- Do **not** combine an IoT client override with a port that blocks the required tagged VLAN —
  UniFi will report the client has no connectivity, and it means it. Treat that warning as a real
  configuration error, not noise.

## Validation — from the Main network

After any firewall change, use Hubitat's **non-destructive "Restart integration"** action only,
then verify from a Main-network host:

```
dns-sd -B _hap._tcp local.        # the Hubitat bridges appear in the browse list
dns-sd -G v4 hubitat.local.       # resolves to 192.168.30.15
```

Then confirm Hubitat's HomeKit page shows an **active connection** from a Main-network controller.
Known-good result on this deployment: Apps HomeKit connected from `192.168.10.158`. A full service
check restarts only the bridge's HomeKit service and confirms it disappears, republishes, and
reconnects.

## Troubleshooting order

Work the path, not the symptom — top to bottom, stop when it breaks:

1. Verify the switch-port VLAN config and the device's effective network first.
2. Verify mDNS advertisements reach Main (`dns-sd -B`).
3. Verify the advertised HAP TCP port is reachable from Main.
4. If isolating a firewall rule: pause the custom policies (only if authorized), **record their
   original states**, then restore incrementally and retest after each group.
5. Fix the narrow exception, re-enable the deny rule, retest.

## What not to do

A blocked mDNS or HAP path looks exactly like a broken integration — and none of the destructive
"fixes" repair a network problem. Until the paths above are proven, do **not**: factory-reset a
hub, reset HomeKit pairing, regenerate the QR/pairing code, reboot or power-cycle a hub, or move a
hub between VLANs. "Devices" Hubitat is production, not a disposable test hub. Every one of these
needs explicit authorization, and none of them can undo a firewall rule.
