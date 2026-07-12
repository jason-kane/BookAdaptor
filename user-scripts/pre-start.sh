#!/bin/bash
set -eu

python3.13 -m pip install \
    kokoro_onnx \
    misaki \
    qwen_vl_utils \
    num2words \
    spacy