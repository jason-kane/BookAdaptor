# Example

[![Link to Youtube Example](assets/example_result.png)](https://youtu.be/UguuQuCYBh0?si=xLMxq_jzNHJccfpF)

## Requirements

* Linux
* Docker
* Docker Compose
* NVidia 3060 12GB (or better).

Windows or OSX might work.  Less VRAM might work.  I have not tried.

## Purpose

As you can see from the sample linked a the top... it is quite rough.  I'm tying together a sort of dashboard for building these long-form text to video.

That, would be this.

Good quality read-along, spoken out-loud video with contextually relevant images and video.

The text of the book is rendered as crisp, precisely typeset L<span style="text-transform: uppercase; font-size: 0.85em; margin-left: -0.3em; vertical-align: 0.5ex; margin-right: -0.15em;">a</span>T<span style="text-transform: uppercase; font-size: 0.85em; margin-left: -0.1667em; vertical-align: -0.5ex; margin-right: -0.125em;">e</span>X.

The process of getting from the text of a story to a full video is an interactive, human driven process.  This is not a one shot upload text and out pops a movie.  It is intended to support iterative improvement.  The longer term plan is to enable collaboration and community driven improvements.

## Preface

I know the installation process is miserable.  My plan is to not tell anyone about this project until I've made the install significantly nicer, bare minimum.  It is quite likely this repo does not in fact contain everything that is actually neccessary to install without addition undocumented addendums.  I don't know.  The program itself is also riddled with bugs, misfeatures and very questionably choices.

Despite the heavy AI angle, I'm not into vibe coding.  It gets rid of what I consider to be the fun part.  I am not a purist, I still use command completion level AI assists and such.

The clever duckling among you may wonder where the git history is.  I'm cutting a clean slate because the history includes API keys and a lot of churn.  I've been building this for years, off and on.

There aren't any tests.  Which explains some of the massive swarm of bugs.

## Initialization

pull this repo, cd into it, then:

    docker compose build

This will take a hot second (on my local it takes 925 seconds, but that's cheating because I already have the base images like ubuntu 26.04 pulled).  The Latex installation (texlive-full) is the longest pole for me with bandwidth as my bottleneck, though the blis wheel compile isn't exactly snapy either.

This will also consume a rather significant amount of disk wherever you store docker images to the tune of:

  24.2GB for ComfyUI
  15.7GB for Artifactserver
  (and a few hundred MB for nginx, redis and glances)

## ComfyUI

from the repo directory:

    mkdir comfy
    cd comfy
    git clone https://github.com/comfy-org/ComfyUI
    git clone https://github.com/Comfy-Org/ComfyUI_frontend
    git clone https://github.com/YanWenKun/ComfyUI-Docker

## Models

* TODO: Helper to get all the models ComfyUI needs
* TODO: Helper to get all the custom nodes we actually use

## Building/Updating ComfyUI Frontend

This kind of sucks.  Sorry.  The ComfyUI Frontend needs patching to behave nicely as a UI for modifying/running ComfyUI workflows.

## The first time

Install nvm and pnpm if you don't already have them.

    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash
    source ~/.bashrc

    nvm install
    nvm use

    npm install -g pnpm@10

## Update the frontend

    git checkout main
    git pull
    # as I'm writing this frontend 1.48+ are bugged, this will give you a detached head at the
    # selected version, good enough for our purposes.
    git checkout v1.47.7

(obsolete, sry)
open up src/platform/workflow/persistence/composables/useWorkflowPersistenceV2.ts and add this right after
  const loadPreviousWorkflowFromStorage = async () => {
    const sessionPath = tabState.getActivePath()

    // If we got a ?workflow= parameter we want to load that.
    if (route.query.workflow && typeof route.query.workflow === 'string') {
      console.warn('Loading workflow from query parameter:', route.query.workflow)
      try {
        const workflow_filename = "workflows/" + route.query.workflow
        const workflow = workflowStore.getWorkflowByPath(workflow_filename)

        if (workflow) {
          await useWorkflowService().openWorkflow(workflow)
          return true
        }
        
      } catch (err) {
        console.error('Error loading workflow from query parameter', err)
      }
    } else {
      console.warn('No workflow query parameter found, loading previous workflow from storage if present')
    }

Before you can build, you'll need to:

    pnpm install

Apologies, I'm sure there is a better way to do this.

## Then build the frontend

    pnpm build

That will give you a fresh build of the UI in ComfyUI_frontend/dist/

If you want to modify the typescript for the frontend, this is the command to rebuild it.  

You will also want to `docker compose stop` and `docker compose up` again so you get the new code.

## Next stop

    docker compose up

Then:

Open this link: [http://localhost:8080/](http://localhost:8080/)

[UI Walkthrough](walkthrough.md)

[Release Notes](release_notes_001.md)
