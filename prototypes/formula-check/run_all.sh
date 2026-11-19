#!/bin/zsh
# PROTOTYPE — throwaway (articulate#37). Guide/exam calls for the formula-check set, one model per arg.
# Usage: zsh run_all.sh gemini-3.6-flash   (writes out/<stem>.<model>.json)
here=${0:A:h}
model=$1
export GEMINI_API_KEY=$(security find-generic-password -s articulate-gemini -w)
export OUT_DIR=$here/out
mkdir -p $OUT_DIR
cd $here/../extract-schema
python3 run_exam.py $here/docs/phy203-test4.pdf courses/phy-203.json $model
python3 run_exam.py $here/docs/mat305-t3-review.pdf courses/mat-305.json $model
python3 run_exam.py $here/docs/mat305-t3-solutions.pdf courses/mat-305.json $model
