#!/usr/bin/env bash
set -euo pipefail

yhrun -n 16 -p cps6 gmx_MMPBSA -O -i mmpbsa.in -cs ../pro.tpr -ct ../protein_fit.xtc -ci ../index.ndx -cg 17 18 -cp ../topol.top -o FINAL_RESULTS_MMPBSA.dat -eo FINAL_RESULTS_MMPBSA.csv -nogui
