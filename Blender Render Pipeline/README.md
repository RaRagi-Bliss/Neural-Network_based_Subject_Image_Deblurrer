# Instructions for running the Blender Pipeline

This rendering pipeline has been written for Blender 4.2, and Python 3.11. These tools may work with newer versions of
Python or Blender, however these have not been tested and are not guaranteed to work.

This pipeline is runnable on Linux, MacOS, and Windows.

### First set of requirements:
The following needs to be installed
- Blender Version 4.2, installed in the default filepath, or Via Steam
- Python 3.11
- Cloning this Repository (Duh)

### Second set of requirements:
- If on Windows, add the root folder of Blender's executable into the Windows environment table's PATH
- Create a Python virtual environment specifically for the Blender Pipeline, and with the activated venv, install the
  necessary pip packages with the supplied `requirements.txt` within this `Blender Render Pipeline` directory.
  - If on Windows, all terminal interactions should be done within a BASH based terminal, or via WSL. Git Bash will be
  installed if you've already set up Git on your Windows Machine.

### Third set of requirements:
- TODO: Add instructions on adding your own assets to be included in the rendering pipeline

### Fourth set of Requirements:
Run the rendering pipeline
- Enter `./run_pipeline.sh` into the terminal, followed by `--count X`, replacing X with the number of
generated reference + ground truth sets you would like.
- E.g. `./run_pipeline.sh --count 50` to run the pipeline and generate 50 image sets
