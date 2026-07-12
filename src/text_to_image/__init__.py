import os
from . import registry as _registry
registry = _registry.registry

# for dirname in os.listdir(os.path.dirname(__file__)):
#     if os.path.isdir(os.path.join(os.path.dirname(__file__), dirname)):
#         # nothing interesting to us at this layer, one deeper.
#         for module_directory in os.listdir(os.path.join(os.path.dirname(__file__), dirname)):
#             if os.path.isdir(os.path.join(os.path.dirname(__file__), dirname, module_directory)):
#                 try:
#                     # the import will source self-registration into ourselves.
#                     module = __import__(f"text_to_image.{dirname}.{module_directory}.ui", fromlist=['*'])
#                 except Exception as err:
#                     raise

