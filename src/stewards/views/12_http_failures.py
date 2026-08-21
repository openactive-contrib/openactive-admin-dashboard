from stewards.components.monitor_page import render_monitor_page
from stewards.monitors.registry import get_monitor

render_monitor_page(get_monitor("http_failure"))
