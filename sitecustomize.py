# Prevent pytest from auto-loading external plugins that may not be installed in the CI environment.
import os
os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
