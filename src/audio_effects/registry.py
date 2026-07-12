
import logger

log = logger.log(__name__)


class AudioEffectRegistry:
    def __init__(self):
        self._registry = {}

    def add_effect(self, func):
        self._registry[func.key] = func

    def get_effect(self, key):
        return self._registry.get(key)

    def all(self):
        return self._registry
    
    def selector(self, skip: list, ns: str = ""):
        log.info(f"{self._registry=}, {skip=}")
        out = f"""
            <wa-select id="{ns}_effect_selector" label="Effects" name="effect">
        """
        for key, effect_class in self._registry.items():
            cosmetic = effect_class.cosmetic
            if key not in skip:
                out += f"""
                    <wa-option value="{key}">{cosmetic}</wa-option>
                """
        out += "</wa-select>"
        return out

        out = f'''<wa-select 
            hx-get="{get_url}"
            hx-trigger="change"
            hx-target="#animation_configuration"
            label="Animation Method"
            value="{selected_key}"
            name="animation_method"
            id="animation_method">'''
        for key, cls in self._registry.items():
            out += f'<wa-option value="{key}">{cls.cosmetic}</wa-option>\n'
        out += "</wa-select>"
        log.info(f"selector: {out}")
        return out



registry = AudioEffectRegistry()