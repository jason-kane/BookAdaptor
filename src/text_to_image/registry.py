
import logger
import os
log = logger.log(__name__)


class TextToImageRegistry:
    def __init__(self):
        self._registry = {}

    def add(self, func):
        log.info("Registering T2I provider", key=func.key, func=func)
        self._registry[func.key] = func

    def get(self, key):
        log.info("Getting T2I provider", key=key)
        log.info("Available providers", providers=list(self._registry.keys()))
        return self._registry.get(key)

    def all(self):
        return self._registry
    
    def selector(self, put_url: str, selected_key="") -> str:
        """
        It's triggering, I know.  Keep it together buddy.
        """
        out = f'''<wa-select 
            hx-put="{put_url}"
            hx-trigger="change"
            hx-target="#prompt"
            hx-vals='js:{{respond_with: "prompt"}}'
            hx-swap="outerHTML transition:true"
            label="Text to Image"
            value="{selected_key}"
            name="t2i"
            id="t2i">'''

        for key, cls in self._registry.items():
            out += f'<wa-option value="{key}">{cls.cosmetic}</wa-option>\n'
        out += "</wa-select>"
        #log.info(f"selector: {out}")
        return out

    def search(self, term: str="", gpu=False):
        if not self._registry:
            this_directory = os.path.join(os.path.dirname(__file__))
            for entry in os.listdir(this_directory):
                if os.path.isdir(os.path.join(this_directory, entry)):
                    log.info('Processing T2I directory', directory=entry)
                    # nothing interesting to us at this layer, one deeper.
                    for module_directory in os.listdir(os.path.join(this_directory, entry)):
                        if os.path.isdir(os.path.join(this_directory, entry, module_directory)):
                            log.info('Processing T2I module', module_directory=module_directory)
                            try:
                                # the import will source self-registration into ourselves.
                                if gpu:
                                    module = __import__(f"text_to_image.{entry}.{module_directory}.action", fromlist=['*'])
                                else:
                                    module = __import__(f"text_to_image.{entry}.{module_directory}.ui", fromlist=['*'])
                            except Exception as err:
                                log.error("Error importing T2I module", module_directory=module_directory, error=err)
                                continue

        # Now, we need to find "term".  Easy.
        if term:
            return self._registry[term]


registry = TextToImageRegistry()
