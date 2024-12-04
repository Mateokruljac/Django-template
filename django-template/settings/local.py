from .base import *

DEBUG = os.environ.get("DEBUG", True)
IS_PRODUCTION = False
CORS_ORIGIN_ALLOW_ALL = True
ALLOWED_HOSTS = ['*']

STATIC_ROOT = os.path.join(BASE_DIR, 'static')

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

EMAIL_HOST = 'mailhog'
EMAIL_PORT = '1025'

print("************************************************************")
print("NOTE: Running in local environment.")
print("************************************************************")
