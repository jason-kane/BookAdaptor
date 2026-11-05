FROM python:3.12-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
ARG AI_TOOLKIT_REF=main
RUN git clone --recurse-submodules https://github.com/ostris/ai-toolkit.git /opt/ai-toolkit && \
    cd /opt/ai-toolkit && git checkout "$AI_TOOLKIT_REF" && git submodule update --init --recursive
WORKDIR /opt/ai-toolkit
RUN pip install --no-cache-dir torch==2.13.0 torchvision==0.28.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu130
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir 'sqlalchemy>=2' 'psycopg[binary]' pillow redis
RUN pip install --no-cache-dir rich pyfiglet structlog flask flask-caching filelock
RUN python - <<'PY'
from pathlib import Path

path = Path('/opt/ai-toolkit/toolkit/dataloader_mixins.py')
source = path.read_text()
default = 'TEXT_EMBED_CACHE_BATCH = 16  # prompts per text-encoder forward when caching'
replacement = 'TEXT_EMBED_CACHE_BATCH = int(os.environ.get("AITK_TEXT_EMBED_CACHE_BATCH", "1"))'
if default not in source:
    raise SystemExit('AI Toolkit text embedding cache setting changed; review the low-VRAM patch.')
path.write_text(source.replace(default, replacement))
PY
ENV PYTHONPATH=/app AI_TOOLKIT_DIR=/opt/ai-toolkit PYTHONUNBUFFERED=1
CMD ["python", "-m", "artifact_editor.book.lora_training"]
