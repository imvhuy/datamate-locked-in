# Follows the reference Dockerfile of competition/docker_guideline.txt (section 9).
# Build:  docker build --platform linux/amd64 -t zaic2026_datamate_<team>:final .
#
# Qwen3.5-9B needs vLLM >= 0.17, which the recommended base (vLLM 0.10.1.1) lacks. The base stays: its CUDA 12.8
# runtime is what the organizers tested on the RTX 5090 hosts (R570+ drivers). vLLM 0.17.1 goes into its own venv,
# installed exactly as on Colab, where it ran with torch 2.10.0+cu128; the build stops if torch is not a CUDA 12.8 build.
FROM vllm/vllm-openai:v0.10.1.1
ENTRYPOINT []
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PIP_NO_CACHE_DIR=1 UV_NO_CACHE=1 \n    UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_PREFERENCE=only-managed
WORKDIR /code
COPY requirements.txt /code/requirements.txt
RUN pip install uv \
 && uv venv -p 3.12 /opt/venv \
 && uv pip install -p /opt/venv/bin/python -r /code/requirements.txt \
 && /opt/venv/bin/python -c "import torch, vllm; assert torch.version.cuda.startswith('12.8'), torch.version.cuda; print('vllm', vllm.__version__, 'torch', torch.__version__)"
# Weights first: they change least, so code edits do not invalidate this layer.
COPY models/ /code/models/
COPY data/train/ /code/data/train/
COPY training_code/ /code/training_code/
COPY scripts/ /code/scripts/
COPY predict.sh README.md /code/
COPY src/ /code/src/
RUN chmod +x /code/predict.sh
# Paths, sizes and SHA-256 of the training data, generated data and weights as they are in the image (guideline 8).
RUN /opt/venv/bin/python -X utf8 -m training_code.manifest
CMD ["bash", "/code/predict.sh"]
