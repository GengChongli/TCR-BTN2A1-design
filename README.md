# Computational Design of Vγ9-BTN2A1 Complexes

## Overview

This repository contains the data associated with the paper "De Novo γδ TCR Design with Higher Affinity to BTN2A1 Using Deep Learning and Molecular Dynamics Simulations".The study focuses on designing the Vγ9 region of the γδ T-cell receptor (TCR) to enhance its binding affinity to butyrophilin 2A1 (BTN2A1).
The computational workflow integrates RFdiffusion for backbone generation, ProteinMPNN for sequence design, ColabFold for structure prediction, HADDOCK3 for protein-protein docking, and molecular dynamics (MD) simulations for structural stability assessment. Binding free energies were further evaluated using the Molecular Mechanics/Poisson–Boltzmann Surface Area (MM/PBSA) method.

## Repository Contents

This repository contains the following computational data:

| Directory | Description |
|---|---|
| `RFdiffusion/` | Backbone structures generated using RFdiffusion3. |
| `ProteinMPNN/` | Designed sequences generated using ProteinMPNN. |
| `ColabFold/` | Protein structures predicted using ColabFold. |
| `HADDOCK3/` | Results from two rounds of protein-protein docking using HADDOCK3. |
| `MD/` | MD simulation parameter files and MM/PBSA calculation results for 100 selected complexes. |

## Molecular Dynamics Simulation Data

The MD trajectories for the 100 selected Vγ9–BTN2A1 complexes are deposited in Zenodo.

The trajectory files are labeled from MD_1 to MD_100 according to their corresponding complexes.

**Zenodo DOI:** [https://doi.org/10.5281/zenodo.23559547]

## Data Availability

The designed backbone structures, sequences, predicted structures, docked complex structures, MD simulation parameters, and MM/PBSA calculation results are available in this repository. The MD trajectories for all 100 selected Vγ9-BTN2A1 complexes are deposited in Zenodo.


