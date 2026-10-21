FROM ubuntu:26.04 AS system-deps

# and blessed sanity returns that all might be simple and well in the land of
# docker.
ENV PIP_BREAK_SYSTEM_PACKAGES=1

RUN apt update && apt install -y \
    build-essential \
    ffmpeg \
    git \
    gunicorn \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    python3-setuptools \
    python3-wheel \
    && ln -s /usr/bin/python3 /usr/bin/python \
    && apt clean
   
ENV BLIS_ARCH="generic"
RUN pip install blis --no-binary blis

# Keep the large TeX installation separate from the stable base packages.
FROM system-deps AS texlive
RUN apt update && apt install -y texlive-full \
    && apt clean

# Application dependencies inherit both the stable base and TeX Live.
FROM texlive AS app

# we need typing-extensions 4.16, apt gives us 4.15
# this uninstall is brutal and is going to break stuff.

RUN apt update && apt remove -y python3-typing-extensions && apt clean
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt --ignore-installed
# and a model for spacy, which we're using for linguistics, which is
# unnecessarily difficult to figure out how to get what you want.. but I just
# thought of how to do it, so here we are.  To do: when this obviously heavily
# versioned solution stops working, because you know it will, this is the fix.
# write a little python script that uses spacey to download the model you want,
# and then run that script here.  moron, isn't there a 'spacy download' cli thing?
# 
# oh yeah, that is nice.
# comment retained indefinitely.  moment of humility.
#
# (old) RUN pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0.tar.gz

# (new)
RUN spacy download en_core_web_sm

COPY . /app/
# imagemagic defaults are too restrictive for our needs, so we override them
# with a custom policy.xml
COPY image_magic_policy.xml /etc/ImageMagick-7/policy.xml

WORKDIR /app
ENV FLASK_RUN_PORT=8080
ENTRYPOINT [ "flask", "--app", "artifact_editor.app:create_app()", "run", "--host=0.0.0.0"]
