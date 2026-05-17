"""
BT38 interpreter startup safety hook.

Python imports sitecustomize automatically when it is present on sys.path.
This installs the shutdown HTTP guard before Flask can serve retired marketplace
routes.
"""

import shutdown_http_guard  # noqa: F401 - import installs guard side effect
