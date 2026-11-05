# Local LoRA creation

Book metadata includes a **T2I engine** selector and a **Z-Image Turbo LoRAs**
multi-select. These defaults apply when new images are created or generated
without an explicit workflow or LoRA selection. An image's saved workflow and
LoRA selection take precedence. LoRAs in this catalog are trained for Z-Image
Turbo and are applied only to Z-Image Turbo workflows.

The book's **+ Add LoRA** button opens four steps:

1. Name, concept, and trigger word.
2. Images discovered recursively in the book’s `source` directory (including
   `images` and `cropped`). All are selected initially; exclude unwanted examples
   before continuing. Selected images are copied, decoded, oriented, converted to
   RGB, and resized to fit within 2048 × 2048 pixels. Originals are unchanged.
3. Automatic captions from the existing local Qwen3.5 vision service, with
   progress and optional edits. Matching `.txt` files are saved beside the
   normalized images. Partial successes survive retries; completed captions are
   not regenerated. Each image has a book-character multi-select. Selected
   character names are stored as `[NAME]` tokens in its training caption,
   separately from the editable descriptive text. Captioning runs through the existing GPU queue and its
   Llama provider handoff. The video worker must load the updated dispatcher.
   Caption and character edits autosave as you work, with a per-image save
   indicator. Reopening Captions centers the most recently edited image.
   At Captions, **Remove image** excludes a training copy while preserving edits
   to other captions. It also works during automatic captioning. Removed copies
   are kept outside the training dataset; book source files remain unchanged.
   Keep at least one image.
4. Review and train with Ostris AI Toolkit using the local NVIDIA GPU.

The GPU queue has three manually selected modes: ComfyUI, Llama, and Training.
Selecting Training stops ComfyUI and Llama, then waits for their CUDA processes
to release GPU memory before marking Training active. The **Train LoRA** and
**Retry training** buttons also request Training mode. The dedicated AI Toolkit
worker starts queued LoRA jobs only in Training mode. ComfyUI and Llama jobs
remain queued until you manually select one of those modes again. Training mode
cannot be left while a LoRA job is actively training.

Drafts, datasets, job configuration, logs, and training output are stored under
`active/<author>/<book>/lora_training/<draft UUID>/`. Keep that directory to
resume or troubleshoot a job. The wizard URL identifies its book-scoped draft.
Training submissions are idempotent; editing is locked while queued or running.

## Trainer service

```sh
docker compose --profile training build lora_trainer
docker compose --profile training up -d lora_trainer
docker compose logs -f lora_trainer
```

The image installs AI Toolkit in its own Python environment, separate from the
application. Set `AI_TOOLKIT_REF` to a tested upstream commit or tag before building
to pin the trainer version; the default is upstream `main`.
The trainer also installs Flask, Flask-Caching, and filelock because completed
adapter registration imports the shared style catalog. After changing trainer
dependencies, rebuild the image and recreate the service with the commands above.

The worker processes jobs serially and waits while `nvidia-smi` reports other
GPU compute processes. Selecting Training stops ComfyUI and Llama and releases
their GPU memory. Select ComfyUI or Llama manually after the LoRA run finishes.
Its idle process does not load a model onto the GPU.

First training downloads the Z-Image Turbo base model and its training adapter;
the Hugging Face cache persists in the `ai_toolkit_cache` Docker volume. No
reference images are submitted to a hosted trainer. Model downloads require
network access. Training uses `cuda:0`, batch size 1, rank 32, 3,000 steps,
quantization, gradient checkpointing, and 512/768/1024 resolution buckets.
Configuration is saved as `training.json` for inspection. The 12 GB GPU
configuration caches text embeddings, offloads transformer and text encoder
layers to CPU, uses 512/768 pixel buckets, and skips generated samples during
training to lower peak VRAM use. Text embeddings are cached one caption at a
time to keep the Qwen encoder's cache pass within GPU memory.

The worker records queued, waiting for GPU, training, complete, and failed
states. The review page refreshes while a job is active and links to the last
64 KiB of its log. A failed job can be retried. Jobs interrupted by a worker
restart become failed rather than remaining falsely marked as running; Retries reuse the existing output directory; checkpoint recovery follows AI Toolkit’s behavior.

On success the final named adapter is copied to `active/loras`, its metadata
records the trigger and draft, the database catalog is refreshed, and the book's
default selection includes the new LoRA. Intermediate checkpoints are not
registered as completed LoRAs.

References: [AI Toolkit CLI](https://github.com/ostris/ai-toolkit),
[Z-Image Turbo training configuration](https://ostris.com/docs/ai-toolkit/models/zimage-turbo).
