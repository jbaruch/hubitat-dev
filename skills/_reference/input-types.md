# Preference Input Types

Source: [App Preferences](https://docs2.hubitat.com/en/developer/app/preferences), [Driver Preferences](https://docs2.hubitat.com/en/developer/driver/preferences). Inputs land in `settings[name]` and are readable bare as `name`.

## Types (apps and drivers)

| Type | Groovy value | Notes |
|------|--------------|-------|
| `text` | String | |
| `textarea` | String | app only; opt `rows` |
| `password` | String | |
| `number` | Long/Integer | |
| `decimal` | Double | |
| `bool` | Boolean | on/off slider |
| `enum` | String, or List if `multiple` | **requires `options:`** (List, or List of single-entry Maps for key≠label) |
| `time` | String `yyyy-MM-dd'T'HH:mm:ss.sssXX` | |
| `date` | String | |
| `email` | String | |
| `color` | Map | app only |
| `capability.<name>` | `DeviceWrapper` (or List if `multiple`) | **app only** — device selector by capability; `<name>` is camelCase (e.g. `capability.switch`, `capability.motionSensor`) |
| `device.<DriverName>` | `DeviceWrapper` | app only — selector by driver name |
| `button` | — | app only; fires `appButtonHandler(String)` and refreshes the page |

## Common input options

`title:`, `description:`, `required: true`, `defaultValue:`, `multiple: true`, `submitOnChange: true` (re-render page/prefs on change — the key to dynamic UIs), `width: 1..12`, `range: "low..high"` (number/decimal, e.g. `"0..100"`), `options:` (enum), `disabled:`, `showFilter:` (capability inputs).

## Shape differences

- **Apps** nest inputs in `preferences { page { section { input ... } } }`; single-page apps may drop `page` and go straight to `section`. Dynamic UI uses `dynamicPage(...)` returned from a method with `submitOnChange` inputs.
- **Drivers** have one flat `metadata { preferences { input ... } }` — no `page`/`section`, no device-selector or `button` inputs.

## The logging-preference convention

Nearly every community driver/app exposes these two, guards chatty logs on them, and auto-disables debug after 30 minutes:

```groovy
input name: "logEnable", type: "bool", title: "Enable debug logging", defaultValue: true
input name: "txtEnable", type: "bool", title: "Enable descriptionText logging", defaultValue: true
```

See the `logging-conventions` rule for the full `runIn(1800, logsOff)` idiom.
