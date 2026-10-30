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
        num2words

    touch installed.txt
fi

# Pin spaCy and preshed together. preshed 3.0.14 breaks spaCy tokenizer loading
# with "Python int too large to convert to C ssize_t" during model deserialization.
if ! python3.13 -c 'import spacy, preshed; assert spacy.__version__ == "3.8.14" and preshed.__version__ == "3.0.15"' >/dev/null 2>&1; then
    python3.13 -m pip install "spacy==3.8.14" "preshed==3.0.15"
fi
