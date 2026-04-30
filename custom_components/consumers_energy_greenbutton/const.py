"""Constants for Consumers Energy Green Button integration."""

DOMAIN = "consumers_energy_greenbutton"

CONF_AUTHORIZATION_UID = "authorization_uid"

# How often to poll for new data (every 6 hours — utility data has 48hr delay anyway)
SCAN_INTERVAL_HOURS = 6

# Statistic IDs
STAT_ELECTRICITY_ENERGY = "consumers_energy_greenbutton:electricity_energy"
STAT_ELECTRICITY_COST = "consumers_energy_greenbutton:electricity_cost"

# Sensor names
SENSOR_ELECTRICITY_ENERGY = "Electricity Energy"
SENSOR_ELECTRICITY_COST = "Electricity Cost"
SENSOR_LAST_UPDATED = "Last Data Update"
