"""FastAPI front-end for the Nova-3 Medical transcription core.

Consumes `nova/` in-process (build_options, transcribe_batch, the response walkers);
holds no transcription logic of its own so it cannot drift from the Streamlit UI.
"""
