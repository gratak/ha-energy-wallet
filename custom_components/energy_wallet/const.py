"""Constants for the Energy Wallet integration."""

from __future__ import annotations

DOMAIN = "energy_wallet"

CONF_IMPORT_ENTITY = "import_entity"
CONF_EXPORT_ENTITY = "export_entity"
CONF_SELLER = "seller"
CONF_DSO = "dso"
CONF_TARIFF_GROUP = "tariff_group"
CONF_DYNAMIC = "dynamic"
CONF_PHASES = "phases"
CONF_SALE_MODE = "sale_mode"

CONF_ENERGY_PEAK = "energy_peak"
CONF_ENERGY_OFFPEAK = "energy_offpeak"
CONF_DYNAMIC_MARKUP = "dynamic_markup"
CONF_DIST_PEAK = "dist_peak"
CONF_DIST_OFFPEAK = "dist_offpeak"
CONF_ENERGY_PEAK2 = "energy_peak2"
CONF_DIST_PEAK2 = "dist_peak2"
CONF_PEAK2_HOURS = "peak2_hours"
CONF_PEAK2_HOURS_SUMMER = "peak2_hours_summer"
CONF_OFFPEAK_HOURS = "offpeak_hours"
CONF_OFFPEAK_HOURS_SUMMER = "offpeak_hours_summer"
CONF_EXCISE = "excise"
CONF_CAPACITY_TIER = "capacity_tier"
CONF_WEEKEND_OFFPEAK = "weekend_offpeak"
CONF_HOLIDAY_OFFPEAK = "holiday_offpeak"
CONF_FIXED_MONTHLY = "fixed_monthly"
CONF_VAT = "vat"
CONF_DEPOSIT_MULTIPLIER = "deposit_multiplier"
CONF_CLAMP_NEGATIVE = "clamp_negative_rce"
CONF_MAX_POWER_KW = "max_power_kw"

SALE_MODE_RCE = "rce"
SALE_MODE_RCEM = "rcem"
SALE_MODES = [SALE_MODE_RCE, SALE_MODE_RCEM]

TARIFF_GROUPS = ["G11", "G12", "G12w", "G13"]
# Groups with an off-peak zone; G13 adds a second (afternoon) peak.
TWO_ZONE_GROUPS = {"G12", "G12w", "G13"}
THREE_ZONE_GROUPS = {"G13"}

OTHER = "other"

DEFAULT_VAT = 0.23
DEFAULT_EXCISE = 0.005
CAPACITY_TIERS = ["none", "lt500", "500_1200", "1200_2800", "gt2800"]
DEFAULT_CAPACITY_TIER = "1200_2800"
DEFAULT_DEPOSIT_MULTIPLIER = 1.0
DEFAULT_MAX_POWER_KW = 50.0

PERIOD_TODAY = "today"
PERIOD_MONTH = "month"
PERIOD_YEAR = "year"
PERIODS = [PERIOD_TODAY, PERIOD_MONTH, PERIOD_YEAR]

SIGNAL_UPDATE = "energy_wallet_update_{}"

STORAGE_VERSION = 1

FRONTEND_URL = "/energy_wallet/energy-wallet-card.js"
