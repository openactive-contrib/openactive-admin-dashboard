from stewards.components.quality_page import render_quality_page
from stewards.monitors.registry import get_monitor

render_quality_page(get_monitor("feed_quality"))
