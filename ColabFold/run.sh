
export CUDA_CACHE_PATH="$HOME/.nv/ComputeCache"
export CUDA_CACHE_DISABLE=0

export JAX_COMPILATION_CACHE_DIR="$HOME/.jax_cache"


export CUDA_VISIBLE_DEVICES=1

export XLA_FLAGS="--xla_gpu_cuda_data_dir=$CONDA_PREFIX"
export JAX_PLATFORMS="cuda,cpu"
export JAX_SKIP_TPU_INIT=true
export TF_ENABLE_ONEDNN_OPTS=0


export XLA_PYTHON_CLIENT_PREALLOCATE=false
#export XLA_PYTHON_CLIENT_ALLOCATOR=platform


INPUT_DIR="./fasta_for_af"
OUTPUT_DIR="./af2-output"

mkdir -p "$OUTPUT_DIR"

colabfold_batch "$INPUT_DIR" "$OUTPUT_DIR" --data /opt/colabfold_weights/
