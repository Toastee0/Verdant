"""VerdantSim reference simulator.

Python implementation of the staged Jacobi auction framework described in
`wiki/framework.md`. This is the correctness oracle for the eventual CUDA
port — both emit the same schema-v1 JSON, both verified by the same checker.

See `wiki/pipeline.md` for the stage-by-stage model and `PLAN.md` for the
roadmap.
"""
