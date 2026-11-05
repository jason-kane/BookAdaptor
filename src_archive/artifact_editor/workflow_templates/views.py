"""Archived definitions from src/artifact_editor/workflow_templates/views.py.

Historical source fragments: decorators and bodies retain their original module
globals. This file is not a runtime module and must never be imported by src.
See src_archive/manifest.json for reachability evidence.
"""

@bp.route("/open/<workflow>", methods=["GET"])
def open_workflow(workflow: str):
    # redirect to comfyui with this workflow loaded.
    log.warning('Obsolete code <WT0003>')
    return redirect(
        const.COMFYUI_UI_URL + "?workflow=" + os.path.basename(workflow),
        code=302,
    )
