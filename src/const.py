import os
import os.path

print("Assigning constants...")

# big fat rich default narrator
NARRATOR = {
    "gender": "male",
    "age": "old",
    "accent": "british",
    "accent_strength": 1.25,
    "file": "NARRATOR.wav",
    "audio_appearances": 149,
    "visual_appearances": 0,
    "tag": "Narrator",
    "name": "Narrator",
    "voice_gender": "male",
    "voice_language": "english",
    "voice_accent": "british",
    "is_global": True,
    "voices": [
        {"id": "bm_daniel", "name": "Daniel", "strength": 79},
        {"id": "bm_george", "name": "George", "strength": 15},
        {"id": "bm_fable", "name": "Fable", "strength": 86},
        {"id": "bm_lewis", "name": "Lewis", "strength": 52},
    ],
}

# NARRATOR = {
#     "gender": "male",
#     "age": "old",
#     "accent": "british",
#     "accent_strength": 1.25,
#     "file": "NARRATOR.wav"
# }

if os.environ.get("CONTAINER_MODE"):
    LIBRARY_DIR = "/active"
    SRC_DIR = "/app"
    ASSETS_DIR = "/assets"
else:
    LIBRARY_DIR = os.path.join(
        os.path.expanduser("~"),
        "books",
        "active",
    )
    SRC_DIR = os.path.join(
        os.path.expanduser("~"),
        "books",
        "src"
    )
    ASSETS_DIR = os.path.join(
        os.path.expanduser("~"),
        "books",
        "assets"
    )

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

# obsolete
MODEL_CACHE_DIR = ""
# os.path.join(
#     os.path.expanduser("~"),
#     ".cache",
#     "huggingface",
#     "hub",
# )

SOUND_DIR = os.path.join(
    LIBRARY_DIR,
    "sounds",
)

PAGES_DIR = os.path.join(
    SRC_DIR,
    "pages"
)

COMFY_DIRS = {
    # from the perspective of...
    "artifactserver": {
        "WORKFLOWS_DIR": "/workflows/",
        "WORKFLOW_TEMPLATES_DIR": "/templates/",
        "OUTPUT_DIR": "/output/",
        "INPUT_DIR": "/input/",
        "API_URL": "http://comfyui:8188/",
        "UI_URL": "http://localhost:8188/",
        "CUSTOM_NODES": "/custom_nodes/",
        "STYLES_DIR": "/custom_nodes/ComfyUI_MileHighStyler/data",
    }, 
    "comfyui": {
        "WORKFLOWS_DIR": "/root/ComfyUI/user/default/workflows/",
        "WORKFLOW_TEMPLATES_DIR": "/root/ComfyUI/user/default/templates/", 
        "OUTPUT_DIR": "/root/ComfyUI/output/", 
        "INPUT_DIR": "/root/ComfyUI/input/",
    }
}

# obsolete, use COMFY_DIRS.
COMFYUI_WORKFLOWS_DIR = os.environ.get("COMFYUI_WORKFLOWS_DIR", os.path.expanduser(
    "~/comfy/ComfyUI/user/default/workflows/"
))
COMFYUI_WORKFLOW_TEMPLATES_DIR = os.environ.get("COMFYUI_WORKFLOW_TEMPLATES_DIR", os.path.expanduser(
    "~/comfy/ComfyUI/user/default/templates/"
))
COMFYUI_OUTPUT_DIR = os.environ.get("COMFYUI_OUTPUT_DIR", os.path.expanduser(
    "~/comfy/ComfyUI/output/"
))
COMFYUI_INPUT_DIR = os.environ.get("COMFYUI_INPUT_DIR", os.path.expanduser(
    "~/comfy/ComfyUI/input/"
))
COMFYUI_API_URL = os.environ.get("COMFYUI_API_URL", "http://comfyui:8188/")
COMFYUI_UI_URL = os.environ.get("COMFYUI_UI_URL", "http://localhost:8188/")

disable_image_generation = False

# TEXT_TO_IMAGE = "dall-e"  # dall-e
# TEXT_TO_IMAGE = "stable-diffusion"
TEXT_TO_IMAGE = "flux-schnell"
TEXT_TO_SPEECH = "coqui-tts"  # not used

BUILT_IN_DRAW = False

ALPHA_FREEZE_DURATION = 1.5
DEBUG_LAYER = False
FONT = "/usr/share/fonts/truetype/freefont/FreeSans.ttf"
FPS = 25
# FPS = 8
LEFT_MARGIN = 37
LINE_HEIGHT = 49  # height of one line of highlighted text in pixels  (approximate)
output_wavfile = "voice_track.wav"
SPEECH_REGION = "eastus"
VOICE = "en-US-JasonNeural"
MULTIPROCESS = True
PROCESSES = int(os.cpu_count() / 2)
THREADED = True
WORKERS = os.cpu_count() - PROCESSES  # worker threads per process

# widescreeen
WIDESCREEN_VSIZE = 1080
WIDESCREEN_HSIZE = 1920

# portrait (youtube short)
PORTRAIT_VSIZE = 1920
PORTRAIT_HSIZE = 1080

# LLM image output expectation
IMG_WIDTH = 1024
IMG_HEIGHT = 1024

# expected final rendered image size
IMG_TARGET_WIDTH = 1080
IMG_TARGET_HEIGHT = 1080

GEOMETRY = {
    "widescreen": {
        "HSIZE": WIDESCREEN_HSIZE,
        "VSIZE": WIDESCREEN_VSIZE,
        "SIZE": (WIDESCREEN_HSIZE, WIDESCREEN_VSIZE),
        "ASPECT_RATIO": WIDESCREEN_HSIZE / WIDESCREEN_VSIZE,
        "TEXT_HEIGHT": WIDESCREEN_VSIZE,
        "TEXT_WIDTH": WIDESCREEN_HSIZE - IMG_TARGET_WIDTH,
    },
    "portrait": {
        "HSIZE": PORTRAIT_HSIZE,
        "VSIZE": PORTRAIT_VSIZE,
        "SIZE": (PORTRAIT_HSIZE, PORTRAIT_VSIZE),
        "ASPECT_RATIO": PORTRAIT_HSIZE / PORTRAIT_VSIZE,
        "TEXT_HEIGHT": PORTRAIT_VSIZE - IMG_TARGET_HEIGHT,
        "TEXT_WIDTH": PORTRAIT_HSIZE,
    },
}

ALL_ASPECTS_LIST = list(GEOMETRY.keys())

# TEXT_HEIGHT = VSIZE
# TEXT_WIDTH = HSIZE - IMG_WIDTH

# FULLSCREEN_ASPECT_RATIO = HSIZE/VSIZE
STANDARD_ASPECT_RATIO = IMG_TARGET_WIDTH / IMG_TARGET_HEIGHT

# is this actually a constant?
# PARAGRAPH_GAP=64  # in pixels, inflates the measured height of each segment
# PARAGRAPH_GAP=63  # in pixels, inflates the measured height of each segment

# PARAGRAPH_AUDIO_GAP=1.0  # in seconds, added padding between paragraphs
DEBUG = False

# two choices for latex highlighter right now.
#
#   lua-ul and \highLight
#   easyReview and \highlight
#
#  I need to experiment and see if there is any difference and
#  more importantly -- if one is better about vertical slipping
HIGHLIGHTER = "lua-ul"  # "easyReview"
HIGHLIGHT_COMMAND = r"\highLight"  # \highlight
