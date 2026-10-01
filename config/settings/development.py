from .base import *

DEBUG = True

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        "auth_login": "100/minute",
        "auth_register": "100/minute",
        "auth_refresh": "100/minute",
        "auth_logout": "100/minute",
    },
}
