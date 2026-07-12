
import logger
log = logger.log(__name__)


class AnimationRegistry:
    def __init__(self):
        self._registry = {}

    def add_module(self, func):
        self._registry[func.key] = func

    def get(self, key):
        return self._registry.get(key)

    def all(self):
        return self._registry
    
    def selector(self, get_url: str, selected_key="", video_index=0):
        out = f'''<wa-select 
            hx-get="{get_url}"
            hx-trigger="change"
            hx-target="#animation_configuration_{video_index:02d}"
            label="Animation Method"
            value="{selected_key}"
            name="animation_method"
            id="animation_method_{video_index:02d}">'''
        
        out += '<wa-option value="disabled">Do Not Animate</wa-option>\n'

        for key, cls in self._registry.items():
            out += f'<wa-option value="{key}">{cls.cosmetic}</wa-option>\n'
        out += "</wa-select>"
        log.info(f"selector: {out}")
        return out



registry = AnimationRegistry()