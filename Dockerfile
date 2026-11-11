# Follows the reference Dockerfile of competition/docker_guideline.txt (section 9).
# Build:  docker build --platform linux/amd64 -t zaic2026_datamate_<team>:final .
FROM vllm/vllm-openai:v0.10.1.1
ENTRYPOINT []
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PIP_NO_CACHE_DIR=1 NCCL_P2P_DISABLE=1
WORKDIR /code
COPY requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir -r /code/requirements.txt
# Weights first: they change least, so code edits do not invalidate this layer.
COPY models/ /code/models/
COPY training_code/ /code/training_code/
COPY scripts/ /code/scripts/
COPY predict.sh README.md /code/
COPY src/ /code/src/
RUN chmod +x /code/predict.sh
CMD ["bash", "/code/predict.sh"]
