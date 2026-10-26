#!/bin/bash
set -eu

if [ ! -f installed.txt ]; then
    python3.13 -m pip install \
        "comfy-kitchen>=0.2.40" \
        "comfy-aimdo>=0.6.0" \
        "comfyui-workflow-templates>=0.11.94" \
        kokoro_onnx \
        "tensorrt==10.*" \
        git+https://github.com/hexgrad/misaki \
        qwen_vl_utils \
        num2words \
        spacy

    touch installed.txt
fi
