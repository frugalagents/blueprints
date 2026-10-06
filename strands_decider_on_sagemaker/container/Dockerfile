FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# gcc: triton compiles a small launcher stub at first kernel use
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# CUDA 12.6 build of torch: the default PyPI wheel is CUDA 13, which needs driver >= 580
# (newer than SageMaker's standard GPU hosts). 12.x runs on older drivers via minor-version compat.
RUN pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cu126

# Versions match the local environment that produced tests/golden.json
RUN pip install \
    strands-decider==0.1.0 \
    transformers==5.18.0 \
    peft==0.21.2 \
    flash-linear-attention \
    && python -c "import torch; print('torch', torch.__version__, 'cuda', torch.version.cuda)"

COPY app.py /opt/program/app.py
COPY serve /usr/local/bin/serve
RUN chmod +x /usr/local/bin/serve

WORKDIR /opt/program
