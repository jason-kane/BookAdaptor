import logger
log = logger.log(__name__)


class TransitionRegistry:
    def __init__(self):
        self._registry = {}

    def add_transition(self, func):
        self._registry[func.key] = func

    def get(self, key):
        return self._registry.get(key)

    def all(self):
        return self._registry
    
    def selector(self, get_url: str, selected_key=""):
        out = f'''<wa-select 
            hx-get="{get_url}"
            hx-trigger="change"
            hx-target="#transition_configuration"
            label="Transition"
            value="{selected_key}"
            name="transition_type"
            id="transition_type">'''
        for key, cls in self._registry.items():
            out += f'<wa-option value="{key}">{cls.cosmetic}</wa-option>\n'
        out += "</wa-select>"
        log.info(f"selector: {out}")
        return out



registry = TransitionRegistry()