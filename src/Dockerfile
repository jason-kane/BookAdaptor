FROM ubuntu:26.04

RUN apt update && apt install -y \
    build-essential \
    ffmpeg \
    git \
    gunicorn \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    texlive-full \
    && ln -s /usr/bin/python3 /usr/bin/python \
    && apt clean
   
ENV BLIS_ARCH="generic"
RUN pip install --break-system-packages blis --no-binary blis

# lighter dependencies
RUN apt update && apt install -y \
    python3-setuptools \
    python3-wheel \
    && apt clean

COPY requirements.txt /app/requirements.txt
RUN pip install --break-system-packages -r /app/requirements.txt
# RUN python3.14 -m spacy download en_core_web_sm --break-system-packages
RUN pip install --break-system-packages https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0.tar.gz

COPY . /app/
COPY image_magic_policy.xml /etc/ImageMagick-7/policy.xml

WORKDIR /app
ENV FLASK_RUN_PORT=8080
ENTRYPOINT [ "flask", "--app", "artifact_editor.app:create_app()", "run", "--host=0.0.0.0"]
