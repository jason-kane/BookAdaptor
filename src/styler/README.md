# Styler

Styler's blueprint, style library, widgets, templates, and bundled style data
live in this package. The editor registers `styler.views.bp` at `/styles/` on
port 8082. Home links to that port from List, Array, and Icons views.

Other editor modules import `styler.styles` and `styler.htmx` for shared style
selection. Sample generation uses the editor's chapter/image helpers and
ComfyUI; Redis stores style selections and ranking data.
