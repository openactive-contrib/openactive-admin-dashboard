from stewards.components.schema_drift_page import render_schema_drift_page
from stewards.monitors.registry import get_monitor

render_schema_drift_page(get_monitor("feed_custom_properties"))
