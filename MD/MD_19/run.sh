#!/usr/bin/env bash
set -euo pipefail

yhrun -n 1 -p cps6 gmx_mpi pdb2gmx -f rank_019.pdb -o step1.gro -p topol.top -ff amber99sb-ildn -water tip3p -ignh
yhrun -n 1 -p cps6 gmx_mpi editconf -f step1.gro -o step2.gro -d 1.2 -bt cubic
yhrun -n 1 -p cps6 gmx_mpi solvate -cp step2.gro -cs spc216.gro -p topol.top -o step3.gro
touch tmp.mdp
yhrun -n 1 -p cps6 gmx_mpi grompp -f tmp.mdp -c step3.gro -p topol.top -o ion.tpr
printf '%s\n' 13 | yhrun -n 1 -p cps6 gmx_mpi genion -s ion.tpr -o step4.gro -neutral -conc 0.15 -p topol.top
yhrun -n 1 -p cps6 gmx_mpi grompp -f min.mdp -c step4.gro -p topol.top -o min.tpr
yhrun -n 16 -p cps6 gmx_mpi mdrun -v -deffnm min -ntomp 1
yhrun -n 1 -p cps6 gmx_mpi grompp -f nvt.mdp -c min.gro -p topol.top -o nvt.tpr -r min.gro
yhrun -n 16 -p cps6 gmx_mpi mdrun -v -deffnm nvt -ntomp 1
yhrun -n 1 -p cps6 gmx_mpi grompp -f npt.mdp -c nvt.gro -p topol.top -o npt.tpr -r nvt.gro -t nvt.cpt
yhrun -n 16 -p cps6 gmx_mpi mdrun -v -deffnm npt -ntomp 1
yhrun -n 1 -p cps6 gmx_mpi grompp -f pro.mdp -c npt.gro -p topol.top -o pro.tpr -t npt.cpt
yhrun -n 16 -p cps6 gmx_mpi mdrun -v -deffnm pro -ntomp 1
