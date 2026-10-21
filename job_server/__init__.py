"""MoleditPy Job Server: loading the plugin never starts a listener."""

PLUGIN_NAME = "Job Server"
PLUGIN_VERSION = "0.2.1"
PLUGIN_AUTHOR = "HiroYokoyama"
PLUGIN_DESCRIPTION = "Authenticated shared job queue and headless server for trusted users."
PLUGIN_CATEGORY = "Utility"
PLUGIN_TAGS = ["Utility"]
PLUGIN_DEPENDENCIES = ["fastapi", "uvicorn", "cryptography", "psutil", "packaging"]
PLUGIN_OPTIONAL_DEPENDENCIES = []
PLUGIN_SUPPORTED_MOLEDITPY_VERSION = ">=5.0.0, <6.0.0"
PLUGIN_SUPPORTED_OS = ["Windows", "Linux", "WSL"]


def initialize(context):
    from .plugin import initialize as register

    register(context)
