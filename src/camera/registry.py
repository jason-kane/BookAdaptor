import logger
log = logger.log(__name__)


class CameraRegistry:
    def __init__(self):
        self._registry = {}

    def add(self, func):
        log.info("Registering camera method", name=func.name)
        self._registry[func.name] = func

    def get(self, key):
        return self._registry.get(key)

    def all(self):
        return self._registry
    
    def selector(self, get_url: str, selected_key=""):
        out = f'''<wa-select 
            hx-get="{get_url}"
            hx-trigger="change"
            hx-target="#camera_configuration"
            label="Camera Motion"
            value="{selected_key}"
            name="camera_motion"
            id="camera_motion">'''

        for key, cls in self._registry.items():
            out += f'<wa-option value="{key}">{cls.cosmetic_name}</wa-option>\n'
        out += "</wa-select>"
        log.debug(f"selector: {out}")
        return out

registry = CameraRegistry()