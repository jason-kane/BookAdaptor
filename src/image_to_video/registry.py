import logger
log = logger.log(__name__)
import os

class ImageToVideoEffectRegistry:
    def __init__(self):
        self._registry = {}

    def add(self, func):
        self._registry[func.name] = func

    def get(self, key):
        if not self._registry:
            # defer loading until first use, these modules should self-register when they import.
            for fn in os.listdir(os.path.dirname(__file__)):
                if fn.endswith(".py") and not fn.startswith("_") and fn not in ("__init__.py", "base.py", "registry.py"):
                    __import__(f"image_to_video.{fn[:-3]}")
        
        return self._registry.get(key)

    def all(self):
        return self._registry
    
    def selector(self, skip: list):
        log.info(f"{self._registry=}, {skip=}")
        out = """
            <wa-select label="Effects" name="effects">
        """
        for key, effect_class in self._registry.items():
            cosmetic = effect_class.cosmetic
            if key not in skip:
                out += f"""
                    <wa-option value="{key}">{cosmetic}</wa-option>
                """
        out += "</wa-select>"
        return out


registry = ImageToVideoEffectRegistry()