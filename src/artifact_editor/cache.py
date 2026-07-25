from flask_caching import Cache
import redis

print('Initializing cache...')

# If you are importing this, know app.py has already reached over to init_app()
# on this to tie it into Flask.  If you want to change how it's configured, that
# stuff lives in app.py, not here.
cache = Cache()
r = redis.Redis(host='redis', port=6379, db=0)

print('Import Cache initialized as %s' % cache)