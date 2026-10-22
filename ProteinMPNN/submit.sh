export CUDA_VISIBLE_DEVICES=""

export OMP_NUM_THREADS=16
export MKL_NUM_THREADS=16
export OPENBLAS_NUM_THREADS=16




folder_with_pdbs="/home/gengchongli/Data/hotspot-5-28/rf_outputs_pdbs"

output_dir="/home/gengchongli/Data/hotspot-5-28/mpnn_result"
if [ ! -d $output_dir ]
then
    mkdir -p $output_dir
fi


path_for_parsed_chains=$output_dir"/parsed_pdbs.jsonl"
path_for_assigned_chains=$output_dir"/assigned_pdbs.jsonl"
path_for_fixed_positions=$output_dir"/fixed_pdbs.jsonl"
chains_to_design="B"
#The first amino acid in the chain corresponds to 1 and not PDB residues index for now.
design_only_positions="20 21 22 23 56 57 58 59 60 61 62 63 64 65 66 67 68 70 71 72 73 74 75 76 77 78 79 80 81 82 83 85 86 87 88 89 90 91" #design only these residues; use flag --specify_non_fixed

python ../helper_scripts/parse_multiple_chains.py --input_path=$folder_with_pdbs --output_path=$path_for_parsed_chains

python ../helper_scripts/assign_fixed_chains.py --input_path=$path_for_parsed_chains --output_path=$path_for_assigned_chains --chain_list "$chains_to_design"

python ../helper_scripts/make_fixed_positions_dict.py --input_path=$path_for_parsed_chains --output_path=$path_for_fixed_positions --chain_list "$chains_to_design" --position_list "$design_only_positions" --specify_non_fixed

python ../protein_mpnn_run.py \
        --jsonl_path $path_for_parsed_chains \
        --chain_id_jsonl $path_for_assigned_chains \
        --fixed_positions_jsonl $path_for_fixed_positions \
        --out_folder $output_dir \
        --num_seq_per_target 8 \
        --sampling_temp "0.1" \
        --seed 37 \
        --batch_size 1 \
        --use_soluble_model
