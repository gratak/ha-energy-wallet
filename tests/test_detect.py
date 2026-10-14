from custom_components.energy_wallet.detect import grid_entities_from_energy_prefs, rank_candidates

P = "sensor.garaz_szafka_rozdzielcza_deye"


def energy(name, state_class="total"):
    return {"unit_of_measurement": "kWh", "state_class": state_class, "friendly_name": name}


DEYE = [
    (f"{P}_inverter_totalenergybuy_2603313875", energy("Deye Inverter TotalEnergyBuy 2603313875")),
    (f"{P}_inverter_totalenergysell_2603313875", energy("Deye Inverter TotalEnergySell 2603313875")),
    (f"{P}_inverter_dailyenergypurchased_2603313875", energy("Deye Inverter DailyEnergyPurchased 2603313875")),
    (f"{P}_inverter_dailygridfeedin_2603313875", energy("Deye Inverter DailyGridFeedIn 2603313875")),
    (f"{P}_inverter_totalconsumption_2603313875", energy("Deye Inverter TotalConsumption 2603313875")),
    (f"{P}_station_monthly_grid_import_62745671", energy("Deye Station Monthly Grid Import 62745671", "total_increasing")),
    (f"{P}_station_daily_grid_export_today_62745671", energy("Deye Station Daily Grid Export Today 62745671", "total_increasing")),
    (f"{P}_station_grid_import_power_62745671", {"unit_of_measurement": "W", "state_class": "measurement"}),
]


def test_deye_detection():
    imports, exports = rank_candidates(DEYE)
    assert [c.entity_id for c in imports] == [f"{P}_inverter_totalenergybuy_2603313875"]
    assert [c.entity_id for c in exports] == [f"{P}_inverter_totalenergysell_2603313875"]


def test_energy_prefs():
    prefs = {
        "energy_sources": [
            {"type": "solar", "stat_energy_from": "sensor.pv"},
            {"type": "grid", "flow_from": [{"stat_energy_from": "sensor.imp"}], "flow_to": [{"stat_energy_to": "sensor.exp"}]},
        ]
    }
    assert grid_entities_from_energy_prefs(prefs) == ("sensor.imp", "sensor.exp")
    assert grid_entities_from_energy_prefs(None) == (None, None)
