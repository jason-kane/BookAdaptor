Docker Build Performance
========================

- Investigate and reduce excessively long `docker compose build` times. The October 20, 2026 build spent over 30 minutes on system-package installation and ComfyUI image export/import.
- Review replacing `texlive-full` with the required TeX packages; the system-package download alone was 5.47 GB.
- Improve layer caching and reuse of shared base images so adding Python dependencies does not repeat expensive system setup.
- Review ComfyUI update/install steps and whether routine app dependency changes can build only the affected services.
- Measure build stages before and after changes to verify the improvement.

Research
========

Would https://github.com/Linwei94/gnvitop be more useful than glances?


General Menu
============

I do not like the workshops dropdown as the primary method of navigation.

Chapter Page
============
/library/authorname/bookname/chapternumber/language

- Revisit character description extraction for long novel chapters: sending the entire chapter can exceed the LLM context window. Add token budgeting and context-aware chunking that preserves character attribution, then combine only supported evidence.
- Add a "Sketch" mode for props with a simple, low-resolution painting UI for drawing a prop. Include an "Enhance" button that uses image-to-image generation to turn the sketch into a usable white-background prop reference image.


Image authoring / Generate Image
-------------------------------

- Change "Generate Image" to open a text-plus-image-to-image (t+i2i) authoring environment.
- Provide a scrapbook-style workspace with easy crop, zoom, and composition of source images.
- Let the user request an image-to-image pass on demand to blend and smooth the composition into a finished image.
- Save the result as an authored image, with its authorship credential and source provenance recorded in Sources.


Multi-language support is .. well, it is shit.

Include the chapter text here?  If only to make it easy to copy-paste the chapter title if there is one.

Comfy
=====
Each chapter should have its own directory in comfy for input/output.

- Write Kokoro support to use Ollama instead of ComfyUI.
