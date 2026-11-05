# Local ComfyUI extensions

`kokoro_pronunciation.patch` adds an optional `pronunciation` string input to the
installed `comfyui-kokoro/ComfyUIKokoro.py` node. Apply it from that custom node's
directory with `patch -p1 < /path/to/kokoro_pronunciation.patch`, then restart
ComfyUI when its queue is idle.

An empty input preserves automatic text-to-phoneme generation. A nonempty input
is sent directly to `kokoro.create(..., is_phonemes=True)` and returned as the
pronunciation output. The selected voice, speed, and language remain in use.

Phrase-panel IPA edits persist a `pronunciation_override` attribute and trigger
the ordinary audio-regeneration path. Only that explicit override is supplied
to this input; a previously generated `pronunciation` is not a user correction.
Clearing the editor removes the override and regenerates from text.
