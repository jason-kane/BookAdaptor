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

NARRATOR: dict[str, str] = configuration.get("NARRATOR", {})

# library, source, and assets directories
LIBRARY_DIR: str = configuration.get("LIBRARY_DIR", "/active")
SRC_DIR: str = configuration.get("SRC_DIR", "/app")
ASSETS_DIR: str = configuration.get("ASSETS_DIR", "/assets")
STYLES_DIR: str = configuration.get("STYLES_DIR", "/styles")

LORA_DIR: str = os.path.join(
    LIBRARY_DIR,
    "loras",
)
os.makedirs(LORA_DIR, exist_ok=True)

STATIC_DIR: str = os.path.join(
    SRC_DIR,
    "artifact_editor",
    "static",
)

GLOBAL_CHARACTERS_DIR: str = os.path.join(
    LIBRARY_DIR,
    "global_characters",
)

SOUND_DIR: str = os.path.join(
    LIBRARY_DIR,
    "sounds",
)

PAGES_DIR: str = os.path.join(
    SRC_DIR,
    "pages"
)

TODO_DIR: str = os.path.join(
    SRC_DIR,
    "todo"
)

COMFY_DIRS = configuration.get("COMFY_DIRS", {})
COMFYUI_API_URL = configuration.get("COMFYUI_API_URL", "http://comfyui:8188/")
COMFYUI_UI_URL = configuration.get("COMFYUI_UI_URL", "http://localhost:8188/")

FPS: int = configuration.get("FPS", 8)

MULTIPROCESS: bool = configuration.get("MULTIPROCESS", True)
PROCESSES: int = os.cpu_count() or 1

# expected final rendered image size
IMG_TARGET_WIDTH: int = configuration.get("IMG_TARGET_WIDTH", 1080)
IMG_TARGET_HEIGHT: int = configuration.get("IMG_TARGET_HEIGHT", 1080)

GEOMETRY = configuration.get("GEOMETRY", {})
ALL_ASPECTS_LIST = list(GEOMETRY.keys())

STANDARD_ASPECT_RATIO = IMG_TARGET_WIDTH / IMG_TARGET_HEIGHT
