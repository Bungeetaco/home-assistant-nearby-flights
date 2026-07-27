"""Test bootstrap: import the api/ package standalone, without homeassistant.

The api/ package deliberately has no homeassistant imports (see the comment in
api/event.py), but importing it as custom_components.nearby_flights.api would
execute the integration's __init__.py, which does import homeassistant. Adding
the integration directory itself to sys.path lets `import api.*` resolve the
package directly, so the whole suite runs under plain `pip install pytest
requests`.
"""
import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "custom_components", "nearby_flights"),
)
