import os
import os.path
import tomllib

print("Assigning constants...")

# one directory above us
with open(
    os.path.join(
        os.path.dirname(__file__),
        "configuration.toml"
    ), "rb"
) as f:
    configuration = tomllib.load(f)

NARRATOR = configuration.get("NARRATOR", {})

# library, source, and assets directories
LIBRARY_DIR = configuration.get("LIBRARY_DIR", "/active")
SRC_DIR = configuration.get("SRC_DIR", "/app")
ASSETS_DIR = configuration.get("ASSETS_DIR", "/assets")
STYLES_DIR = configuration.get("STYLES_DIR", "/styles")

LORA_DIR = os.path.join(
    LIBRARY_DIR,
    "loras",
)
os.makedirs(LORA_DIR, exist_ok=True)

STATIC_DIR = os.path.join(
    SRC_DIR,
    "artifact_editor",
    "static",
)

GLOBAL_CHARACTERS_DIR = os.path.join(
    LIBRARY_DIR,
    "global_characters",
)

SOUND_DIR = os.path.join(
    LIBRARY_DIR,
    "sounds",
)

PAGES_DIR = os.path.join(
    SRC_DIR,
    "pages"
)

TODO_DIR = os.path.join(
    SRC_DIR,
    "todo"
)

COMFY_DIRS = configuration.get("COMFY_DIRS", {})
COMFYUI_API_URL = configuration.get("COMFYUI_API_URL", "http://comfyui:8188/")
COMFYUI_UI_URL = configuration.get("COMFYUI_UI_URL", "http://localhost:8188/")

FPS = configuration.get("FPS", 8)

MULTIPROCESS = configuration.get("MULTIPROCESS", True)
PROCESSES = int(os.cpu_count() / 2)

# expected final rendered image size
IMG_TARGET_WIDTH = configuration.get("IMG_TARGET_WIDTH", 1080)
IMG_TARGET_HEIGHT = configuration.get("IMG_TARGET_HEIGHT", 1080)

GEOMETRY = configuration.get("GEOMETRY", {})
ALL_ASPECTS_LIST = list(GEOMETRY.keys())

STANDARD_ASPECT_RATIO = IMG_TARGET_WIDTH / IMG_TARGET_HEIGHT
