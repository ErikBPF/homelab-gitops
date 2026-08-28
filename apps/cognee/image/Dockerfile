FROM docker.io/cognee/cognee:1.4.2@sha256:680cd87732430cf5e52b1ff58ae7ec2ed6ed71a9a32a9e77b8a0627f425cc4c3

ENV HF_HOME=/app/hf-cache

RUN /app/.venv/bin/python -m ensurepip \
 && /app/.venv/bin/python -m pip install --no-deps \
      'https://files.pythonhosted.org/packages/07/1c/d193c37bd835e8fd99dcb440e8a3a01a1fdbc032363ea51b8a40ae627365/cognee_community_hybrid_adapter_falkor-0.4.0-py3-none-any.whl#sha256=99324b2ea8373968053b2d33e82c10730e2d480205e51938b0f97bd222e6c62b' \
      'https://files.pythonhosted.org/packages/bf/43/41967fad8b625e2b319e03a1c8897535dd1e05723a051342422b48b870da/falkordb-1.7.1-py3-none-any.whl#sha256=0e62d535edd5abf7b6d35e2493fba71118734313436ff803fcd5d1b20e9e2194' \
      'https://files.pythonhosted.org/packages/66/9d/c5731f6e3608663d4d3656fd8d3aecee8b509c3082818f5a13eae925baea/redis-8.1.0-py3-none-any.whl#sha256=a4fe1aac3d3b3cc791d4b3d5931c5a956045dc951ee74d1c913ee3ac4d2ee9fb' \
 && /app/.venv/bin/python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('BAAI/bge-m3', revision='5617a9f61b028005a4858fdac845db406aefb181')" \
 && /app/.venv/bin/python -m pip uninstall --yes pip

COPY sitecustomize.py /app/sitecustomize.py

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

RUN HOME=/tmp/cognee-image-check /app/.venv/bin/python -c "import sitecustomize; from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('BAAI/bge-m3', revision='5617a9f61b028005a4858fdac845db406aefb181', local_files_only=True)" \
 && rm -rf /tmp/cognee-image-check
