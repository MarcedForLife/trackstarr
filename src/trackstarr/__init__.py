"""Keep library audio and subtitle tracks tidy."""

import os

__version__ = "0.1.0"

#: The zone the deploy stated, read before anything else runs. The service
#: writes a saved zone into TZ itself, and :mod:`trackstarr.config` is reloaded
#: on every save, so only this never-reloaded module can remember the deploy's
#: own word. Empty means the settings file may say.
ENV_TZ = os.environ.get("TZ", "").strip()

__all__ = ["ENV_TZ", "__version__"]
