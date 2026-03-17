#!/bin/bash
# Run with network=host so the C-to-SysML web UI is accessible at http://localhost:8080
# Data stays local; no outbound connections from the container.
echo "Starting container. Once ready, open http://localhost:8080 in your browser."
docker run -ti --rm --name qwen-claude-code --network=host --gpus all \
  -e LLAMA_ARG_N_GPU_LAYERS=30 \
  -e LLAMA_ARG_CTX_SIZE=32768 \
  -e LLAMA_ARG_N_PREDICT=32768 \
  -v $PWD/models:/home/developer/models \
  -v $PWD/workspace:/home/developer/workspace \
  qwen-claude-code:llama.cpp
