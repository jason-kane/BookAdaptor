from flask_caching import Cache

print('Initializing cache...')
cache = Cache()
print('Import Cache initialized as %s' % cache)