[![Link to Youtube Example](assets/example_result.png)](https://youtu.be/UguuQuCYBh0?si=xLMxq_jzNHJccfpF)

Requirements
------------
* Linux 
* Docker
* Docker Compose
* NVidia 3060 12GB or better.

Windows or OSX might work.  I have not tried.

Purpose
-------

This is a local browser based UI environment for turning an plain text book into a read-along, spoken out-loud video with contextually relevant images and video.

The text of the book is rendered as crisp, precisely typeset L<span style="text-transform: uppercase; font-size: 0.85em; margin-left: -0.3em; vertical-align: 0.5ex; margin-right: -0.15em;">a</span>T<span style="text-transform: uppercase; font-size: 0.85em; margin-left: -0.1667em; vertical-align: -0.5ex; margin-right: -0.125em;">e</span>X.


The process of getting from the text of a story to a full video is interactive.  This isn't a one shot upload text and watch a movie.  It is intended to support iterative improvement.  The longer term plan is to enable collaboration and community driven improvements.

The end goal is to produce high quality videos that are absolutely faithful to the source material.

Preface
-------

I know the installation process is miserable.  My plan is to not tell anyone about this project until I've made the install significantly nicer, bare minimum.  The program itself is also riddled with bugs, misfeatures and very questionably choices.

Despite the heavy AI angle, I'm not into vibe coding.  It gets rid of what I consider to be the fun part.  I am not a purist, I still use command completion level AI assists when it suggests what I'm about to do anyway.

The clever duckling among you may wonder where the git history is.  I'm cutting a clean slate because the history includes API keys and a lot of churn.  I've been building this for years, off and on.

There aren't any tests.  Which explains some of the massive swarm of bugs.


Initialization
--------------

pull this repo, cd into it, then:

    docker compose build

This will take a hot second (on my local it takes 925 seconds, but that's cheating because I already have the base images like ubuntu 26.04 pulled).  The Latex installation (texlive-full) is the longest pole for me with bandwidth as my bottleneck, though the blis wheel compile isn't exactly snapy either.

This will also consume a rather significant amount of disk wherever you store docker images to the tune of:

  24.2GB for ComfyUI
  15.7GB for Artifactserver
  (and a few hundred MB for nginx, redis and glances)

## ComfyUI

Install git clone of:

    ComfyUI to ./comfy/ComfyUI
    ComfyUI_frontend to ./comfy/ConfyUI_frontend
    ComfyUI-Docker to ./comfy/ComfyUI-Docker

## Models

* TODO: Helper to get all the models ComfyUI needs
* TODO: Helper to get all the custom nodes we actually use

# Building/Updating ComfyUI Frontend

This kind of sucks.  Sorry.


## The first time
Install nvm and pnpm if you don't already have them.

curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash
source ~/.bashrc

nvm install 25
nvm use

npm install -g pnpm@10

## To update the frontend:

    git checkout main
    git pull
    # as I'm writing this frontend 1.48+ are bugged, this will give you a detached head at the
    # selected version, good enough for our purposes.
    git checkout v1.47.7

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

## Then build the frontend:

    pnpm build

That will give you a fresh build of the UI in ComfyUI_frontend/dist/

If you want to modify the typescript for the frontend, this is the command to rebuild it.  You will want to stop the docker compose and `docker compose up` again so you get your new code.

## Next stop:

    docker compose up

Then:

http://localhost:8080/