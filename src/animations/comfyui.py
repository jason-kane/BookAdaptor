# we're going to fade out the old image and fade in the new one
# half the given duration for each.
import html
import json
import os
import shutil
import subprocess
import time

import httpx
from flask import url_for

import const
import logger
from animations import Animation, registry

log = logger.log(__name__)


class ComfyBase(Animation):
    animation = True
    video_index = 0

    def get_configuration_widgets(self, chapter, image_xml, video_index=0):
        """
        These are individually responseive for submitting themselves on-change.
        """
        
        self.video_index = int(video_index)
        self.video_tag = f"_{self.video_index:02d}"
        
        self.animation_prompt = image_xml.attrs.get(f"animation_prompt{self.video_tag}", "This is a test of the ComfyUI animation feature.")

        self.image_index = int(image_xml.attrs["index"])        
        
        self.animation_frame_directory = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(
                image_xml.find_parent("paragraph").attrs["index"]
            ),
            "animation",
            f"image_{self.video_index:02d}_{self.image_index:06d}",
        )
        os.makedirs(self.animation_frame_directory, exist_ok=True)

        widget = f"""<wa-textarea
                hx-put="{url_for('library.book.chapter.images.base', **chapter.kwargs, image_index=image_xml.attrs['index'])}"
                hx-trigger="change"
                hx-swap="none"
                label="Prompt"
                name="animation_prompt{self.video_tag}"
                value="{html.escape(self.animation_prompt, quote=True)}"></wa-textarea>"""
        
        return widget    

class ComfyUi_i2v(ComfyBase):
    """ComfyUI based animation launcher."""
    cosmetic = "ComfyUI LTX i2v"
    key = "comfy_ui_i2v"
    mode = "i2v"
    workflow_animation_template = "LTX23"
    
    def apply(self, chapter, image_xml, frame_directory, extend=False, prompt_enhance=False):
        """
        We're going to use LTX 2.3 to apply the given prompt to the image.

        Then we're going places the frames in frame_directory.  The 
        only guarantee is that they will be sortable by filename.
        """
        os.makedirs(frame_directory, exist_ok=True)
        done_flag_fn = os.path.join(frame_directory, 'done.flag')

        image_index = int(image_xml.attrs["index"])
        video_index = self.video_index
        
        if os.path.exists(done_flag_fn):
            os.unlink(done_flag_fn)

        api_workflow = chapter.get_comfy_workflow(
            image_xml=image_xml,
            interface="api",
            mode=self.mode,
            workflow_template=self.workflow_template,
            video_index=video_index
        )

        client = httpx.Client()

        json_prompt = {
            "prompt": api_workflow
        }
        
        # for debugging
        with open('/tmp/comfyui_prompt.json', 'w') as h:
            json.dump(json_prompt, h, indent=2)

        response = client.post(
            const.COMFYUI_URL + "api/prompt", 
            json=json_prompt
        )

        if response.status_code != 200:
            # log.info(f"POST: {json.dumps(workflow, indent=2)}")
            log.error(f"Failed to create workflow: {response.text}")
            raise ValueError(f"Failed to create workflow: {response.text}")
    
        finished = False
        job_id = response.json().get("prompt_id")
        
        # 1 second polling loop
        while not finished:
            workflow_response = client.get(const.COMFYUI_URL + f"api/jobs/{job_id}")
            if workflow_response.status_code != 200:
                log.error(f"Failed to get workflow: {workflow_response.text}")
                return f"Failed to get workflow: {workflow_response.text}", 500
            job_dict = workflow_response.json()
            if job_dict.get("status") in ["error", "cancelled", "completed"]:
                finished = True
            
            if not finished:
                time.sleep(1)

        # get the image prompt save it to the image_xml
        workflow_template = image_xml.attrs.get("workflow_template", "")
        prompt_fn = os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            f"{chapter.nice}_img_{video_index:02d}_{image_index:06d}_{workflow_template}.prompt.txt"
        )
        
        if os.path.exists(prompt_fn):
            with open(prompt_fn, "r") as h:
                prompt = h.read().strip()
                image_xml.attrs[f"{self.mode}_prompt"] = prompt
        else:
            log.error(f"Prompt file {prompt_fn} not found after workflow completion.")

        # the workflow has finished, the "final" job_dict should reflect the
        # finished state.
        log.info(f"{job_dict=}")
        if job_dict.get("outputs"):
            for nodeId in job_dict.get("outputs", {}):                
                try:
                    mp4_filename = job_dict["outputs"][nodeId]["images"][0]["filename"]
                    break
                except KeyError:
                    pass
        else:
            # no outputs, we probably already genererated an mp4
            # but something went wrong after that.  Look for the mp4.
            mp4_filename = os.path.join(
                const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
                f"{chapter.nice}_img_{video_index:02d}_{image_index:06d}_{workflow_template}_00001_.mp4"
            )
            if not os.path.exists(mp4_filename):
                mp4_filename = os.path.join(
                    const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
                    "video",
                    f"{chapter.nice}_img_{image_index:06d}_{video_index:02d}_{workflow_template}_00001_.mp4"
                )

        animation_frame_directory = os.path.join(
            const.LIBRARY_DIR,
            chapter.get_paragraph_dir(
                 image_xml.find_parent("paragraph").attrs["index"]
            ),
            "animation",
            f"image_{image_index:06d}_{video_index:02d}",
        )

        os.makedirs(animation_frame_directory, exist_ok=True)

        log.info(f'Breaking {mp4_filename} into frames with ffmpeg')
        subprocess.run([
            "ffmpeg", 
            "-i", mp4_filename, 
            os.path.join(
                animation_frame_directory, 
                "output_%04d.png"
            )
        ])

        shutil.copy(
            mp4_filename,
            chapter.get_image_filename(image_xml).replace(".png", ".mp4")
        )

registry.add_module(ComfyUi_i2v)



class ComfyUi_flf2v(ComfyBase):
    """ComfyUI based animation launcher."""
    cosmetic = "ComfyUI: LTX 2.3 First and Last Frame"
    key = "comfy_ui_flf2v"
    mode = "flf2v"
    workflow_animation_template = "LTX23_flf"

    def apply(self, chapter, image_xml, extend=False, prompt_enhance=False):
        """
        We're going to use LTX 2.3 to apply the given prompt to the image.

        Then we're going places the frames in frame_directory.  The 
        only guarantee is that they will be sortable by filename.
        """
        done_flag_fn = os.path.join(
            self.animation_frame_directory,
            'done.flag'
        )
        
        if os.path.exists(done_flag_fn):
            os.unlink(done_flag_fn)

        api_workflow = chapter.get_comfy_workflow(
            image_xml=image_xml,
            interface="api",
            mode=self.mode,
            workflow_template=self.workflow_animation_template,
            video_index=self.video_index
        )

        client = httpx.Client()

        json_prompt = {
            "prompt": api_workflow
        }
        
        with open('/tmp/comfyui_prompt.json', 'w') as h:
            json.dump(json_prompt, h, indent=2)

        response = client.post(
            const.COMFYUI_URL + "api/prompt", 
            json=json_prompt
        )

        if response.status_code != 200:
            # log.info(f"POST: {json.dumps(workflow, indent=2)}")
            log.error(f"Failed to create workflow: {response.text}")
            raise ValueError(f"Failed to create workflow: {response.text}")
    
        finished = False
        job_id = response.json().get("prompt_id")
        
        # 1 second polling loop
        while not finished:
            workflow_response = client.get(const.COMFYUI_URL + f"api/jobs/{job_id}")
            if workflow_response.status_code != 200:
                log.error(f"Failed to get workflow: {workflow_response.text}")
                return f"Failed to get workflow: {workflow_response.text}", 500
            job_dict = workflow_response.json()
            if job_dict.get("status") in ["error", "cancelled", "completed"]:
                finished = True
            
            if not finished:
                time.sleep(1)

        # get the image prompt save it to the image_xml
        if self.animation:
            workflow_template = image_xml.attrs.get(f'workflow_animation_template{self.video_tag}')
        else:
            workflow_template = image_xml.attrs.get("workflow_template", "")

        # The comfyui workflow drops the prompt as a txt file
        prompt_fn = os.path.join(
            const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
            f"{chapter.nice}_img_{self.video_index:02d}_{self.image_index:06d}_{workflow_template}.prompt.txt"
        )
        if os.path.exists(prompt_fn):
            with open(prompt_fn, "r") as h:
                prompt = h.read().strip()
                # this is wrong
                # image_xml.attrs[f"{self.mode}_prompt"] = prompt
                # TODO: Save the prompt properly
        else:
            log.error(f"Prompt file {prompt_fn} not found after workflow completion.")

        # the workflow has finished, the "final" job_dict should reflect the
        # finished state.
        log.info(f"{job_dict=}")
        if job_dict.get("outputs"):
            for nodeId in job_dict.get("outputs", {}):                
                try:
                    mp4_filename = job_dict["outputs"][nodeId]["images"][0]["filename"]
                    break
                except KeyError:
                    pass
        else:
            # no outputs, we probably already genererated an mp4
            # but something went wrong after that.  Look for the mp4.
            mp4_filename = os.path.join(
                const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
                f"{chapter.nice}_img_{self.video_index:02d}_{self.image_index:06d}_{workflow_template}_00001_.mp4"
            )
            if not os.path.exists(mp4_filename):
                mp4_filename = os.path.join(
                    const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"],
                    "video",
                    f"{chapter.nice}_img_{self.video_index:02d}_{self.image_index:06d}_{workflow_template}_00001_.mp4"
                )

        log.info(f'Breaking {mp4_filename} into frames with ffmpeg')
        subprocess.run([
            "ffmpeg", 
            "-i", mp4_filename, 
            os.path.join(
                self.animation_frame_directory, 
                "output_%04d.png"
            )
        ])

        shutil.copy(
            mp4_filename,
            chapter.get_image_filename(image_xml).replace(".png", ".mp4")
        )

registry.add_module(ComfyUi_flf2v)