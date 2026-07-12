# generic helper for comfyUI interactions
import glob
import html
import json
import os
import re
import shutil
import time

import httpx

import const
import logger

log = logger.log(__name__)


def load_workflow_template(interface, mode, workflow_name: str) -> dict:
    log.info('Loading workflow template', interface=interface, mode=mode, workflow_name=workflow_name)
    try:
        workflow_fn = glob.glob(os.path.join(
            const.COMFYUI_WORKFLOW_TEMPLATES_DIR,
            f"{interface}.{mode}.{workflow_name}*.json"
        ))[0]
    except IndexError:
        log.error(
            "Workflow file not found",
            glob=f"{interface}.{mode}.{workflow_name}*.json"
        )
        raise ValueError(f"Workflow file not found for interface '{interface}', mode '{mode}', workflow name '{workflow_name}'.")
    
    workflow_fn = glob.glob(os.path.join(
        const.COMFYUI_WORKFLOW_TEMPLATES_DIR,
        f"{interface}.{mode}.{workflow_name}*.json"
    ))[0]

    if not os.path.exists(workflow_fn):
        log.error("Workflow file does not exist", workflow_fn=workflow_fn)
        raise ValueError(f"Workflow file {workflow_fn} does not exist.")
    
    with open(workflow_fn, "r") as h:
        workflow = json.load(h)
    
    return workflow


def apply_template_environment(workflow, template_environment):
    # template substitutions are easy when workflow is a string.
    # workflow_str = json.dumps(workflow)
    all_keys = template_environment.keys()

    if isinstance(workflow, str):
        if "{{" in workflow and "}}" in workflow:
            log.info(f"Applying template environment to string: {workflow}")
            replacement_variables = re.findall(r"\{\{(.*?)\}\}", workflow)
            log.info(f'Found replacement variables: {replacement_variables}')
            for var in replacement_variables:                
                var_type = None
                if ":" in var:
                    var_name, var_type = var.split(":")
                else:
                    var_name = var

                if var_name not in template_environment:
                    log.warning("Template variable not found in environment", variable=var_name, template_environment=template_environment)
                    raise ValueError(f"Template variable '{var_name}' not found in environment.")

                replacement_value = template_environment[var_name]
                if var_type and var_type.lower() == "int":
                    replacement_value = int(replacement_value)
                    # remove the quotes around the (now-replaced) value to make it a 'real' int as far as JSON is concerned.
                    log.info('Replacing "{{' + var + '}}" with int value %s', replacement_value)
                    workflow = workflow.replace('"{{' + var + '}}"', str(replacement_value))
                    workflow = workflow.replace('{{' + var + '}}', str(replacement_value))
                    log.info('Result after replacement: %s', workflow)
                elif var_type and var_type.lower() == "float":
                    replacement_value = float(replacement_value)
                    # remove the quotes around the (now-replaced) value to make it a 'real' float as far as JSON is concerned.
                    log.info('Replacing "{{' + var + '}}" with float value %s', replacement_value)
                    # if we're wrapped in quote, get rid of the quotes.
                    workflow = workflow.replace('"{{' + var + '}}"', str(replacement_value))
                    # if we're not wrapped in quotes, just replace as is.
                    workflow = workflow.replace('{{' + var + '}}', str(replacement_value))                    
                elif var_type and var_type.lower() == "safe":
                    replacement_value = str(replacement_value)
                    workflow = workflow.replace("{{" + var_name + ":safe}}", replacement_value)
                else:
                    replacement_value = str(replacement_value)
                    workflow = workflow.replace("{{" + var_name + "}}", replacement_value)
            #     else:
            #         log.warning("Template variable not found in environment", variable=var)
            #         raise ValueError(f"Template variable '{var}' not found in environment.")
            # # re.sub(r"\{\{(.*?)\}\}", lambda match: template_environment.get(match.group(1), match.group(0)), value)

    elif isinstance(workflow, list):
        for index, item in enumerate(workflow):
            workflow[index] = apply_template_environment(item, template_environment)
    
    elif isinstance(workflow, float):
        # no need to apply template environment to a float, but we want to make sure it is a valid JSON type.
        return workflow

    elif isinstance(workflow, int):
        # no need to apply template environment to an int, but we want to make sure it is a valid JSON type.
        return workflow

    elif isinstance(workflow, dict):
        for key, value in workflow.items():
            workflow[key] = apply_template_environment(value, template_environment)
            # if isinstance(value, str):
                
            # elif isinstance(value, dict):
            #     # just recurse
            #     workflow[key] = apply_template_environment(value, template_environment)
            # elif isinstance(value, list):
            #     new_list = []
            #     for item in value:
            #         item = apply_template_environment(item, template_environment)
            #         new_list.append(item)
            #     workflow[key] = new_list


                    # template_key = value[2:-2]
                    # if template_key in template_environment:
                    #     workflow[key] = template_environment[template_key]
                    # else:
                    #     log.warning("Template key not found in environment", template_key=template_key)
                    #     raise ValueError(f"Template key '{template_key}' not found in environment.")

        # for key, value in template_environment.items():
        #     if value is None:
        #         log.warning("Template environment value is None", key=key)
        #         raise ValueError(f"Template environment value for key '{key}' is None. Please provide a valid value.")
        #     # Hey bob, when it is a _bad_ time for the new python f-string? Well...
        #     # workflow_str = workflow_str.replace(f"{{{{{key}}}}}", str(value))
        #     if ":" in key:
        #         # typed key, the syntax sugar we are applying/removing to allow an
        #         # easy intermediary, human editable syntax.  we can't just drop
        #         # {{max}} in for an integer or it won't be valid json.  It's so much
        #         # easier when it stays valid json.
        #         key, keytype = key.split(":")

        #         if keytype == "int":
        #             # remove the quotes around the (now-replaced) value
        #             workflow_str = workflow_str.replace("{{" + key + ":" + keytype + "}}", json.dumps(int(value)))
        #         elif keytype == "safe":
        #             workflow_str = workflow_str.replace("{{" + key + ":" + keytype + "}}", str(value))
        #         else:
        #             log.error("Unknown key type", keytype=keytype)
        #             workflow_str = workflow_str.replace("\"{{" + key + "}}\"", json.dumps(value))
        #     else:
        #         log.info('Preparing template substitution', key=key, value=value)
        #         value = value.replace("\n", "\\n").replace('"', r'\"')
        #         workflow_str = workflow_str.replace("{{" + key + "}}", value)
        
        # every iteration should remain valid json.
        check = json.loads(json.dumps(workflow))

    workflow_str = json.dumps(workflow)
    if "{{" in workflow_str:
        offset = workflow_str.find("{{")
        context = 20
        start = max(0, offset - context)
        end = offset + context
        snippet = workflow_str[start:end]
        log.error("Unreplaced template placeholders found", snippet=snippet, pointer=" " * (offset - start + 2) + "v", all_keys=all_keys)
        raise ValueError("Unreplaced template placeholders found in workflow.")

    # try:
    #     out = json.loads(workflow_str)
    # except json.JSONDecodeError as e:
    #     log.error("Failed to parse workflow JSON", error=str(e))
    #     context = 20
    #     start = max(0, e.pos - context)
    #     end = e.pos + context
    #     snippet = e.doc[start:end]
    #     log.error("template replace problem",error=e, snippet=snippet, pointer=" " * (e.pos - start + 12) + "v", all_keys=all_keys)
    #     with open("/tmp/failed_workflow.json", "w") as f:
    #         f.write(workflow_str)
    #     raise ValueError(f"Failed to parse workflow JSON: {e}")

    return workflow




def apply_template_environment_old(workflow, template_environment):
    # template substitutions are easy when workflow is a string.
    workflow_str = json.dumps(workflow)
    all_keys = template_environment.keys()

    for key, value in template_environment.items():
        if value is None:
            log.warning("Template environment value is None", key=key)
            raise ValueError(f"Template environment value for key '{key}' is None. Please provide a valid value.")
        # Hey bob, when it is a _bad_ time for the new python f-string? Well...
        # workflow_str = workflow_str.replace(f"{{{{{key}}}}}", str(value))
        if ":" in key:
            # typed key, the syntax sugar we are applying/removing to allow an
            # easy intermediary, human editable syntax.  we can't just drop
            # {{max}} in for an integer or it won't be valid json.  It's so much
            # easier when it stays valid json.
            key, keytype = key.split(":")

            if keytype == "int":
                # remove the quotes around the (now-replaced) value
                workflow_str = workflow_str.replace("{{" + key + ":" + keytype + "}}", json.dumps(int(value)))
            elif keytype == "safe":
                workflow_str = workflow_str.replace("{{" + key + ":" + keytype + "}}", str(value))
            else:
                log.error("Unknown key type", keytype=keytype)
                workflow_str = workflow_str.replace("\"{{" + key + "}}\"", json.dumps(value))
        else:
            log.info('Preparing template substitution', key=key, value=value)
            value = value.replace("\n", "\\n").replace('"', r'\"')
            workflow_str = workflow_str.replace("{{" + key + "}}", value)
        
        # every iteration should remain valid json.
        check = json.loads(workflow_str)

    
    if "{{" in workflow_str:
        offset = workflow_str.find("{{")
        context = 20
        start = max(0, offset - context)
        end = offset + context
        snippet = workflow_str[start:end]
        log.error("Unreplaced template placeholders found", snippet=snippet, pointer=" " * (offset - start + 2) + "v", all_keys=all_keys)
        raise ValueError("Unreplaced template placeholders found in workflow.")

    try:
        out = json.loads(workflow_str)
    except json.JSONDecodeError as e:
        log.error("Failed to parse workflow JSON", error=str(e))
        context = 20
        start = max(0, e.pos - context)
        end = e.pos + context
        snippet = e.doc[start:end]
        log.error("template replace problem",error=e, snippet=snippet, pointer=" " * (e.pos - start + 12) + "v", all_keys=all_keys)
        with open("/tmp/failed_workflow.json", "w") as f:
            f.write(workflow_str)
        raise ValueError(f"Failed to parse workflow JSON: {e}")

    return out


def run_workflow(workflow, template_environment={}):
    """
    our input is a workflow template object.  
    (ie: What you get from json.load() on a comfyui workflow export)
    its' a template because it has {{placeholders}} in it that we 
    want to replace with things in ^^ template_environment before we
    can execute (for api) or link to it (for ui).
    
    This is 'run', so we're in the api lane. hang onto your butts.
    
    apply this template environment to this workflow template, then run it.
    """
    if "SEED" not in template_environment:
        template_environment["SEED"] = int(time.time() * 1000) % 2**32

    workflow = apply_template_environment(
        workflow,
        template_environment
    )
    
    try:
        workflow = {
            "prompt": workflow
        }
    except TypeError as e:
        with open("/tmp/failed_workflow.json", "w") as f:
            f.write(str(workflow))

        log.error("Failed to parse workflow JSON", error=str(e))
        raise

    client = httpx.Client()

    log.info(
        "client.post(COMFYUI_API_URL + 'prompt', workflow)", 
        COMFYUI_API_URL=const.COMFYUI_API_URL, 
        workflow=json.dumps(workflow, indent=2)
    )
    # api/workflow
    response = client.post(
        const.COMFYUI_API_URL + "prompt", 
        json=workflow
    )

    if response.status_code != 200:
        log.error("Failed to create workflow", response_text=response.text)
        raise ValueError(f"Failed to create workflow: {response.text}")

    finished = False
    job_id = response.json().get("prompt_id")
    # 1 second polling loop
    while not finished:
        workflow_response = client.get(const.COMFYUI_API_URL + f"api/jobs/{job_id}")
        if workflow_response.status_code != 200:
            log.error("Failed to get workflow", response_text=workflow_response.text)
            return f"Failed to get workflow: {workflow_response.text}", 500

        job_dict = workflow_response.json()
        if job_dict.get("status") in ["error", "cancelled", "completed"]:
            finished = True

        if not finished:
            time.sleep(1)


    # text outputs use 'FILE_NAME'
    if 'FILE_NAME' in template_environment:
        try:
            # because the "easy safeText" POS only allows .txt and .csv
            output_file_path = os.path.join(
                const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
                template_environment["FILE_NAME"]
            )
            
            if os.path.exists(output_file_path + ".txt"):
                with open(output_file_path + ".txt", "r") as f:
                    output_content = f.read()
                
                # remove "extra" .txt extension
                shutil.move(
                    output_file_path + ".txt",
                    output_file_path
                )

                log.info('Workflow output file content', output_content=output_content)
                return output_content
            
            log.info(
                "os.listdir",
                files=os.listdir(const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"]), 
                output_file_path=output_file_path
            )
            raise ValueError("Expected output file not found", output_file_path)

        except KeyError as err:
            log.error("Missing template environment key", error=str(err))
            # Not a text output
            output_file_path = None

    return "", 200
    ##########################################

    workflow_template = image_xml.attrs.get("workflow_template", "")
    prompt_fn = os.path.join(
        const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
        f"{chapter.nice}_img_{image_xml.attrs['index']}_{workflow_template}.prompt.txt",
    )

    prompt = ""
    if os.path.exists(prompt_fn):
        with open(prompt_fn, "r") as h:
            prompt = h.read().strip()
            image_xml.attrs["prompt"] = prompt
    else:
        log.error(f"Prompt file {prompt_fn} not found after workflow completion.")
        image_xml.attrs["prompt"] = ""
    chapter.save_xml()

    # the workflow has finished, the "final" job_dict should reflect the
    # finished state.  Gather the output image and copy it to the expected
    # location in the library, then update the image_xml to point to the new
    # image.
    for nodeId in job_dict.get("outputs", {}):
        for image in job_dict["outputs"][nodeId].get("images", []):
            if "filename" in image:
                filename = image["filename"]
                pfn = images.get_image_fn(
                    prompt=prompt + "_comfyui",
                    loras=[],
                    paragraph_dir=chapter.get_paragraph_dir(
                        image_xml.find_parent("paragraph").attrs["index"]
                    ),
                    image_index=image_xml.attrs["index"],
                    randomized=False,
                )

                shutil.copy(
                    os.path.join(const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"], filename),
                    os.path.join(const.LIBRARY_DIR, pfn),
                )

                image_xml.attrs["src"] = os.path.basename(pfn)
                chapter.save_xml()

    return "", 200
