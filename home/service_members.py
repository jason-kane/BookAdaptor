"""Display metadata for Home's Compose service members; owned panels add their own."""
MEMBER_METADATA = {
    "artifactserver": {"name": "Library", "image": "library.png", "description": "Book and video editor."},
    "glances": {"name": "Glances", "image": "glances.png", "description": "Monitor CPU, GPU, RAM, and disk usage."},
    "comfyui": {"name": "Comfy UI", "image": "comfyui.png", "description": "Image and workflow generation.", "path": "/", "port": 8188},
    "test": {"name": "Tests", "image": "pytest.png", "description": "Run the editor test suite."},
    "nginx": {"name": "Nginx", "image": "pytest.png", "description": "Shared web entry point and coverage reports.", "path": "/", "port": 80},
}
