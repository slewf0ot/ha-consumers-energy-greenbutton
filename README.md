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

## Notes

- Test mode tokens (`UAPI_TEST_*`) work with sandbox data only
- Live mode requires Consumers Energy to approve your third-party registration
- Gas meter data support coming in a future release
