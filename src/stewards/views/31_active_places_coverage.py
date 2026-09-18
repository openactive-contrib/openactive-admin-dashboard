from stewards.components.coverage_page import render_coverage_page
from stewards.monitors.registry import get_monitor

render_coverage_page(get_monitor("active_places_coverage"))
