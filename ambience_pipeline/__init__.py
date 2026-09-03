"""Middle Watch Ambience — long-form soundscapes with looping visuals.

Design: docs/ambience/pipeline-design.md

Built so far: `synth`, the procedural audio module and its audition CLI. It has
no bundle, no ledger and no credentials, and is meant to be judged by ear:

    python3 -m ambience_pipeline.synth list
    python3 -m ambience_pipeline.synth rain --seconds 60 --out rain.wav
"""
