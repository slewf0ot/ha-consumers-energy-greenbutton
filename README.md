# Consumers Energy Green Button — Home Assistant Integration

A custom Home Assistant integration that pulls your Consumers Energy electricity usage data via the Green Button Connect API (powered by UtilityAPI) and injects it into the HA Energy Dashboard.

## Features

- Fetches hourly/interval electricity usage from Consumers Energy
- Injects historical data into HA statistics for the Energy Dashboard
- Tracks cost data alongside kWh consumption
- Polls every 6 hours (utility data has a ~48hr delay anyway)
- Works with both test (sandbox) and live authorizations

## Prerequisites

1. A Consumers Energy account with smart meter
2. Complete Green Button authorization at [greenbutton.consumersenergy.com](https://greenbutton.consumersenergy.com)
3. A UtilityAPI account and API token from [utilityapi.com/dashboard](https://utilityapi.com/dashboard)

## Installation via HACS

1. In HACS, go to **Integrations** → **Custom repositories**
2. Add `https://github.com/slewf0ot/ha-consumers-energy-greenbutton` as an **Integration**
3. Search for **Consumers Energy Green Button** and install
4. Restart Home Assistant

## Manual Installation

1. Copy the `custom_components/consumers_energy_greenbutton` folder to your HA `custom_components` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Consumers Energy Green Button**
3. Enter your UtilityAPI API token
4. Select your authorization if prompted

## Energy Dashboard

After setup, go to **Settings → Dashboards → Energy** and:
- Under **Electricity Grid**, click **Add consumption**
- Select the statistic `consumers_energy_greenbutton:electricity_energy`
- Optionally add `consumers_energy_greenbutton:electricity_cost` for cost tracking

> **Note:** Data has a ~48 hour delay from the utility. The Energy Dashboard will show historical data but not real-time usage. For real-time monitoring, pair this with a hardware energy monitor like the Emporia Vue 2 or Shelly Pro 3EM.

## Data Sources

- **Interval data:** Hourly kWh readings from your smart meter
- **Cost data:** USD cost per interval (when available from utility)
- **Billing data:** Monthly bill summaries

## Services

### `consumers_energy_greenbutton.refresh_data`
Re-fetches the latest cached data from UtilityAPI and re-injects it into HA statistics. **Free and instant** — does not trigger a new collection from Consumers Energy.

```yaml
service: consumers_energy_greenbutton.refresh_data
```

### `consumers_energy_greenbutton.trigger_collection`
Requests UtilityAPI to pull fresh data directly from Consumers Energy right now.

> ⚠️ **Warning:** This costs money on UtilityAPI paid plans and requires a $50 minimum prepaid account. Only use this when you need the absolute latest data. Normal usage should rely on the automatic 6-hour polling schedule.

```yaml
service: consumers_energy_greenbutton.trigger_collection
data:
  confirm: true
```

## Dashboard Cards

### Simple Refresh Button
Add a button to any dashboard that manually syncs the latest cached data:

```yaml
type: button
name: Refresh Energy Data
icon: mdi:refresh
tap_action:
  action: call-service
  service: consumers_energy_greenbutton.refresh_data
```

### Energy Status Card
A card showing reading count, last update time, and a refresh button together:

```yaml
type: vertical-stack
cards:
  - type: entities
    title: Consumers Energy
    entities:
      - entity: sensor.consumers_energy_reading_count
        name: Readings Collected
        icon: mdi:counter
      - entity: sensor.consumers_energy_last_reading
        name: Latest Reading
        icon: mdi:clock-check
  - type: button
    name: Refresh Data
    icon: mdi:refresh
    tap_action:
      action: call-service
      service: consumers_energy_greenbutton.refresh_data
```

### Automation: Daily Refresh at 6 AM
Since utility data arrives with a ~48hr delay, scheduling a daily refresh in the morning ensures your Energy Dashboard is always showing the most current available data:

```yaml
alias: Consumers Energy Daily Refresh
trigger:
  - platform: time
    at: "06:00:00"
action:
  - service: consumers_energy_greenbutton.refresh_data
mode: single
```

Add this automation via **Settings → Automations → Create Automation → Edit in YAML**.

## Updating Your API Token

If your UtilityAPI token changes or expires:

1. Go to **Settings → Devices & Services**
2. Find **Consumers Energy Green Button**
3. Click the **⋮ menu** → **Reconfigure**
4. Enter your new token

Your existing energy history will be preserved.

## Notes

- Test mode tokens (`UAPI_TEST_*`) work with sandbox data only
- Live mode requires Consumers Energy to approve your third-party registration
- Gas meter data support coming in a future release
- Data has a ~48 hour delay from the utility — this is a Consumers Energy limitation, not a bug
