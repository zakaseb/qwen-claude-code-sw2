docker run -ti --rm --name qwen-claude-code --network=none --gpus all \
  -e LLAMA_ARG_N_GPU_LAYERS=30 \
  -e LLAMA_ARG_CTX_SIZE=32768 \
  -e LLAMA_ARG_N_PREDICT=32768 \
  -v $PWD/models:/home/developer/models \
  -v $PWD/workspace:/home/developer/workspace \
  qwen-claude-code:llama.cpp
