#!/bin/bash
set -eu

if [ ! -f installed.txt ]; then
    python3.13 -m pip install \
        kokoro_onnx \
        "tensorrt==10.*" \
        git+https://github.com/hexgrad/misaki \
        qwen_vl_utils \
        num2words \
        spacy

    touch installed.txt
fi
